"""Small, isolated execution service. Deploy this module in the restricted sandbox container."""

from __future__ import annotations

import asyncio
import base64
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
MAX_OUTPUT_FILES = 30
TIMEOUT_SECONDS = 30
WORKSPACE_TTL_SECONDS = 3600
# The service has one event loop. Serialize the entire stage/run/collect operation.
_workspace_locks = weakref.WeakValueDictionary()


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


class ReadRequest(BaseModel):
    workspace_id: str
    path: str


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
    cutoff = time.time() - WORKSPACE_TTL_SECONDS
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

    resource.setrlimit(resource.RLIMIT_CPU, (timeout_seconds, timeout_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


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


@app.post('/run', dependencies=[Depends(_authorize)])
async def run_workspace(request: RunRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        return await _run_workspace(root, request)


async def _run_workspace(root: Path, request: RunRequest) -> dict:
    # Cleanup only while the workspace lock is held, but expire this session too.
    if root.is_dir() and root.stat().st_mtime < time.time() - WORKSPACE_TTL_SECONDS:
        _remove_tree(root)
        _metadata_path(root).unlink(missing_ok=True)
    _cleanup_workspaces(root.parent)
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
    if len(request.inputs) > 30:
        raise HTTPException(413, 'Too many input files')
    decoded: dict[str, bytes] = {}
    total = 0
    for name, encoded in request.inputs.items():
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
    if set(request.input_revisions) != set(decoded) or any(
        not ((type(revision) is int and revision >= 1)
             or (isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{64}', revision)))
        for revision in request.input_revisions.values()
    ):
        raise HTTPException(400, 'Input revisions must match input files')
    if any(isinstance(value, str) and hashlib.sha256(decoded[path]).hexdigest() != value
           for path, value in request.input_revisions.items()):
        raise HTTPException(400, 'Workspace input checksum mismatch')
    metadata = _metadata(root)
    previous_revisions = metadata.get('input_revisions', {})
    if root.exists() and (not metadata or any(
        path not in decoded or (path.startswith('inputs/project/') and request.input_revisions[path] != revision)
        for path, revision in previous_revisions.items()
    )):
        # Scratch files may be derived from any previous input. Drop them when scope narrows.
        _remove_tree(root)
        metadata = {}
        previous_revisions = {}
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    input_dir = root / 'inputs'
    _reset_directory(input_dir)
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
    for name, content in decoded.items():
        target = root.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise HTTPException(400, 'Invalid input path')
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
    metadata['input_revisions'] = request.input_revisions
    _save_metadata(root, metadata)
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
    input_paths = sorted(decoded)
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


@app.post('/read', dependencies=[Depends(_authorize)])
async def read_output(request: ReadRequest) -> dict:
    root = _workspace(request.workspace_id)
    async with _workspace_lock(root):
        return _read_output(root, request.path)


def _read_output(root, path):
    _cleanup_workspaces(root.parent)
    path = _relative_path(path, 'outputs')
    if not root.exists() or root.stat().st_mtime < time.time() - WORKSPACE_TTL_SECONDS:
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
