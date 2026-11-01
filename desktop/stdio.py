import ctypes
import io
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager


def windows_stream(kind: int, read: bool) -> io.TextIOWrapper:
    if sys.platform != "win32":
        raise RuntimeError("Windowed stdio recovery is Windows-specific")
    import msvcrt

    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.DuplicateHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    kernel.DuplicateHandle.restype = ctypes.c_int
    handle = kernel.GetStdHandle(kind & 0xFFFFFFFF)
    if handle in {None, ctypes.c_void_p(-1).value}:
        raise RuntimeError("MCP mode requires redirected stdin/stdout from the AI client")
    process = kernel.GetCurrentProcess()
    duplicate = ctypes.c_void_p()
    if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
        raise RuntimeError("Cannot duplicate the MCP stdio handle")
    descriptor = msvcrt.open_osfhandle(
        int(duplicate.value or 0), os.O_BINARY | (os.O_RDONLY if read else os.O_WRONLY)
    )
    raw = os.fdopen(descriptor, "rb" if read else "wb", buffering=0)
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace", line_buffering=True)


@contextmanager
def restored_stdio() -> Iterator[None]:
    previous = (sys.stdin, sys.stdout, sys.stderr)
    owned = []
    try:
        for name, kind, read in (
            ("stdin", -10, True),
            ("stdout", -11, False),
            ("stderr", -12, False),
        ):
            if getattr(sys, name) is None:
                stream = windows_stream(kind, read)
                setattr(sys, name, stream)
                owned.append(stream)
        yield
    finally:
        sys.stdin, sys.stdout, sys.stderr = previous
        for stream in owned:
            stream.close()
