import ctypes
import hashlib
import os
import platform
import socket
import sys
from pathlib import Path


def node_id() -> str:
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            machine = winreg.QueryValueEx(key, "MachineGuid")[0]
    else:
        path = Path("/etc/machine-id")
        if not path.exists():
            path = Path("/var/lib/dbus/machine-id")
        machine = path.read_text().strip()
    if not machine:
        raise RuntimeError("Machine identity is unavailable")
    digest = hashlib.sha256((machine + socket.gethostname()).encode()).hexdigest()[:16]
    return "node_" + digest


def is_admin() -> bool:
    if sys.platform == "win32":
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0


def platform_name() -> str:
    return platform.system() + " " + platform.release()
