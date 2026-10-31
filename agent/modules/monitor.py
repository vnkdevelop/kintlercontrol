import getpass
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil

from agent.fingerprint import is_admin, platform_name
from common.protocol import ProtocolError


def summary() -> dict:
    memory = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(),
        "ram_percent": memory.percent,
        "ram_total": memory.total,
        "uptime": max(0, time.time() - psutil.boot_time()),
    }


def metadata(name: str) -> dict:
    return {
        "name": name,
        "platform": platform_name(),
        "user": getpass.getuser(),
        "admin": is_admin(),
        "version": "1.0.0",
        "telemetry": summary(),
    }


def sysinfo() -> dict:
    disks = []
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            disks.append(
                {
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "total": usage.total,
                    "free": usage.free,
                    "percent": usage.percent,
                }
            )
        except (OSError, psutil.Error):
            continue
    addresses = {
        name: [{"family": str(addr.family), "address": addr.address} for addr in entries]
        for name, entries in psutil.net_if_addrs().items()
    }
    return {
        "ok": True,
        "hostname": socket.gethostname(),
        "platform": platform_name(),
        "user": getpass.getuser(),
        "admin": is_admin(),
        "cpu_count": psutil.cpu_count(),
        **summary(),
        "disks": disks,
        "network": addresses,
        "gpu": gpu_info(),
    }


def gpu_info() -> dict:
    try:
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        if query.returncode == 0:
            devices = []
            for line in query.stdout.splitlines()[:16]:
                name, total, used, usage = (part.strip() for part in line.split(","))
                devices.append(
                    {
                        "name": name,
                        "memory_total_mib": float(total),
                        "memory_used_mib": float(used),
                        "utilization_percent": float(usage),
                    }
                )
            if devices:
                return {"available": True, "provider": "nvidia-smi", "devices": devices}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    if sys.platform == "win32":
        import winreg

        devices = []
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}",
            ) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    child = winreg.EnumKey(parent, index)
                    if not child.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(parent, child) as adapter:
                            devices.append({"name": winreg.QueryValueEx(adapter, "DriverDesc")[0]})
                    except OSError:
                        continue
        except OSError:
            pass
        if devices:
            return {
                "available": True,
                "provider": "windows-display-class",
                "devices": devices,
                "metrics_available": False,
            }
    else:
        devices = []
        for device in Path("/sys/class/drm").glob("card[0-9]*/device"):
            try:
                devices.append(
                    {
                        "vendor_id": (device / "vendor").read_text().strip(),
                        "device_id": (device / "device").read_text().strip(),
                    }
                )
            except OSError:
                continue
        if devices:
            return {
                "available": True,
                "provider": "linux-drm",
                "devices": devices,
                "metrics_available": False,
            }
    return {"available": False, "reason": "No supported GPU provider detected"}


def screenshot(args: dict) -> bytes:
    try:
        import mss
        import mss.tools
    except ImportError:
        raise ProtocolError("Install the screen extra on this agent") from None
    monitor = args.get("monitor", 1)
    if isinstance(monitor, bool) or not isinstance(monitor, int) or monitor < 0:
        raise ProtocolError("Invalid monitor index")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        raise ProtocolError("No graphical desktop available")
    with mss.mss() as capture:
        if monitor >= len(capture.monitors):
            raise ProtocolError("Monitor not found")
        image = capture.grab(capture.monitors[monitor])
        output = mss.tools.to_png(image.rgb, image.size)
        if output is None:
            raise ProtocolError("Screenshot encoding failed")
        return output
