"""Small, isolated execution service. Deploy this module in the restricted sandbox container."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
import tempfile
import weakref
from collections import Counter
from functools import partial
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
SKILLS = {'docx', 'xlsx', 'pptx', 'pdf'}
MAX_INPUT_BYTES = 40 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_READ_BYTES = 4 * 1024 * 1024
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_FILES = 30
TIMEOUT_SECONDS = 30
DEFAULT_WORKSPACE_TTL_SECONDS = 86_400
DEFAULT_MAX_OPEN_FILES = 512
WORKSPACE_TTL_SECONDS = int(os.getenv('AGENT_RUNTIME_SANDBOX_WORKSPACE_TTL_SECONDS', str(DEFAULT_WORKSPACE_TTL_SECONDS)))
IMAGE_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
}
# The service has one event loop. Serialize the entire stage/run/collect operation.
_workspace_locks = weakref.WeakValueDictionary()


def _workspace_ttl_seconds() -> int:
    raw = os.getenv('AGENT_RUNTIME_SANDBOX_WORKSPACE_TTL_SECONDS')
    if raw is not None:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return WORKSPACE_TTL_SECONDS


def _max_open_files() -> int:
    raw = os.getenv('AGENT_RUNTIME_SANDBOX_MAX_OPEN_FILES')
    if raw is not None:
        try:
            value = int(raw)
            if value >= 64:
                return value
        except ValueError:
            pass
    return DEFAULT_MAX_OPEN_FILES


def _workspace_lock(root):
    return _workspace_locks.setdefault(root, asyncio.Lock())


class RunRequest(BaseModel):
    workspace_id: str
    timeout_seconds: int = Field(default=TIMEOUT_SECONDS, ge=1, le=300)
    language: str | None = None
    code: str | None = None
    skill: str | None = None
    script: str | None = None
    args: list[str] = Field(default_factory=list)
    inputs: dict[str, str] = Field(default_factory=dict)
    input_revisions: dict[str, int | str] = Field(default_factory=dict)


class SyncRequest(BaseModel):
    workspace_id: str
    input_revisions: dict[str, int | str] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)


class ReadRequest(BaseModel):
    workspace_id: str
    path: str


class FsListRequest(BaseModel):
    workspace_id: str
    path: str = '.'
    max_depth: int = Field(default=3, ge=1, le=10)
    limit: int = Field(default=200, ge=1, le=500)


class FsReadRequest(BaseModel):
    workspace_id: str
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=200, ge=1)
    max_chars: int = Field(default=20_000, ge=1, le=50_000)
    as_image: bool = False
    raw_text: bool = False


class FsWriteRequest(BaseModel):
    workspace_id: str
    path: str
    content: str
    encoding: str = Field(default='utf-8', pattern='^(utf-8|base64)$')


class FsEditRequest(BaseModel):
    workspace_id: str
    path: str
    target: str
    replacement: str
    replace_all: bool = False


class FsSearchRequest(BaseModel):
    workspace_id: str
    pattern: str
    path: str = 'work'
    glob: str | None = None
    case_insensitive: bool = False
    max_matches: int = Field(default=50, ge=1, le=200)


def _workspace(workspace_id: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{64}', workspace_id):
        raise HTTPException(400, 'Invalid workspace')
    return Path(os.getenv('AGENT_RUNTIME_SANDBOX_WORK_ROOT', '/tmp/agent-runtime-workspaces')) / workspace_id


def _workspace_uid(root: Path) -> int:
    # Stay inside the usual 65536-ID rootless Docker mapping. The broker owns
    # each workspace root, so its group is a durable reservation for this UID.
    low, count = 10000, 50000
    group = root.stat().st_gid
    if low <= group < low + count:
        return group
    occupied = {item.stat().st_gid for item in root.parent.iterdir() if item.is_dir()}
    start = int(root.name[:8], 16)
    for offset in range(count):
        candidate = low + (start + offset) % count
        if candidate not in occupied:
            return candidate
    raise HTTPException(503, 'Sandbox workspace capacity reached')


def _metadata_path(root: Path) -> Path:
    directory = root.parent / '.metadata'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory / f'{root.name}.json'


def _cleanup_workspaces(base: Path) -> None:
    if not base.exists():
        return
    cutoff = time.time() - _workspace_ttl_seconds()
    for item in base.iterdir():
        if item.name == '.metadata' or not re.fullmatch(r'[a-f0-9]{64}', item.name):
            continue
        lock = _workspace_locks.get(item)
        if lock and lock.locked():
            continue
        if item.is_dir() and item.stat().st_mtime < cutoff:
            _remove_tree(item)
            _metadata_path(item).unlink(missing_ok=True)


def _remove_tree(path: Path) -> None:
    def retry_readonly(operation, target, error):
        if os.name != 'nt' or not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IWRITE)
        operation(target)

    shutil.rmtree(path, onexc=retry_readonly)


def _metadata(root: Path) -> dict:
    path = _metadata_path(root)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_metadata(root, metadata):
    path = _metadata_path(root)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as staged:
        json.dump(metadata, staged)
    Path(staged.name).replace(path)


def _relative_path(value: str, prefix: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or '\\' in value or path.is_absolute() or '..' in path.parts
            or not path.parts or path.parts[0] != prefix):
        raise HTTPException(400, 'Invalid path')
    return path


def _reset_directory(path: Path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.exists():
        _remove_tree(path)
    path.mkdir()


def _seed_project(root, inputs, previous_revisions, uid):
    """Start with editable project copies; later invocations preserve edits and deletions."""
    paths = [PurePosixPath(name) for name in inputs if name.startswith('inputs/project/')]
    names = Counter(path.name for path in paths)
    for path in paths:
        # Duplicate basenames remain addressable by upload ID under inputs/project/.
        if str(path) in previous_revisions or names[path.name] != 1:
            continue
        try:
            target = (root / 'work' / path.name).open('xb')
        except FileExistsError:
            continue
        with target:
            target.write(inputs[str(path)])
            if uid is not None:
                os.fchown(target.fileno(), uid, uid)
                os.fchmod(target.fileno(), 0o600)


def _regular_file(root: Path, relative: PurePosixPath) -> Path:
    target = root.joinpath(*relative.parts)
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise HTTPException(404, 'Output not found')
    if target.stat().st_size > MAX_OUTPUT_BYTES:
        raise HTTPException(413, 'Output exceeds 20 MB limit')
    return target


def _limits(timeout_seconds: int) -> None:
    if os.name != 'posix':
        return
    import resource

    nofile = _max_open_files()
    resource.setrlimit(resource.RLIMIT_CPU, (timeout_seconds, timeout_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))


def _child_setup(uid: int | None, install_filter, timeout_seconds: int) -> None:
    _limits(timeout_seconds)
    if uid is not None:
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    if install_filter is not None:
        install_filter()


async def _read_excerpt(stream):
    head, tail = bytearray(), bytearray()
    size = 0
    while block := await stream.read(32_768):
        split = min(len(block), 10_000 - len(head))
        head.extend(block[:split])
        tail.extend(block[split:])
        del tail[:max(0, len(tail) - 10_000)]
        size += len(block)
    if size <= 20_000:
        return bytes(head + tail), False
    marker = b'\n... output truncated ...\n'
    return bytes(head + marker + tail[len(marker):]), True


def _decode_output(output: bytes | str) -> str:
    if isinstance(output, str):
        return output[:20_000]
    return output[:20_000].decode('utf-8', errors='replace')


def _authorize(x_agent_runtime_sandbox_token: str | None = Header(default=None)) -> None:
    expected = os.getenv('AGENT_RUNTIME_SANDBOX_TOKEN')
    if not expected:
        raise HTTPException(503, 'Sandbox token is not configured')
    if not x_agent_runtime_sandbox_token or not secrets.compare_digest(x_agent_runtime_sandbox_token, expected):
        raise HTTPException(401, 'Invalid sandbox token')


def _skill_script(skill: str | None, script: str | None) -> Path:
    if skill not in SKILLS or not script:
        raise HTTPException(400, 'Unknown skill or script')
    relative = _relative_path(script, 'scripts')
    root = Path(__file__).parent / 'skills' / skill
    target = root.joinpath(*relative.parts)
    if target.suffix != '.py' or not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise HTTPException(400, 'Unknown skill or script')
    return target


def _validate_input_revisions(input_revisions: dict[str, int | str]) -> None:
    if len(input_revisions) > 30:
        raise HTTPException(413, 'Too many input files')
    for name, revision in input_revisions.items():
        relative = _relative_path(name, 'inputs')
        if not 2 <= len(relative.parts) <= 4:
            raise HTTPException(400, 'Invalid input path')
        if not (
            (type(revision) is int and revision >= 1)
            or (isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{64}', revision))
        ):
            raise HTTPException(400, 'Input revisions must match input files')


def _decode_inputs(inputs: dict[str, str], input_revisions: dict[str, int | str]) -> dict[str, bytes]:
    if len(inputs) > 30:
        raise HTTPException(413, 'Too many input files')
    decoded: dict[str, bytes] = {}
    total = 0
    for name, encoded in inputs.items():
        relative = _relative_path(name, 'inputs')
        if not 2 <= len(relative.parts) <= 4:
            raise HTTPException(400, 'Invalid input path')
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise HTTPException(400, 'Invalid input content') from exc
        total += len(content)
        if total > MAX_INPUT_BYTES:
            raise HTTPException(413, 'Inputs exceed 40 MB limit')
        decoded[str(relative)] = content
    if not set(decoded).issubset(set(input_revisions)):
        raise HTTPException(400, 'Input revisions must match input files')
    if any(
        isinstance(input_revisions[path], str) and hashlib.sha256(content).hexdigest() != input_revisions[path]
        for path, content in decoded.items()
    ):
        raise HTTPException(400, 'Workspace input checksum mismatch')
    return decoded


def _ensure_workspace_dirs(root: Path) -> int | None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    input_dir = root / 'inputs'
    if input_dir.is_symlink() or (input_dir.exists() and not input_dir.is_dir()):
        input_dir.unlink()
    input_dir.mkdir(exist_ok=True)
    for name in ('outputs', 'work'):
        directory = root / name
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            directory.unlink()
        directory.mkdir(mode=0o700, exist_ok=True)
    os.utime(root, None)
    uid = None
    if os.name == 'posix' and os.geteuid() == 0:
        uid = _workspace_uid(root)
        os.chown(root, 0, uid)
        root.chmod(0o1770)
        os.chown(input_dir, 0, uid)
        input_dir.chmod(0o550)
        for directory in (root / 'outputs', root / 'work'):
            os.chown(directory, uid, uid)
            directory.chmod(0o700)
    return uid


def _existing_input_ok(root: Path, path: str) -> bool:
    target = root.joinpath(*PurePosixPath(path).parts)
    if target.is_symlink() or not target.is_file():
        return False
    return target.resolve().is_relative_to(root.resolve())


def _sync_workspace_inputs(
    root: Path,
    input_revisions: dict[str, int | str],
    inputs: dict[str, str],
    *,
    allow_missing: bool,
) -> tuple[bool, list[str], int | None]:
    ttl = _workspace_ttl_seconds()
    if root.is_dir() and root.stat().st_mtime < time.time() - ttl:
        _remove_tree(root)
        _metadata_path(root).unlink(missing_ok=True)
    _cleanup_workspaces(root.parent)
    _validate_input_revisions(input_revisions)
    decoded = _decode_inputs(inputs, input_revisions)
    metadata = _metadata(root)
    previous_revisions = metadata.get('input_revisions', {})
    if root.exists() and (
        not metadata
        or any(
            path not in input_revisions
            or (path.startswith('inputs/project/') and input_revisions[path] != revision)
            for path, revision in previous_revisions.items()
        )
    ):
        # Scratch files may be derived from any previous input. Drop them when scope narrows.
        _remove_tree(root)
        metadata = {}
        previous_revisions = {}

    missing = [
        path
        for path, revision in sorted(input_revisions.items())
        if path not in decoded
        and (previous_revisions.get(path) != revision or not _existing_input_ok(root, path))
    ]
    if missing:
        if allow_missing:
            return False, missing, None
        raise HTTPException(400, 'Input revisions must match input files')

    # Verify total size of retained + newly uploaded inputs does not exceed MAX_INPUT_BYTES.
    total_bytes = sum(len(content) for content in decoded.values())
    for path in input_revisions:
        if path not in decoded:
            target = root.joinpath(*PurePosixPath(path).parts)
            total_bytes += target.stat().st_size
    if total_bytes > MAX_INPUT_BYTES:
        raise HTTPException(413, 'Inputs exceed 40 MB limit')

    uid = _ensure_workspace_dirs(root)
    input_dir = root / 'inputs'
    if decoded or 'input_revisions' not in metadata or previous_revisions != input_revisions:
        for name, content in decoded.items():
            target = root.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise HTTPException(400, 'Invalid input path')
            if os.name == 'nt' and target.exists():
                target.chmod(0o600)
            target.write_bytes(content)
            target.chmod(0o400)
            if uid is not None:
                os.chown(target, 0, uid)
                target.chmod(0o440)
                for directory in target.relative_to(input_dir).parents:
                    current = input_dir / directory
                    current.chmod(0o550)
                    os.chown(current, 0, uid)
        _seed_project(root, decoded, previous_revisions, uid)
        metadata['input_revisions'] = input_revisions
        _save_metadata(root, metadata)
    return True, [], uid


@app.post('/sync', dependencies=[Depends(_authorize)])
async def sync_workspace(request: SyncRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        synced, missing, _ = _sync_workspace_inputs(
            root,
            request.input_revisions,
            request.inputs,
            allow_missing=True,
        )
        return {
            'synced': synced,
            'missing_paths': missing,
            'input_paths': sorted(request.input_revisions) if synced else [],
        }


@app.post('/run', dependencies=[Depends(_authorize)])
async def run_workspace(request: RunRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        return await _run_workspace(root, request)


async def _run_workspace(root: Path, request: RunRequest) -> dict:
    if request.skill:
        script = _skill_script(request.skill, request.script)
        if len(request.args) > 30 or any(len(arg) > 4096 or '\x00' in arg for arg in request.args):
            raise HTTPException(400, 'Invalid script arguments')
        command = [sys.executable, '-B', str(script), *request.args]
    else:
        if request.language not in {'shell', 'python', 'javascript', 'node'} or not request.code or len(request.code) > 100_000:
            raise HTTPException(400, 'Expected shell, Python or JavaScript code up to 100 KB')
        if request.language == 'shell':
            if sys.platform != 'linux':
                raise HTTPException(503, 'The workspace terminal requires the Linux sandbox service')
            command = ['/bin/sh', str(root / 'agent.sh')]
        elif request.language == 'python':
            command = [sys.executable, '-B', str(root / 'agent.py')]
        else:
            node = shutil.which('node')
            if not node:
                raise HTTPException(503, 'Node is unavailable in sandbox')
            command = [node, str(root / 'agent.js')]
    _, _, uid = _sync_workspace_inputs(
        root,
        request.input_revisions,
        request.inputs,
        allow_missing=False,
    )
    if not request.skill:
        source = Path(command[-1])
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, delete=False) as script_file:
            script_file.write(request.code)
        staged = Path(script_file.name)
        staged.chmod(0o400)
        if uid is not None:
            os.chown(staged, 0, uid)
            staged.chmod(0o440)
        if os.name == 'nt' and source.exists() and not source.is_symlink():
            source.chmod(0o600)
        staged.replace(source)
    environment = {'PATH': os.getenv('PATH', ''), 'HOME': str(root), 'TMPDIR': str(root),
                   'TEMP': str(root), 'TMP': str(root),
                   'NODE_PATH': os.getenv('NODE_PATH', ''),
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
                   'PYTHONIOENCODING': 'utf-8'}
    if os.name == 'nt':
        environment['SYSTEMROOT'] = os.getenv('SYSTEMROOT', '')
        try:
            completed = await asyncio.to_thread(
                subprocess.run, command, cwd=root, env=environment, capture_output=True,
                timeout=request.timeout_seconds + 2, check=False, encoding='utf-8', errors='replace')
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(408, 'Execution timed out') from exc
        return_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        stdout_cut, stderr_cut = len(stdout) > 20_000, len(stderr) > 20_000
    else:
        from .sandbox_process import child_filter

        install_filter = child_filter() if sys.platform == 'linux' else None
        process = await asyncio.create_subprocess_exec(
            *command, cwd=root, env=environment, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, preexec_fn=partial(_child_setup, uid, install_filter, request.timeout_seconds),
            start_new_session=True,
        )
        readers = asyncio.gather(_read_excerpt(process.stdout), _read_excerpt(process.stderr))
        try:
            async with asyncio.timeout(request.timeout_seconds + 2):
                (stdout, stdout_cut), (stderr, stderr_cut) = await asyncio.shield(readers)
                await process.wait()
        except asyncio.TimeoutError as exc:
            raise HTTPException(408, 'Execution timed out') from exc
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            await readers
        return_code = process.returncode
    outputs = []
    if (root / 'outputs').is_symlink():
        raise HTTPException(409, 'Invalid output directory')
    for item in (root / 'outputs').rglob('*'):
        if item.is_symlink():
            item.unlink()
            continue
        if item.is_file() and item.stat().st_size <= MAX_OUTPUT_BYTES:
            outputs.append(item.relative_to(root).as_posix())
            if len(outputs) >= MAX_OUTPUT_FILES:
                break
    input_paths = sorted(request.input_revisions)
    metadata = _metadata(root)
    output_metadata = {p: meta for p, meta in metadata.get('outputs', {}).items() if p in outputs}
    for output_path in outputs:
        target = root.joinpath(*PurePosixPath(output_path).parts)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        previous = output_metadata.get(output_path, {})
        if previous.get('sha256') != digest:
            if return_code:
                output_metadata.pop(output_path, None)
                continue
            output_metadata[output_path] = {
                'sha256': digest,
                'input_paths': input_paths,
                'input_revisions': request.input_revisions,
            }
    metadata['outputs'] = output_metadata
    _save_metadata(root, metadata)
    return {'exit_code': return_code, 'stdout': _decode_output(stdout),
            'stderr': _decode_output(stderr), 'stdout_truncated': stdout_cut, 'stderr_truncated': stderr_cut,
            'outputs': sorted(outputs),
            'input_paths': input_paths}


def _require_active_workspace(root: Path) -> None:
    _cleanup_workspaces(root.parent)
    if not root.exists() or root.stat().st_mtime < time.time() - _workspace_ttl_seconds():
        raise HTTPException(404, 'Workspace expired')
    os.utime(root, None)


def _assert_no_symlink_ancestors(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(400, 'Symlinks are not allowed')
    resolved_root = root.resolve()
    if current.exists() and not current.resolve().is_relative_to(resolved_root):
        raise HTTPException(400, 'Invalid path')
    if not current.parent.resolve().is_relative_to(resolved_root):
        raise HTTPException(400, 'Invalid path')
    return current


def _workspace_relative_path(
    value: str,
    *,
    allowed_roots: tuple[str, ...],
    allow_root_dot: bool = False,
) -> PurePosixPath:
    if not isinstance(value, str) or '\x00' in value or '\\' in value:
        raise HTTPException(400, 'Invalid path')
    stripped = value.strip()
    if allow_root_dot and stripped in {'', '.', './'}:
        return PurePosixPath('.')
    path = PurePosixPath(stripped)
    if not path.parts or path.is_absolute() or '..' in path.parts or path.parts[0] not in allowed_roots:
        raise HTTPException(400, f'Path must be under {"/ or ".join(allowed_roots)}/')
    return path


def _record_output_provenance(root: Path, relative: PurePosixPath, raw: bytes) -> None:
    if relative.parts[0] != 'outputs':
        return
    metadata = _metadata(root)
    input_revisions = metadata.get('input_revisions', {})
    outputs = metadata.setdefault('outputs', {})
    outputs[relative.as_posix()] = {
        'sha256': hashlib.sha256(raw).hexdigest(),
        'input_paths': sorted(input_revisions),
        'input_revisions': input_revisions,
    }
    _save_metadata(root, metadata)


def _write_workspace_bytes(root: Path, relative: PurePosixPath, raw: bytes) -> tuple[str, int]:
    if len(raw) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, 'File exceeds 20 MB limit')
    uid = _workspace_uid(root) if (os.name == 'posix' and os.geteuid() == 0) else None
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise HTTPException(400, 'Invalid parent directory')
        if not current.exists():
            current.mkdir(mode=0o700)
            if uid is not None:
                os.chown(current, uid, uid)
    target = _assert_no_symlink_ancestors(root, relative)
    if target.exists() and target.is_dir():
        raise HTTPException(400, 'Cannot overwrite a directory')
    with tempfile.NamedTemporaryFile(mode='wb', dir=target.parent, delete=False) as staged_file:
        staged_file.write(raw)
        staged = Path(staged_file.name)
    staged.chmod(0o600)
    if uid is not None:
        os.chown(staged, uid, uid)
    if os.name == 'nt' and target.exists():
        target.chmod(0o600)
    staged.replace(target)
    digest = hashlib.sha256(raw).hexdigest()
    _record_output_provenance(root, relative, raw)
    return digest, len(raw)


@app.post('/fs/list', dependencies=[Depends(_authorize)])
async def fs_list(request: FsListRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        _require_active_workspace(root)
        relative = _workspace_relative_path(
            request.path, allowed_roots=('inputs', 'work', 'outputs'), allow_root_dot=True
        )
        entries: list[dict] = []
        truncated = False
        roots_to_walk: list[tuple[PurePosixPath, Path]]
        if relative == PurePosixPath('.'):
            roots_to_walk = [(PurePosixPath(name), root / name) for name in ('inputs', 'work', 'outputs')]
        else:
            target = _assert_no_symlink_ancestors(root, relative)
            if not target.exists():
                raise HTTPException(404, 'Path not found')
            if target.is_file():
                return {
                    'path': relative.as_posix(),
                    'entries': [{'path': relative.as_posix(), 'type': 'file', 'size': target.stat().st_size}],
                    'truncated': False,
                }
            roots_to_walk = [(relative, target)]

        def walk(rel_dir: PurePosixPath, fs_dir: Path, depth: int) -> None:
            nonlocal truncated
            if not fs_dir.is_dir() or fs_dir.is_symlink() or depth > request.max_depth:
                return
            for child in sorted(fs_dir.iterdir(), key=lambda item: item.name):
                if child.is_symlink():
                    continue
                child_rel = rel_dir / child.name
                if len(entries) >= request.limit:
                    truncated = True
                    return
                if child.is_dir():
                    entries.append({'path': child_rel.as_posix(), 'type': 'dir'})
                    if depth < request.max_depth:
                        walk(child_rel, child, depth + 1)
                elif child.is_file():
                    entries.append({'path': child_rel.as_posix(), 'type': 'file', 'size': child.stat().st_size})

        for rel_dir, fs_dir in roots_to_walk:
            if len(entries) >= request.limit:
                truncated = True
                break
            walk(rel_dir, fs_dir, 1)

        return {'path': relative.as_posix(), 'entries': entries, 'truncated': truncated}


@app.post('/fs/read', dependencies=[Depends(_authorize)])
async def fs_read(request: FsReadRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        _require_active_workspace(root)
        relative = _workspace_relative_path(request.path, allowed_roots=('inputs', 'work', 'outputs'))
        target = _assert_no_symlink_ancestors(root, relative)
        if not target.is_file():
            raise HTTPException(404, 'File not found')
        size = target.stat().st_size
        if size > MAX_OUTPUT_BYTES:
            raise HTTPException(413, 'File exceeds 20 MB limit')

        suffix = target.suffix.lower()
        if request.as_image:
            mime_type = IMAGE_MIME_TYPES.get(suffix)
            if not mime_type:
                raise HTTPException(400, 'Image read supports only PNG, JPEG, and WEBP files')
            if size > MAX_IMAGE_READ_BYTES:
                raise HTTPException(413, 'Image exceeds 4 MB limit')
            raw = target.read_bytes()
            return {
                'path': relative.as_posix(),
                'mime_type': mime_type,
                'size': len(raw),
                'content_b64': base64.b64encode(raw).decode(),
            }

        raw = target.read_bytes()
        if b'\x00' in raw[:4096]:
            raise HTTPException(400, 'Binary file cannot be read as text; inspect it with a script in the workspace')
        text = raw.decode('utf-8', errors='replace')
        if request.raw_text:
            truncated = len(text) > request.max_chars
            return {
                'path': relative.as_posix(),
                'size': len(raw),
                'content': text[: request.max_chars],
                'total_chars': len(text),
                'truncated': truncated,
            }
        lines = text.splitlines()
        total_lines = len(lines)
        start = max(1, request.start_line)
        end = min(total_lines, max(start, request.end_line))
        selected = lines[start - 1 : end]
        numbered = [f'{idx}: {line}' for idx, line in enumerate(selected, start=start)]
        joined = '\n'.join(numbered)
        truncated = end < total_lines
        if len(joined) > request.max_chars:
            joined = joined[: request.max_chars]
            truncated = True
        return {
            'path': relative.as_posix(),
            'start_line': start,
            'end_line': end,
            'total_lines': total_lines,
            'content': joined,
            'truncated': truncated,
        }


@app.post('/fs/write', dependencies=[Depends(_authorize)])
async def fs_write(request: FsWriteRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        _require_active_workspace(root)
        relative = _workspace_relative_path(request.path, allowed_roots=('work', 'outputs'))
        if len(relative.parts) < 2:
            raise HTTPException(400, 'File path must be inside work/ or outputs/')
        if request.encoding == 'base64':
            try:
                raw = base64.b64decode(request.content, validate=True)
            except Exception as exc:
                raise HTTPException(400, 'Invalid base64 content') from exc
        else:
            raw = request.content.encode('utf-8')
        digest, size = _write_workspace_bytes(root, relative, raw)
        return {'path': relative.as_posix(), 'size': size, 'sha256': digest}


@app.post('/fs/edit', dependencies=[Depends(_authorize)])
async def fs_edit(request: FsEditRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        _require_active_workspace(root)
        relative = _workspace_relative_path(request.path, allowed_roots=('work', 'outputs'))
        if len(relative.parts) < 2:
            raise HTTPException(400, 'File path must be inside work/ or outputs/')
        if not request.target:
            raise HTTPException(400, 'target must be non-empty')
        target = _assert_no_symlink_ancestors(root, relative)
        if not target.is_file():
            raise HTTPException(404, 'File not found')
        raw = target.read_bytes()
        if len(raw) > MAX_OUTPUT_BYTES:
            raise HTTPException(413, 'File exceeds 20 MB limit')
        if b'\x00' in raw[:4096]:
            raise HTTPException(400, 'Cannot text-edit a binary file')
        text = raw.decode('utf-8')
        occurrences = text.count(request.target)
        if occurrences == 0:
            raise HTTPException(400, 'Target text not found in file')
        if occurrences > 1 and not request.replace_all:
            raise HTTPException(
                400,
                f'Target text matched {occurrences} times; pass replace_all=True or include more surrounding lines',
            )
        updated = text.replace(request.target, request.replacement) if request.replace_all else text.replace(
            request.target, request.replacement, 1
        )
        replaced = occurrences if request.replace_all else 1
        digest, size = _write_workspace_bytes(root, relative, updated.encode('utf-8'))
        return {'path': relative.as_posix(), 'replacements': replaced, 'size': size, 'sha256': digest}


@app.post('/fs/search', dependencies=[Depends(_authorize)])
async def fs_search(request: FsSearchRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        _require_active_workspace(root)
        if not request.pattern:
            raise HTTPException(400, 'pattern must be non-empty')
        flags = re.IGNORECASE if request.case_insensitive else 0
        try:
            compiled = re.compile(request.pattern, flags)
        except re.error as exc:
            raise HTTPException(400, f'Invalid regex pattern: {exc}') from exc
        relative = _workspace_relative_path(
            request.path, allowed_roots=('inputs', 'work', 'outputs'), allow_root_dot=True
        )
        if relative == PurePosixPath('.'):
            roots = [(PurePosixPath(name), root / name) for name in ('inputs', 'work', 'outputs')]
        else:
            target = _assert_no_symlink_ancestors(root, relative)
            if not target.exists():
                raise HTTPException(404, 'Search path not found')
            roots = [(relative, target)]

        matches: list[dict] = []
        truncated = False
        files_scanned = 0

        def scan_file(rel_path: PurePosixPath, fs_path: Path) -> None:
            nonlocal truncated, files_scanned
            if fs_path.is_symlink() or not fs_path.is_file():
                return
            if request.glob and not fnmatch.fnmatchcase(rel_path.name, request.glob) and not fnmatch.fnmatchcase(
                rel_path.as_posix(), request.glob
            ):
                return
            if fs_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                return
            files_scanned += 1
            raw = fs_path.read_bytes()
            if b'\x00' in raw[:4096]:
                return
            text = raw.decode('utf-8', errors='replace')
            for line_no, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    if len(matches) >= request.max_matches:
                        truncated = True
                        return
                    matches.append({'path': rel_path.as_posix(), 'line': line_no, 'text': line[:400]})

        for rel_root, fs_root in roots:
            if truncated:
                break
            if fs_root.is_file():
                scan_file(rel_root, fs_root)
            elif fs_root.is_dir():
                for dirpath, dirnames, filenames in os.walk(fs_root):
                    dirnames[:] = sorted(
                        d for d in dirnames if not (Path(dirpath) / d).is_symlink()
                    )
                    for fname in sorted(filenames):
                        candidate = Path(dirpath) / fname
                        rel_candidate = rel_root / candidate.relative_to(fs_root).as_posix()
                        scan_file(PurePosixPath(rel_candidate.as_posix()), candidate)
                        if truncated:
                            break
                    if truncated:
                        break

        return {
            'pattern': request.pattern,
            'path': relative.as_posix(),
            'files_scanned': files_scanned,
            'matches': matches,
            'truncated': truncated,
        }


@app.post('/read', dependencies=[Depends(_authorize)])
async def read_output(request: ReadRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        return _read_output(root, request.path)


def _read_output(root, path):
    _cleanup_workspaces(root.parent)
    path = _relative_path(path, 'outputs')
    if not root.exists() or root.stat().st_mtime < time.time() - _workspace_ttl_seconds():
        raise HTTPException(404, 'Workspace expired')
    target = _regular_file(root, path)
    os.utime(root, None)
    provenance = _metadata(root).get('outputs', {}).get(path.as_posix())
    content = target.read_bytes()
    if not provenance or provenance.get('sha256') != hashlib.sha256(content).hexdigest():
        raise HTTPException(409, 'Output provenance is unavailable or has changed')
    return {'content_b64': base64.b64encode(content).decode(), 'size': len(content),
            'input_paths': provenance['input_paths'], 'input_revisions': provenance['input_revisions']}


if __name__ == '__main__':
    import uvicorn

    if sys.platform != 'linux' or os.geteuid() != 0 or not os.statvfs('/').f_flag & os.ST_RDONLY:
        raise SystemExit('Start the sandbox in its read-only Linux container')
    from .sandbox_process import child_filter

    child_filter()
    uvicorn.run(app, host=os.getenv('AGENT_RUNTIME_SANDBOX_HOST', '127.0.0.1'),
                port=int(os.getenv('AGENT_RUNTIME_SANDBOX_PORT', '8765')))
