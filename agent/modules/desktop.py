import asyncio
import json
import os
import subprocess
from pathlib import Path

from agent.modules import executor
from common.protocol import ProtocolError

MAX_RESULT = 160 * 1024


def open_application(args: dict, root: Path) -> dict:
    if set(args) - {"app", "arguments"}:
        raise ProtocolError("Invalid open arguments")
    app = args.get("app")
    arguments = args.get("arguments", [])
    if not isinstance(app, str) or not app.strip() or len(app) > 10000 or "\0" in app:
        raise ProtocolError("Invalid application")
    if (
        not isinstance(arguments, list)
        or len(arguments) > 64
        or any(not isinstance(v, str) or len(v) > 10000 or "\0" in v for v in arguments)
    ):
        raise ProtocolError("Invalid application arguments")
    if os.name == "nt":
        os.startfile(app, "open", subprocess.list2cmdline(arguments), str(root))
        return {"ok": True, "accepted": True, "verification": "launch_requested"}
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise ProtocolError("An interactive graphical session is required")
    child = subprocess.Popen(
        [app, *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    code = child.poll()
    return {"ok": code is None or code == 0, "pid": child.pid, "verification": "process_started"}


async def operate(
    args: dict, root: Path, session_id: str, timeout: float, native: Path | None
) -> dict:
    if os.name != "nt":
        raise ProtocolError("Embedded desktop control requires the native Windows Agent")
    if native is None or not native.is_absolute() or not native.is_file():
        raise ProtocolError(
            "Use the native KintlerAgent.exe for desktop control; source hosts may configure KINTLER_NATIVE_AGENT"
        )
    if set(args) - {"action", "params", "headed"}:
        raise ProtocolError("Invalid desktop arguments")
    action = args.get("action")
    if not isinstance(action, str) or not action or action == "launch":
        raise ProtocolError("Invalid desktop action; applications require open permission")
    if not isinstance(args.get("params", {}), dict) or not isinstance(
        args.get("headed", False), bool
    ):
        raise ProtocolError("Invalid desktop parameters")
    payload = json.dumps(
        {**args, "session": session_id, "timeout_ms": int(timeout * 1000)}, ensure_ascii=False
    ).encode()
    process = await asyncio.create_subprocess_exec(
        str(native),
        "--desktop-worker",
        cwd=root,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={
            **{
                k: v
                for k, v in os.environ.items()
                if k not in {"AGENT_DESKTOP_SESSION", "AGENT_DESKTOP_AGENT_ID"}
            },
            "AGENT_DESKTOP_HOME": str(root.parent.resolve() / "desktop-state" / root.name),
        },
        creationflags=0x08000000,
    )
    try:
        if process.stdin is None or process.stdout is None:
            raise ProtocolError("Desktop worker pipes unavailable")
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        output = bytearray()
        while chunk := await process.stdout.read(4096):
            output.extend(chunk)
            if len(output) > MAX_RESULT:
                raise ProtocolError("Desktop output limit reached; inspect state before retry")
        if await process.wait() != 0:
            raise ProtocolError("Desktop worker failed; inspect state before retry")
        result = json.loads(output)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ProtocolError("Invalid desktop result")
        return result
    finally:
        await executor.terminate(process)
