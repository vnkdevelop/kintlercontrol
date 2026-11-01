import asyncio
import os
import secrets
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from common.protocol import TOOLS
from desktop.settings import (
    agent_settings,
    controller_settings,
    load_profile,
    profile,
    save_profile,
)
from desktop.tools import LABELS, PARAMETERS, arguments, dangerous


def test_profile_roles_and_secret_files(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert profile("controller") != profile("agent")
    values = {
        "KINTLER_URL": "https://localhost:8443",
        "KINTLER_CONTROLLER_ID": "operator",
        "KINTLER_CONTROLLER_SECRET": secrets.token_urlsafe(48),
        "KINTLER_AGENT_SECRET": "not-for-this-role",
    }
    save_profile(profile("controller"), values, "controller")
    loaded = load_profile(profile("controller"), "controller")
    assert "KINTLER_AGENT_SECRET" not in loaded
    assert controller_settings(loaded).principal == "operator"
    with pytest.raises(ValueError, match="другой роли"):
        load_profile(profile("controller"), "agent")


def test_server_env_cannot_be_imported(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("KINTLER_CREDENTIALS=[]\n")
    with pytest.raises(ValueError, match="серверный"):
        load_profile(path, "controller")


def test_agent_policy_defaults_and_relative_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("desktop.settings.node_id", lambda: "node_test")
    values = {
        "KINTLER_URL": "https://localhost:8443",
        "KINTLER_AGENT_ID": "device",
        "KINTLER_AGENT_SECRET": secrets.token_urlsafe(48),
        "KINTLER_WORKSPACE": "workspace",
    }
    settings = agent_settings(values, tmp_path / ".env")
    assert "shell" not in settings.tools
    assert settings.root == tmp_path / "workspace"
    values["KINTLER_AGENT_TOOLS"] = ",".join(TOOLS)
    assert agent_settings(values, tmp_path / ".env").tools == TOOLS


def test_all_tool_forms_and_conversions():
    assert set(LABELS) == TOOLS == set(PARAMETERS)
    assert arguments("kill", {"pid": "10", "created": "123.2", "force": "false"}) == {
        "pid": 10,
        "created": 123.2,
        "force": False,
    }
    assert dangerous("shell", {})
    assert not dangerous("sysinfo", {})
    assert dangerous("registry", {"operation": "set"})
    with pytest.raises(ValueError):
        arguments("download", {"remote_path": "file"})


@pytest.mark.parametrize("code", [None, "123456"])
async def test_controller_requires_manual_code_for_known_device(tmp_path: Path, code):
    from desktop.controller_app import ControllerApp
    from desktop.terminal_commands import parse_command

    app = ControllerApp(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 36)):
        client = AsyncMock()
        client.nodes.return_value = []
        client.approvals.return_value = []
        app.client = client
        app.capabilities = {"can_approve": True}
        app.requests = [
            {"request_id": "request", "name": "device", "node_id": "node_test", "known": True}
        ]
        if code is None:
            with pytest.raises(ValueError):
                parse_command("/approve 1")
            client.approve.assert_not_called()
        else:
            await app.execute(parse_command(f"/approve 1 {code}"))
            client.approve.assert_awaited_once_with("request", True, code)


async def test_terminal_frontends_open_without_network(tmp_path: Path):
    from desktop.agent_app import AgentApp
    from desktop.controller_app import ControllerApp

    for app_type in (ControllerApp, AgentApp):
        app = app_type(tmp_path / app_type.__name__ / ".env", autoconnect=False)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause()
            assert app.title.startswith("Kintler") and app.theme == "kintler"


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop bridge")
def test_bridge_background_jobs():
    from desktop.ui import Bridge

    bridge = Bridge()

    async def work():
        await asyncio.sleep(0.01)
        return {"ok": True}

    try:
        assert bridge.submit(work()).result(timeout=5) == {"ok": True}
        assert bridge.events.get(timeout=5) == ("done", {"ok": True})
    finally:
        bridge.stop()
        bridge.thread.join(timeout=5)
