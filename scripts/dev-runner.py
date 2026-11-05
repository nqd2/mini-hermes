"""Run local app processes together and restart services when backend files change."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from dotenv import dotenv_values
from watchfiles import watch


ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / 'packages' / 'backend'
FRONTEND = ROOT / 'packages' / 'frontend'
PYTHON = sys.executable
VITE = FRONTEND / 'node_modules' / '.bin' / ('vite.cmd' if os.name == 'nt' else 'vite')
STOP = threading.Event()
LOCK = threading.Lock()
PROCESSES: dict[str, subprocess.Popen[str]] = {}


def prepare_data_dir() -> None:
    settings = dotenv_values(BACKEND / '.env')
    data_dir = Path(settings.get('DATA_DIR') or 'data')
    if not data_dir.is_absolute():
        data_dir = BACKEND / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)


def wait_for_backend(timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with LOCK:
            backend = PROCESSES['backend']
        if backend.poll() is not None:
            raise RuntimeError(f'Backend exited with code {backend.returncode}')
        try:
            with urlopen('http://127.0.0.1:8080/health', timeout=2) as response:
                if response.status == 200:
                    print('[backend] ready', flush=True)
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('Backend did not become healthy within 90 seconds')


def show_output(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f'[{name}] {line}', end='', flush=True)


def start(name: str, command: list[str], cwd: Path) -> None:
    child_env = os.environ.copy()
    if cwd == BACKEND:
        token = dotenv_values(BACKEND / '.env').get('MH_SANDBOX_TOKEN')
        if not token or token.startswith('replace-'):
            raise RuntimeError('Set MH_SANDBOX_TOKEN in packages/backend/.env before starting the dev stack')
        child_env['MH_SANDBOX_TOKEN'] = token
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
        start_new_session=os.name != 'nt',
    )
    with LOCK:
        PROCESSES[name] = process
    threading.Thread(target=show_output, args=(name, process), daemon=True).start()
    print(f'[{name}] started (pid {process.pid})', flush=True)


def stop_process(process: subprocess.Popen[str], *, tree: bool = True) -> None:
    if process.poll() is not None:
        return
    if os.name == 'nt' and tree:
        subprocess.run(
            ['taskkill', '/PID', str(process.pid), '/T', '/F'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    elif os.name == 'nt':
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def watch_backend() -> None:
    def relevant(_change: object, path: str) -> bool:
        changed = Path(path)
        return (changed.suffix == '.py' or changed == BACKEND / '.env'
                or (BACKEND / 'open_webui' / 'mini_hermes' / 'skills') in changed.parents)

    for changes in watch(BACKEND / 'open_webui', BACKEND / '.env', watch_filter=relevant,
                         stop_event=STOP, raise_interrupt=False):
        if STOP.is_set():
            return
        print(f'[dev] restarting sandbox, backend, connector and worker after {len(changes)} file change(s)', flush=True)
        with LOCK:
            old = [PROCESSES.pop(name, None) for name in ('worker', 'connector', 'backend', 'sandbox')]
        for process in old:
            if process:
                stop_process(process, tree=False)
        if not STOP.is_set():
            start('sandbox', [PYTHON, '-m', 'open_webui.mini_hermes.sandbox_service'], BACKEND)
            start('backend', [PYTHON, '-m', 'uvicorn', 'open_webui.main:app',
                              '--host', '127.0.0.1', '--port', '8080', '--loop', 'none'], BACKEND)
            wait_for_backend()
            start('connector', [PYTHON, '-m', 'uvicorn', 'open_webui.mini_hermes.drive:app',
                                '--host', '127.0.0.1', '--port', '8090', '--loop', 'none'], BACKEND)
            start('worker', [PYTHON, '-m', 'open_webui.mini_hermes.worker'], BACKEND)


def main() -> int:
    if not VITE.is_file():
        raise RuntimeError('Vite dependencies missing; run npm ci --force in packages/frontend')
    try:
        prepare_data_dir()
        start('sandbox', [PYTHON, '-m', 'open_webui.mini_hermes.sandbox_service'], BACKEND)
        start('backend', [PYTHON, '-m', 'uvicorn', 'open_webui.main:app',
                          '--host', '127.0.0.1', '--port', '8080', '--loop', 'none'], BACKEND)
        wait_for_backend()
        start('connector', [PYTHON, '-m', 'uvicorn', 'open_webui.mini_hermes.drive:app',
                            '--host', '127.0.0.1', '--port', '8090', '--loop', 'none'], BACKEND)
        start('worker', [PYTHON, '-m', 'open_webui.mini_hermes.worker'], BACKEND)
        start('frontend', [str(VITE), 'dev', '--host', '127.0.0.1'], FRONTEND)
        threading.Thread(target=watch_backend, daemon=True).start()
        print('Dev stack running. Press Ctrl+C to stop app processes.', flush=True)
        while not STOP.wait(1):
            with LOCK:
                processes = list(PROCESSES.items())
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    with LOCK:
                        if PROCESSES.get(name) is not process:
                            continue
                    print(f'[{name}] exited with code {code}', flush=True)
                    return code or 1
    except KeyboardInterrupt:
        print('\nStopping dev stack...', flush=True)
        return 0
    finally:
        STOP.set()
        with LOCK:
            processes = list(PROCESSES.values())
        for process in reversed(processes):
            stop_process(process)
    return 0


if __name__ == '__main__':
    sys.exit(main())
