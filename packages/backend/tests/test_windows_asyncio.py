import asyncio
import select
import socket
import sys
import threading

import pytest


class ResetSocket:
    """A real registered socket whose next recv reports a Windows reset."""

    def __init__(self, sock):
        self.sock = sock

    def recv(self, size):
        raise ConnectionResetError(10054, 'Connection reset by peer')

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        self.sock.close()


def test_stock_selector_reproduces_unhandled_self_pipe_reset():
    loop = asyncio.SelectorEventLoop()
    try:
        loop._ssock = ResetSocket(loop._ssock)
        with pytest.raises(ConnectionResetError):
            loop._read_from_self()
        # The dead reader stays registered, allowing the same callback to repeat.
        assert loop._selector.get_key(loop._ssock.fileno())
    finally:
        loop.close()


@pytest.mark.parametrize('failure', ['reset', 'eof'])
def test_self_pipe_recovery_preserves_cross_thread_wakeup(failure, caplog):
    from open_webui.utils.windows_asyncio import RecoveringSelectorEventLoop

    loop = RecoveringSelectorEventLoop()
    thread = None
    release = threading.Event()
    try:
        old_reader, old_writer = loop._ssock, loop._csock
        internal_fds = loop._internal_fds
        if failure == 'reset':
            loop._ssock = ResetSocket(old_reader)
        else:
            old_writer.shutdown(socket.SHUT_WR)
            old_writer.close()
            assert select.select([old_reader], [], [], 1)[0]
        loop._read_from_self()
        assert old_reader.fileno() == old_writer.fileno() == -1
        assert loop._internal_fds == internal_fds
        assert loop._selector.get_key(loop._ssock.fileno())
        assert 'Rebuilt asyncio wakeup sockets' in caplog.text

        result = loop.create_future()

        def wake():
            if release.wait(timeout=2):
                loop.call_soon_threadsafe(result.set_result, 'awake')

        thread = threading.Thread(target=wake)
        thread.start()
        loop.call_later(0.01, release.set)
        assert loop.run_until_complete(asyncio.wait_for(result, timeout=1)) == 'awake'
        # Repeated healthy reads must not replace or leak sockets.
        reader = loop._ssock
        loop._read_from_self()
        assert loop._ssock is reader
    finally:
        release.set()
        if thread:
            thread.join(timeout=2)
        loop.close()


def test_unrelated_socket_errors_are_not_swallowed():
    from open_webui.utils.windows_asyncio import RecoveringSelectorEventLoop

    class BrokenSocket(ResetSocket):
        def recv(self, size):
            raise PermissionError('unrelated error')

    loop = RecoveringSelectorEventLoop()
    try:
        loop._ssock = BrokenSocket(loop._ssock)
        with pytest.raises(PermissionError, match='unrelated error'):
            loop._read_from_self()
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows policy integration')
def test_installed_policy_used_by_asyncio_run():
    from open_webui.utils.windows_asyncio import (
        RecoveringSelectorEventLoop,
        install_windows_selector_policy,
    )

    previous = asyncio.get_event_loop_policy()
    try:
        install_windows_selector_policy()

        async def inspect_loop():
            assert isinstance(asyncio.get_running_loop(), RecoveringSelectorEventLoop)

        asyncio.run(inspect_loop())
    finally:
        asyncio.set_event_loop_policy(previous)


def test_non_windows_policy_is_unchanged(monkeypatch):
    from open_webui.utils import windows_asyncio

    previous = asyncio.get_event_loop_policy()
    monkeypatch.setattr(windows_asyncio.sys, 'platform', 'linux')
    windows_asyncio.install_windows_selector_policy()
    assert asyncio.get_event_loop_policy() is previous
