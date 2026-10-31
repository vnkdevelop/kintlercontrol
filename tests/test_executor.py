import os
import sys
from pathlib import Path

import psutil
import pytest

from agent.modules.executor import execute, run_process
from common.protocol import ProtocolError


async def test_streaming(tmp_path: Path):
    chunks = []

    async def emit(stream: str, data: str):
        chunks.append((stream, data))

    result = await run_process(
        [sys.executable, "-c", "import sys; print('OUT'); print('ERR',file=sys.stderr)"],
        tmp_path,
        emit,
        10,
    )
    assert result == {"ok": True, "exit_code": 0}
    assert "OUT" in "".join(data for stream, data in chunks if stream == "stdout")
    assert "ERR" in "".join(data for stream, data in chunks if stream == "stderr")


async def test_timeout_kills_process(tmp_path: Path):
    pidfile = tmp_path / "pid"

    async def emit(stream: str, data: str):
        pass

    with pytest.raises(TimeoutError):
        await run_process(
            [
                sys.executable,
                "-c",
                f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(60)",
            ],
            tmp_path,
            emit,
            1,
        )
    assert pidfile.exists()
    assert not psutil.pid_exists(int(pidfile.read_text()))


async def test_output_limit(tmp_path: Path):
    async def emit(stream: str, data: str):
        pass

    with pytest.raises(ProtocolError, match="Output limit"):
        await run_process(
            [sys.executable, "-c", "print('a'*10000)"], tmp_path, emit, 10, output_limit=100
        )


async def test_shell_and_cwd(tmp_path: Path):
    chunks = []

    async def emit(stream: str, data: str):
        chunks.append(data)

    result = await execute(
        {"shell": "cmd" if os.name == "nt" else "bash", "command": "echo KINTLER_TEST"},
        tmp_path,
        emit,
        5,
    )
    assert result["ok"] and "KINTLER_TEST" in "".join(chunks)
    with pytest.raises(ProtocolError):
        await execute({"command": "echo no", "cwd": ".."}, tmp_path, emit, 5)
