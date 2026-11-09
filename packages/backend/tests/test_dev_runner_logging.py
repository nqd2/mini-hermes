import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace


class StrictCp1252Stream:
    encoding = 'cp1252'

    def __init__(self):
        self.lines = []

    def write(self, value):
        value.encode(self.encoding, errors='strict')
        self.lines.append(value)

    def flush(self):
        pass


def _load_dev_runner():
    repo_root = Path(__file__).resolve().parents[3]
    runner_path = repo_root / 'scripts' / 'dev-runner.py'
    spec = importlib.util.spec_from_file_location('mini_hermes_dev_runner', runner_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_show_output_handles_text_not_supported_by_windows_console(monkeypatch):
    dev_runner = _load_dev_runner()
    console = StrictCp1252Stream()
    monkeypatch.setattr(dev_runner.sys, 'stdout', console)
    process = SimpleNamespace(stdout=io.StringIO('model output: 中文'))

    dev_runner.show_output('backend', process)

    assert console.lines == ['[backend] model output: ??']
