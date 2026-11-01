import ctypes
import sys


def clipboard_text() -> str | None:
    if sys.platform != "win32":
        return None
    user = ctypes.windll.user32
    kernel = ctypes.windll.kernel32
    user.OpenClipboard.argtypes = [ctypes.c_void_p]
    user.GetClipboardData.argtypes = [ctypes.c_uint]
    user.GetClipboardData.restype = ctypes.c_void_p
    kernel.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel.GlobalSize.restype = ctypes.c_size_t
    if not user.OpenClipboard(None):
        raise OSError("Буфер обмена занят. Повторите вставку.")
    try:
        if not user.IsClipboardFormatAvailable(13):
            return None
        handle = user.GetClipboardData(13)
        if not handle:
            raise OSError("Не удалось прочитать текст из буфера обмена.")
        size = kernel.GlobalSize(handle)
        if not size or size % 2 or size > 2 * 1024 * 1024:
            raise OSError("Текст в буфере обмена имеет неподдерживаемый размер.")
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise OSError("Не удалось прочитать текст из буфера обмена.")
        try:
            return (
                ctypes.string_at(pointer, size)
                .decode("utf-16le", errors="replace")
                .split("\0", 1)[0]
            )
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()
