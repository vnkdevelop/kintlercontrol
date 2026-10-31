import asyncio
import sys

import psutil
import pytest

from agent.modules import processes
from common.protocol import ProtocolError


async def test_kill_checks_creation_time():
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)"
    )
    try:
        created = psutil.Process(process.pid).create_time()
        with pytest.raises(ProtocolError, match="reused"):
            processes.kill({"pid": process.pid, "created": created - 10})
        result = await asyncio.to_thread(processes.kill, {"pid": process.pid, "created": created})
        assert result["ok"]
        await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize(
    "args",
    [
        {"pid": 0, "created": 0},
        {"pid": True},
        {"pid": 12345},
        {"pid": 12345, "created": 0, "force": "false"},
    ],
)
def test_invalid_kill(args: dict):
    with pytest.raises(ProtocolError):
        processes.kill(args)
