import os
import re
from pathlib import Path

from agent.fingerprint import is_admin
from agent.modules.executor import Emit, run_process
from common.protocol import ProtocolError


def text(value: object, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise ProtocolError(f"Invalid {name}")
    return value


async def admin(tool: str, args: dict, root: Path, emit: Emit, timeout: float) -> dict:
    operation = args.get("operation", "query" if tool == "registry" else "list")
    if operation not in {"list", "status", "query"} and not is_admin():
        raise ProtocolError(
            "Administrator privileges required; automatic elevation is not supported"
        )
    if tool == "services" and os.name != "nt":
        if operation not in {"list", "status", "start", "stop", "restart"}:
            raise ProtocolError("Invalid service operation")
        argv = ["systemctl", "--no-pager", "--plain"]
        if operation == "list":
            argv += ["list-units", "--type=service", "--all"]
        else:
            name = text(args.get("name"), "service name", 200)
            if not re.fullmatch(r"[A-Za-z0-9_.@-]+", name) or name.startswith("-"):
                raise ProtocolError("Invalid service name")
            argv += [operation, name]
    elif os.name != "nt":
        raise ProtocolError("This administration tool requires Windows")
    elif tool == "registry":
        operation = args.get("operation", "query")
        key = text(args.get("key"), "registry key")
        if not key.startswith(("HKLM\\", "HKCU\\", "HKCR\\", "HKU\\", "HKCC\\")):
            raise ProtocolError("Local registry key required")
        if operation == "query":
            argv = ["reg.exe", "query", key]
            if args.get("name"):
                argv += ["/v", text(args["name"], "value name")]
        elif operation == "set":
            kind = args.get("value_type", "REG_SZ")
            if kind not in {
                "REG_SZ",
                "REG_EXPAND_SZ",
                "REG_DWORD",
                "REG_QWORD",
                "REG_MULTI_SZ",
                "REG_BINARY",
            }:
                raise ProtocolError("Invalid registry value type")
            argv = [
                "reg.exe",
                "add",
                key,
                "/v",
                text(args.get("name"), "value name"),
                "/t",
                kind,
                "/d",
                text(str(args.get("value", "")), "value"),
                "/f",
            ]
        else:
            raise ProtocolError("Invalid registry operation")
    elif tool == "services":
        if operation == "list":
            argv = ["sc.exe", "query", "type=", "service", "state=", "all"]
        elif operation in {"status", "start", "stop"}:
            argv = [
                "sc.exe",
                "query" if operation == "status" else operation,
                text(args.get("name"), "service name", 200),
            ]
        else:
            raise ProtocolError("Invalid service operation")
    elif tool == "tasks":
        if operation == "list":
            argv = ["schtasks.exe", "/Query", "/FO", "CSV", "/V"]
        elif operation == "create":
            argv = [
                "schtasks.exe",
                "/Create",
                "/TN",
                text(args.get("name"), "task name", 200),
                "/TR",
                text(args.get("command"), "task command"),
                "/SC",
                "ONLOGON",
            ]
        else:
            raise ProtocolError("Invalid scheduled task operation")
    else:
        raise ProtocolError("Unknown administration tool")
    return await run_process(argv, root, emit, timeout)
