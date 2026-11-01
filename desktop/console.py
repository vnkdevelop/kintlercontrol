import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO


@contextmanager
def terminal_console(title: str) -> Iterator[None]:
    previous = (
        sys.stdin,
        sys.stdout,
        sys.stderr,
        sys.__stdin__,
        sys.__stdout__,
        sys.__stderr__,
    )
    opened: list[TextIO] = []
    handles: list[tuple[int, int | None]] = []
    allocated = False
    try:
        if sys.platform == "win32":
            kernel = ctypes.windll.kernel32
            kernel.GetConsoleWindow.restype = ctypes.c_void_p
            kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
            kernel.GetStdHandle.restype = ctypes.c_void_p
            kernel.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
            handles = [
                (kind, kernel.GetStdHandle(kind)) for kind in (0xFFFFFFF6, 0xFFFFFFF5, 0xFFFFFFF4)
            ]
            mode = ctypes.c_ulong()
            kernel.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            output_is_console = bool(
                kernel.GetConsoleMode(kernel.GetStdHandle(0xFFFFFFF5), ctypes.byref(mode))
            )
            if (
                getattr(sys, "frozen", False)
                and not output_is_console
                and kernel.GetConsoleWindow()
            ):
                kernel.FreeConsole()
            if not kernel.GetConsoleWindow():
                allocated = bool(kernel.AllocConsole())
                if not allocated:
                    raise RuntimeError("Windows could not open a terminal window")
            kernel.SetConsoleTitleW(title)
            kernel.SetConsoleCP(65001)
            kernel.SetConsoleOutputCP(65001)
            if getattr(sys, "frozen", False):
                import msvcrt

                incoming = open("CONIN$", encoding="utf-8")
                outgoing = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                errors = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                opened.extend((incoming, outgoing, errors))
                for kind, stream in zip((0xFFFFFFF6, 0xFFFFFFF5, 0xFFFFFFF4), opened, strict=True):
                    if not kernel.SetStdHandle(kind, msvcrt.get_osfhandle(stream.fileno())):
                        raise RuntimeError(
                            "Windows could not attach the terminal's standard handles"
                        )
                sys.stdin, sys.stdout, sys.stderr = incoming, outgoing, errors
                for name, stream in zip(
                    ("__stdin__", "__stdout__", "__stderr__"), opened, strict=True
                ):
                    setattr(sys, name, stream)
        yield
    finally:
        for name, saved_stream in zip(
            ("stdin", "stdout", "stderr", "__stdin__", "__stdout__", "__stderr__"),
            previous,
            strict=True,
        ):
            setattr(sys, name, saved_stream)
        if sys.platform == "win32":
            for kind, handle in handles:
                ctypes.windll.kernel32.SetStdHandle(kind, handle)
        for stream in opened:
            stream.close()
        if sys.platform == "win32" and allocated:
            ctypes.windll.kernel32.FreeConsole()
