import os

import psutil

from common.protocol import ProtocolError


def processes(args: dict) -> dict:
    sort = args.get("sort", "ram")
    limit = args.get("limit", 100)
    if (
        sort not in {"ram", "cpu", "pid"}
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 500
    ):
        raise ProtocolError("Invalid process sort or limit")
    rows = []
    for process in psutil.process_iter(
        ["pid", "name", "username", "memory_info", "cpu_percent", "create_time"]
    ):
        try:
            info = process.info
            rows.append(
                {
                    "pid": info["pid"],
                    "name": info["name"],
                    "user": info["username"],
                    "ram": info["memory_info"].rss if info["memory_info"] else 0,
                    "cpu": info["cpu_percent"],
                    "created": info["create_time"],
                }
            )
        except psutil.Error:
            continue
    rows.sort(key=lambda row: row[sort] or 0, reverse=sort != "pid")
    return {"ok": True, "processes": rows[:limit]}


def kill(args: dict) -> dict:
    pid, created = args.get("pid"), args.get("created")
    if not isinstance(args.get("force", False), bool):
        raise ProtocolError("force must be a boolean")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 1
        or pid in {os.getpid(), os.getppid()}
    ):
        raise ProtocolError("Invalid or protected PID")
    if not isinstance(created, int | float):
        raise ProtocolError("Process creation time from ps is required to prevent PID reuse")
    process = psutil.Process(pid)
    if abs(process.create_time() - created) > 0.01:
        raise ProtocolError("PID has been reused")
    process.terminate()
    try:
        process.wait(timeout=3)
    except psutil.TimeoutExpired:
        if not args.get("force", False):
            raise ProtocolError("Process still running; explicit force is required") from None
        process.kill()
        process.wait(timeout=3)
    return {"ok": True, "pid": pid}
