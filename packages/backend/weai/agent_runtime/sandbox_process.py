"""Linux child restrictions inside the sandbox container."""

import ctypes
import errno
import socket
from functools import lru_cache


class _Argument(ctypes.Structure):
    _fields_ = [('index', ctypes.c_uint), ('comparison', ctypes.c_uint),
                ('value', ctypes.c_uint64), ('mask', ctypes.c_uint64)]


@lru_cache(maxsize=1)
def child_filter():
    """Prepare libseccomp before forking. The Debian sandbox base ships libseccomp2."""
    library = ctypes.CDLL('libseccomp.so.2')
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Argument)
    ]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError('Sandbox process filter unavailable')
    try:
        # A command and all its children stay in the timed process group. The
        # broker handles network I/O; children inherit only local standard I/O.
        deny = 0x00050000 | errno.EPERM  # SCMP_ACT_ERRNO
        # Local IPC is used by office tools. The container has no host sockets.
        non_local = _Argument(0, 1, socket.AF_UNIX, 0)  # SCMP_CMP_NE
        number = library.seccomp_syscall_resolve_name(b'socket')
        if number < 0 or library.seccomp_rule_add_array(context, deny, number, 1, ctypes.byref(non_local)):
            raise RuntimeError('Could not restrict sandbox sockets')
        for name in ('setsid', 'setpgid', 'unshare', 'setns', 'io_uring_setup'):
            number = library.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and library.seccomp_rule_add(context, deny, number, 0):
                raise RuntimeError('Could not configure sandbox process filter')
    except BaseException:
        library.seccomp_release(context)
        raise

    def install():
        if library.seccomp_load(context):
            raise RuntimeError('Could not isolate sandbox child')

    return install
