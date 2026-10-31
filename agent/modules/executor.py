import asyncio
import codecs
import contextlib
import locale
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import psutil

from common.protocol import ProtocolError

Emit = Callable[[str, str], Awaitable[None]]


async def terminate(process: asyncio.subprocess.Process) -> None:
    if sys.platform != "win32":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        if process.returncode is not None:
            return

        def kill_tree() -> None:
            with contextlib.suppress(psutil.Error):
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in reversed(children):
                    with contextlib.suppress(psutil.Error):
                        child.kill()
                parent.kill()
                psutil.wait_procs(children, timeout=3)

        await asyncio.to_thread(kill_tree)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), 5)


def safe_cwd(root: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise ProtocolError("cwd must be a relative path")
    if Path(value).is_absolute() or Path(value).drive or ".." in Path(value).parts:
        raise ProtocolError("cwd must stay inside workspace")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_dir():
        raise ProtocolError("cwd is outside workspace or unavailable")
    return path


async def run_process(
    argv: list[str],
    cwd: Path,
    emit: Emit,
    timeout: float,
    output_limit: int = 2 * 1024 * 1024,
    encoding: str | None = None,
) -> dict:
    options = {"creationflags": 0x08000200} if os.name == "nt" else {"start_new_session": True}
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **options,
    )
    total = 0

    async def pump(reader: asyncio.StreamReader | None, stream: str) -> None:
        nonlocal total
        if reader is None:
            return
        decoder = codecs.getincrementaldecoder(
            encoding or (locale.getpreferredencoding(False) if os.name == "nt" else "utf-8")
        )(errors="replace")
        while chunk := await reader.read(4096):
            total += len(chunk)
            if total > output_limit:
                raise ProtocolError("Output limit reached")
            text = decoder.decode(chunk)
            if text:
                await emit(stream, text)
        tail = decoder.decode(b"", final=True)
        if tail:
            await emit(stream, tail)

    tasks = [
        asyncio.create_task(pump(process.stdout, "stdout")),
        asyncio.create_task(pump(process.stderr, "stderr")),
    ]
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*tasks)
            code = await process.wait()
        return {"ok": code == 0, "exit_code": code}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await terminate(process)


async def execute(args: dict, root: Path, emit: Emit, timeout: float) -> dict:
    command = args.get("command")
    shell = args.get("shell", "cmd" if os.name == "nt" else "bash")
    if (
        not isinstance(command, str)
        or not command.strip()
        or len(command) > 32768
        or "\x00" in command
    ):
        raise ProtocolError("Invalid shell command")
    if shell == "cmd" and os.name == "nt":
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    elif shell == "powershell" and os.name == "nt":
        argv = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    elif shell == "bash" and os.name != "nt":
        argv = ["/bin/bash", "--noprofile", "--norc", "-c", command]
    else:
        raise ProtocolError("Shell is not supported on this node")
    return await run_process(argv, safe_cwd(root, args.get("cwd", ".")), emit, timeout)
