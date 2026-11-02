import base64
import io
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from agent.kintler_agent import Agent, AgentSettings
from agent.modules.desktop import open_application
from client.kintler_sdk import KintlerClient
from client.mcp_server import desktop, open, screenshot
from common.activity import describe_action
from common.protocol import SAFE_TOOLS, ProtocolError
from desktop.terminal_commands import parse_command
from tests.conftest import Environment


def context(client):
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(client=client))
    )


@pytest.mark.parametrize("prefix", ["/open", "open"])
def test_operator_open_uses_literal_arguments(prefix):
    invocation = parse_command(f'{prefix} "C:\\Program Files\\App\\app.exe" "Привет & мир" --flag')
    assert invocation.name == "open"
    assert invocation.args == {
        "app": "C:\\Program Files\\App\\app.exe",
        "arguments": ["Привет & мир", "--flag"],
    }


def test_operator_desktop_keeps_json_and_headed_policy():
    invocation = parse_command('/desktop mouse-click {"x":10,"y":20,"headed":true}')
    assert invocation.args == {
        "action": "mouse-click",
        "params": {"x": 10, "y": 20},
        "headed": True,
    }
    for value in (
        "/desktop snapshot []",
        "/desktop snapshot {bad}",
        '/desktop click {"headed":"true"}',
        "/open",
    ):
        with pytest.raises(ValueError):
            parse_command(value)


async def test_mcp_open_and_desktop_forward_only_selected_device():
    client = AsyncMock()
    client.call.return_value = {"ok": True}
    assert (await open("node_fixture", "app.exe", context(client), ["a & b"]))["ok"]
    client.call.assert_awaited_once_with(
        "node_fixture", "open", {"app": "app.exe", "arguments": ["a & b"]}
    )
    client.call.reset_mock()
    await desktop("node_fixture", "click", context(client), {"ref_id": "@sfixture:e1"}, True, 30)
    client.call.assert_awaited_once_with(
        "node_fixture",
        "desktop",
        {"action": "click", "params": {"ref_id": "@sfixture:e1"}, "headed": True},
        30,
    )


async def test_new_tools_do_not_expand_safe_default_or_bypass_local_policy(tmp_path):
    assert not {"open", "desktop"} & SAFE_TOOLS
    agent = Agent(AgentSettings("http://localhost", "fixture", "fixture", root=tmp_path))
    for tool in ("open", "desktop"):
        with pytest.raises(ProtocolError, match="disabled"):
            await agent.invoke(tool, {}, "session", AsyncMock(), 10)


async def test_open_and_desktop_use_their_own_backend_acl(environment: Environment):
    async with KintlerClient(environment.restricted) as client:
        for tool in ("open", "desktop"):
            with pytest.raises(ProtocolError, match="denied"):
                await client.call("node_a", tool, {"action": "help"})


async def test_old_agent_is_not_sent_unknown_desktop_commands(environment: Environment):
    node = environment.service.registry.nodes["node_a"]
    node.metadata["tools"] = [
        tool for tool in node.metadata["tools"] if tool not in {"open", "desktop"}
    ]
    commands = dict(environment.service.router.commands)
    async with KintlerClient(environment.operator) as client:
        for tool in ("open", "desktop"):
            with pytest.raises(ProtocolError, match="update"):
                await client.call("node_a", tool, {"action": "help"})
    assert node.ready and environment.service.router.commands == commands


async def test_source_agent_dispatch_preserves_session_and_timeout(tmp_path, monkeypatch):
    settings = AgentSettings(
        "http://localhost",
        "fixture",
        "fixture",
        root=tmp_path,
        tools=frozenset({"desktop"}),
        native_agent=tmp_path / "KintlerAgent.exe",
    )
    agent = Agent(settings)
    operate = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("agent.kintler_agent.desktop.operate", operate)
    args = {"action": "help"}
    assert (await agent.invoke("desktop", args, "session_fixture", AsyncMock(), 12))["ok"]
    operate.assert_awaited_once_with(
        args, agent.transfers.root, "session_fixture", 12, settings.native_agent
    )
    agent.settings = replace(settings, tools=SAFE_TOOLS)
    with pytest.raises(ProtocolError, match="disabled"):
        await agent.invoke("desktop", args, "session_fixture", AsyncMock(), 12)
    assert operate.await_count == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows launch adapter")
def test_source_windows_open_does_not_interpolate_shell(tmp_path, monkeypatch):
    start = Mock()
    monkeypatch.setattr(os, "startfile", start)
    result = open_application({"app": "notepad.exe", "arguments": ["Привет & мир"]}, tmp_path)
    assert result["accepted"] and result["verification"] == "launch_requested"
    start.assert_called_once_with("notepad.exe", "open", '"Привет & мир"', str(tmp_path))


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"app": "\0"},
        {"app": "app.exe", "arguments": "a"},
        {"app": "app.exe", "arguments": [1]},
        {"app": "app.exe", "binary": "other"},
    ],
)
def test_invalid_open_never_starts_a_process(tmp_path, monkeypatch, args):
    start = Mock()
    monkeypatch.setattr(os, "startfile", start, raising=False)
    with pytest.raises(ProtocolError):
        open_application(args, tmp_path)
    start.assert_not_called()


async def test_mcp_screen_is_an_image_and_controller_temporary_is_removed():
    client = AsyncMock()
    client.resolve.return_value = "node_fixture"
    client.checked.return_value = {"ok": True, "path": "remote.png", "left": -100, "top": 20}
    paths = []

    async def download(node, remote, local):
        assert (node, remote) == ("node_fixture", "remote.png")
        paths.append(Path(local))
        Image.new("RGB", (2000, 1000), "black").save(local)
        return {"ok": True}

    client.download.side_effect = download
    result = await screenshot("fixture", context(client))
    assert isinstance(result, list) and result[1].type == "image"
    with Image.open(io.BytesIO(base64.b64decode(result[1].data))) as image:
        assert image.size == (1600, 800)
    assert '"origin": [-100, 20]' in result[0].text
    assert '"scale_to_device": [1.25, 1.25]' in result[0].text
    assert all(not path.exists() and not path.parent.exists() for path in paths)


async def test_explicit_screenshot_destination_keeps_png_download_contract(tmp_path):
    client = AsyncMock()
    client.resolve.return_value = "node_fixture"
    client.checked.return_value = {"ok": True, "path": "remote.png"}
    client.download.return_value = {"ok": True}
    destination = str(tmp_path / "kept.png")
    result = await screenshot("fixture", context(client), destination, 0)
    assert result == {"ok": True}
    client.checked.assert_awaited_once_with("node_fixture", "screenshot", {"monitor": 0})
    client.download.assert_awaited_once_with("node_fixture", "remote.png", destination)


def test_desktop_activity_never_contains_typed_text_or_launch_arguments():
    assert "private-text" not in describe_action(
        "desktop", {"action": "type", "params": {"text": "private-text"}}
    )
    assert "private-token" not in describe_action(
        "open", {"app": "app.exe", "arguments": ["private-token"]}
    )
