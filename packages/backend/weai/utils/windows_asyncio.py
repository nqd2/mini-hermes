"""Windows Selector loop compatibility for psycopg.

CPython 3.12 leaves the self-pipe reader registered after a reset (or EOF).
On Windows these are TCP loopback sockets: a dead pair can repeatedly wake
select(), flooding logs and consuming CPU. Replace the pair, rather than
filtering asyncio exceptions or changing database event-loop requirements.
The private asyncio hooks are isolated here and covered by wakeup tests.
"""

import asyncio
import logging
import sys


log = logging.getLogger(__name__)


class RecoveringSelectorEventLoop(asyncio.SelectorEventLoop):
    def _read_from_self(self):
        # Mirrors BaseSelectorEventLoop's drain loop, with recovery on a dead
        # socket. Run on the loop thread; _write_to_self already tolerates a
        # concurrent close, and a fresh wakeup preserves queued callbacks.
        while True:
            try:
                data = self._ssock.recv(4096)
                if not data:
                    self._rebuild_wakeup_sockets('EOF')
                    return
                self._process_self_data(data)
            except InterruptedError:
                continue
            except BlockingIOError:
                return
            except ConnectionResetError:
                self._rebuild_wakeup_sockets('connection reset')
                return

    def _rebuild_wakeup_sockets(self, reason):
        self._close_self_pipe()
        self._make_self_pipe()
        self._write_to_self()
        log.warning('Rebuilt asyncio wakeup sockets after %s', reason)


def install_windows_selector_policy():
    """Configure future loops only; callers keep control of non-Windows loops."""
    if sys.platform != 'win32':
        return

    class RecoveringWindowsSelectorPolicy(asyncio.WindowsSelectorEventLoopPolicy):
        _loop_factory = RecoveringSelectorEventLoop

    asyncio.set_event_loop_policy(RecoveringWindowsSelectorPolicy())
