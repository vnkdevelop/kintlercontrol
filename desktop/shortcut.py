import ctypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any


class Guid(ctypes.Structure):
    _fields_ = [
        ("one", ctypes.c_uint32),
        ("two", ctypes.c_uint16),
        ("three", ctypes.c_uint16),
        ("four", ctypes.c_ubyte * 8),
    ]


def guid(value: str) -> Guid:
    return Guid.from_buffer_copy(uuid.UUID(value).bytes_le)


def create_shortcut(executable: Path, destination: Path | None = None) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("Desktop shortcuts are Windows-specific")
    if not executable.is_file():
        raise ValueError("Controller executable is missing")
    ole = ctypes.windll.ole32
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2) >= 0
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    clsid = guid("00021401-0000-0000-C000-000000000046")
    iid = guid("000214F9-0000-0000-C000-000000000046")
    ole.CoCreateInstance.argtypes = [
        ctypes.POINTER(Guid),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole.CoCreateInstance.restype = ctypes.c_long

    def invoke(pointer: ctypes.c_void_p, index: int, argument_types: list, *arguments: Any) -> int:
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)(table[index])
        result = function(pointer, *arguments)
        if result < 0:
            raise RuntimeError("Windows could not create the shortcut")
        return int(result)

    try:
        if (
            ole.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link)
            )
            < 0
        ):
            raise RuntimeError("Windows ShellLink is unavailable")
        if destination is None:
            desktop = guid("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
            value = ctypes.c_wchar_p()
            shell = ctypes.windll.shell32
            shell.SHGetKnownFolderPath.argtypes = [
                ctypes.POINTER(Guid),
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_wchar_p),
            ]
            if shell.SHGetKnownFolderPath(ctypes.byref(desktop), 0, None, ctypes.byref(value)) != 0:
                raise RuntimeError("Desktop folder unavailable")
            destination = Path(value.value or "") / "KintlerControl.lnk"
            ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))
        if destination.exists():
            raise FileExistsError("Existing shortcut will not be overwritten")
        invoke(link, 20, [ctypes.c_wchar_p], str(executable.resolve()))
        invoke(link, 9, [ctypes.c_wchar_p], str(executable.resolve().parent))
        invoke(link, 7, [ctypes.c_wchar_p], "KintlerControl desktop controller")
        persist_iid = guid("0000010b-0000-0000-C000-000000000046")
        invoke(
            link,
            0,
            [ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)],
            ctypes.byref(persist_iid),
            ctypes.byref(persist),
        )
        invoke(persist, 6, [ctypes.c_wchar_p, ctypes.c_int], str(destination), True)
        return destination
    finally:
        for pointer in (persist, link):
            if pointer.value:
                invoke(pointer, 2, [])
        if initialized:
            ole.CoUninitialize()


def read_shortcut(path: Path) -> tuple[str, str, str | None]:
    if sys.platform != "win32":
        raise RuntimeError("Desktop shortcuts are Windows-specific")
    ole = ctypes.windll.ole32
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2) >= 0
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()

    def invoke(pointer: ctypes.c_void_p, index: int, argument_types: list, *arguments: Any) -> int:
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)(table[index])
        result = function(pointer, *arguments)
        if result < 0:
            raise OSError("Windows could not read the shortcut")
        return int(result)

    clsid = guid("00021401-0000-0000-C000-000000000046")
    iid = guid("000214F9-0000-0000-C000-000000000046")
    ole.CoCreateInstance.argtypes = [
        ctypes.POINTER(Guid),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole.CoCreateInstance.restype = ctypes.c_long
    try:
        if (
            ole.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link)
            )
            < 0
        ):
            raise OSError("Windows ShellLink is unavailable")
        persist_iid = guid("0000010b-0000-0000-C000-000000000046")
        invoke(
            link,
            0,
            [ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)],
            ctypes.byref(persist_iid),
            ctypes.byref(persist),
        )
        invoke(persist, 5, [ctypes.c_wchar_p, ctypes.c_ulong], str(path), 0)
        target = ctypes.create_unicode_buffer(32768)
        arguments = ctypes.create_unicode_buffer(32768)
        cwd = ctypes.create_unicode_buffer(32768)
        invoke(
            link,
            3,
            [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong],
            target,
            len(target),
            None,
            0,
        )
        invoke(link, 10, [ctypes.c_wchar_p, ctypes.c_int], arguments, len(arguments))
        invoke(link, 8, [ctypes.c_wchar_p, ctypes.c_int], cwd, len(cwd))
        target_path = os.path.expandvars(target.value)
        directory = os.path.expandvars(cwd.value)
        return (
            target_path or str(path),
            arguments.value if target_path else "",
            directory or None,
        )
    finally:
        for pointer in (persist, link):
            if pointer.value:
                invoke(pointer, 2, [])
        if initialized:
            ole.CoUninitialize()


def start_menu_folders() -> list[Path]:
    if sys.platform != "win32":
        return []
    shell = ctypes.windll.shell32
    ole = ctypes.windll.ole32
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(Guid),
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    folders = []
    for identifier in (
        "A77F5D77-2E2B-44C3-A6A2-ABA601054A51",
        "0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8",
    ):
        folder_id = guid(identifier)
        value = ctypes.c_wchar_p()
        if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(value)) >= 0:
            try:
                if value.value:
                    folders.append(Path(value.value))
            finally:
                ole.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))
    return folders
