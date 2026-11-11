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
import stat
import subprocess
import sys
import time
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


class RunRequest(BaseModel):
    workspace_id: str
    language: str | None = None
    code: str | None = None
    skill: str | None = None
    script: str | None = None
    args: list[str] = Field(default_factory=list)
    inputs: dict[str, str] = Field(default_factory=dict)
    input_revisions: dict[str, int] = Field(default_factory=dict)


class ReadRequest(BaseModel):
    workspace_id: str
    path: str


def _workspace(workspace_id: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{64}', workspace_id):
        raise HTTPException(400, 'Invalid workspace')
    return Path(os.getenv('MH_SANDBOX_WORK_ROOT', '/tmp/mh-workspaces')) / workspace_id


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
    except FileNotFoundError:
        return {}


def _relative_path(value: str, prefix: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or '\\' in value or path.is_absolute() or '..' in path.parts
            or not path.parts or path.parts[0] != prefix):
        raise HTTPException(400, 'Invalid path')
    return path


def _regular_file(root: Path, relative: PurePosixPath) -> Path:
    target = root.joinpath(*relative.parts)
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise HTTPException(404, 'Output not found')
    if target.stat().st_size > MAX_OUTPUT_BYTES:
        raise HTTPException(413, 'Output exceeds 20 MB limit')
    return target


def _limits() -> None:
    if os.name != 'posix':
        return
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _child_setup(uid: int | None) -> None:
    _limits()
    if uid is not None:
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)


def _decode_output(output: bytes | str) -> str:
    if isinstance(output, str):
        return output[:20_000]
    return output[:20_000].decode('utf-8', errors='replace')


def _authorize(x_mh_sandbox_token: str | None = Header(default=None)) -> None:
    expected = os.getenv('MH_SANDBOX_TOKEN')
    if not expected:
        raise HTTPException(503, 'Sandbox token is not configured')
    if not x_mh_sandbox_token or not secrets.compare_digest(x_mh_sandbox_token, expected):
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
    _cleanup_workspaces(root.parent)
    if request.skill:
        script = _skill_script(request.skill, request.script)
        if len(request.args) > 30 or any(len(arg) > 4096 or '\x00' in arg for arg in request.args):
            raise HTTPException(400, 'Invalid script arguments')
        command = [sys.executable, '-B', str(script), *request.args]
    else:
        if request.language not in {'python', 'javascript', 'node'} or not request.code or len(request.code) > 100_000:
            raise HTTPException(400, 'Only Python or JavaScript code up to 100 KB is supported')
        if request.language == 'python':
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
        if len(relative.parts) > 4:
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
        not isinstance(revision, int) or revision < 1 for revision in request.input_revisions.values()
    ):
        raise HTTPException(400, 'Input revisions must match input files')
    root.mkdir(parents=True, exist_ok=True)
    input_dir = root / 'inputs'
    if input_dir.is_symlink():
        input_dir.unlink()
    elif input_dir.exists():
        _remove_tree(input_dir)
    input_dir.mkdir()
    (root / 'outputs').mkdir(exist_ok=True)
    os.utime(root, None)
    uid = None
    if os.name == 'posix' and os.geteuid() == 0:
        uid = 10000 + int(request.workspace_id[:8], 16) % 4_000_000_000
        for directory in (root, root / 'inputs', root / 'outputs'):
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
            os.chown(target, uid, uid)
            target.parent.chmod(0o700)
            os.chown(target.parent, uid, uid)
    if not request.skill:
        source = root / ('agent.py' if request.language == 'python' else 'agent.js')
        if source.exists() and os.name == 'nt':
            source.chmod(0o600)
        source.write_text(request.code, encoding='utf-8')
        source.chmod(0o400)
        if uid is not None:
            os.chown(source, uid, uid)
    environment = {'PATH': os.getenv('PATH', ''), 'HOME': str(root), 'TMPDIR': str(root),
                   'TEMP': str(root), 'TMP': str(root),
                   'NODE_PATH': os.getenv('NODE_PATH', ''),
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
                   'PYTHONIOENCODING': 'utf-8'}
    if os.name == 'nt':
        environment['SYSTEMROOT'] = os.getenv('SYSTEMROOT', '')
    if os.name == 'nt':
        try:
            completed = await asyncio.to_thread(
                subprocess.run, command, cwd=root, env=environment, capture_output=True,
                timeout=TIMEOUT_SECONDS + 2, check=False, encoding='utf-8', errors='replace')
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(408, 'Execution timed out') from exc
        return_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    else:
        try:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=root, env=environment, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, preexec_fn=partial(_child_setup, uid),
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT_SECONDS + 2)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise HTTPException(408, 'Execution timed out') from exc
        return_code = process.returncode
    outputs = []
    for item in sorted((root / 'outputs').rglob('*')):
        if item.is_symlink():
            item.unlink()
            continue
        if item.is_file() and item.stat().st_size <= MAX_OUTPUT_BYTES:
            outputs.append(item.relative_to(root).as_posix())
            if len(outputs) >= MAX_OUTPUT_FILES:
                break
    input_paths = sorted(decoded)
    metadata = _metadata(root)
    output_metadata = metadata.get('outputs', {})
    for output_path in outputs:
        target = root.joinpath(*PurePosixPath(output_path).parts)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        previous = output_metadata.get(output_path, {})
        if previous.get('sha256') != digest:
            output_metadata[output_path] = {
                'sha256': digest,
                'input_paths': input_paths,
                'input_revisions': request.input_revisions,
            }
    _metadata_path(root).write_text(json.dumps({'outputs': output_metadata}), encoding='utf-8')
    return {'exit_code': return_code, 'stdout': _decode_output(stdout),
            'stderr': _decode_output(stderr), 'outputs': outputs,
            'input_paths': input_paths}


@app.post('/read', dependencies=[Depends(_authorize)])
async def read_output(request: ReadRequest) -> dict:
    root = _workspace(request.workspace_id)
    _cleanup_workspaces(root.parent)
    path = _relative_path(request.path, 'outputs')
    target = _regular_file(root, path)
    os.utime(root, None)
    provenance = _metadata(root).get('outputs', {}).get(path.as_posix())
    if not provenance or provenance.get('sha256') != hashlib.sha256(target.read_bytes()).hexdigest():
        raise HTTPException(409, 'Output provenance is unavailable or has changed')
    return {'content_b64': base64.b64encode(target.read_bytes()).decode(), 'size': target.stat().st_size,
            'input_paths': provenance['input_paths'], 'input_revisions': provenance['input_revisions']}


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host=os.getenv('MH_SANDBOX_HOST', '127.0.0.1'),
                port=int(os.getenv('MH_SANDBOX_PORT', '8765')))
