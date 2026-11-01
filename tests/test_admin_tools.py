from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.modules import admin_tools
from common.protocol import ProtocolError


async def test_admin_is_gated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(admin_tools, "is_admin", lambda: False)

    async def emit(stream: str, data: str):
        pass

    with pytest.raises(ProtocolError, match="Administrator"):
        await admin_tools.admin(
            "services", {"operation": "stop", "name": "fixture"}, tmp_path, emit, 1
        )


async def test_read_only_admin_tools_do_not_require_elevation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(admin_tools, "is_admin", lambda: False)
    monkeypatch.setattr(admin_tools, "os", SimpleNamespace(name="nt"))
    captured = []

    async def process(argv, cwd, emit, timeout):
        captured.append(argv)
        return {"ok": True}

    async def emit(stream: str, data: str):
        pass

    monkeypatch.setattr(admin_tools, "run_process", process)
    await admin_tools.admin("services", {"operation": "list"}, tmp_path, emit, 1)
    await admin_tools.admin(
        "registry", {"operation": "query", "key": "HKCU\\Software"}, tmp_path, emit, 1
    )
    await admin_tools.admin("tasks", {"operation": "list"}, tmp_path, emit, 1)
    assert [arguments[0] for arguments in captured] == ["sc.exe", "reg.exe", "schtasks.exe"]


async def test_windows_registry_argv_without_shell(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(admin_tools, "is_admin", lambda: True)
    monkeypatch.setattr(admin_tools, "os", SimpleNamespace(name="nt"))
    captured = []

    async def process(argv, cwd, emit, timeout):
        captured.extend(argv)
        return {"ok": True}

    async def emit(stream: str, data: str):
        pass

    monkeypatch.setattr(admin_tools, "run_process", process)
    await admin_tools.admin(
        "registry",
        {
            "operation": "set",
            "key": "HKCU\\Software\\Kintler",
            "name": "test",
            "value": "a & echo b",
        },
        tmp_path,
        emit,
        1,
    )
    assert captured[0] == "reg.exe"
    assert "a & echo b" in captured
    with pytest.raises(ProtocolError, match="Local registry"):
        await admin_tools.admin("registry", {"key": "\\\\remote\\HKLM"}, tmp_path, emit, 1)
