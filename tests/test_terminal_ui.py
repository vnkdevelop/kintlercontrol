import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Input, OptionList, Static

from common.activity import describe_action, redact
from desktop.console import terminal_console
from desktop.terminal_commands import SPECS, parse_command
from desktop.terminal_controller import ConfirmOperation, TerminalController
from desktop.terminal_theme import BACKGROUND, big_code


def test_commands_cover_every_remote_tool():
    assert {
        "shell",
        "sysinfo",
        "ps",
        "kill",
        "upload",
        "download",
        "screenshot",
        "registry",
        "services",
        "tasks",
    } <= set(SPECS)
    assert (
        parse_command('/upload "C:\\work folder\\file.bin" file.bin').args["remote_path"]
        == "file.bin"
    )
    assert parse_command("echo REMOTE_OK").args["command"] == "echo REMOTE_OK"
    assert parse_command('/shell --cwd repo -- echo "A B"').args["command"] == 'echo "A B"'
    assert parse_command("/approve 1 123456").args["code"] == "123456"
    assert parse_command("/setup https://relay.example.com output").args["mode"] == "automatic"
    assert (
        parse_command("/setup https://relay.example.com output --mode existing-proxy").args["mode"]
        == "existing-proxy"
    )
    for value in (
        "/approve 1 12345678",
        "/approve 1 １２３４５６",
        "/ps --limit 501",
        "/tasks create --name task",
    ):
        with pytest.raises(ValueError):
            parse_command(value)


def test_big_code_and_activity_do_not_leak_secrets():
    assert len(big_code("123456").splitlines()) == 10
    message = describe_action(
        "shell", {"command": "set PASSWORD=super-secret && echo ready"}, "mcp"
    )
    assert "AI" in message and "echo ready" in message and "super-secret" not in message
    assert "payload" not in describe_action("upload", {"operation": "chunk", "data": "payload"})
    assert "token-value" not in redact("Authorization=Bearer token-value")
    assert "second-word" not in redact('set "PASSWORD=first-word second-word" && echo ready')
    assert "secret-value" not in redact('tool --api-key "secret-value with spaces"')


async def test_terminal_prompt_palette_and_short_names(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)) as pilot:
        assert app.theme == "kintler"
        assert app.get_theme(app.theme).background == BACKGROUND
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/"
        await pilot.pause()
        assert app.query_one("#suggestions", OptionList).option_count > 0
        app.nodes = {
            "node_long_identifier": {
                "id": "node_long_identifier",
                "name": "laptop",
                "online": True,
                "admin": False,
            }
        }
        app.target = "node_long_identifier"
        app.client = AsyncMock()
        app.update_status()
        assert "laptop" in str(app.query_one("#status", Static).render())
        assert "node_long_identifier" not in str(app.query_one("#status", Static).render())


async def test_manual_approval_and_cancel_are_bound_to_request(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)):
        client = AsyncMock()
        client.nodes.return_value = []
        client.approvals.return_value = []
        app.client = client
        app.capabilities = {"can_approve": True}
        app.requests = [
            {"request_id": "req_test", "node_id": "node_test", "name": "laptop", "known": True}
        ]
        await app.execute(parse_command("/approve 1 123456"))
        client.approve.assert_awaited_once_with("req_test", True, "123456")
        assert app.target is None


async def test_refresh_does_not_restore_disconnected_session(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)):
        client = AsyncMock()

        async def nodes():
            await app.disconnect()
            return [{"id": "old_node", "name": "old", "online": True}]

        client.nodes.side_effect = nodes
        app.client = client
        await app.refresh_remote()
        assert app.client is None
        assert app.nodes == {}
        assert not app.refreshing


async def test_frozen_mcp_uses_selected_profile(tmp_path: Path, monkeypatch):
    app = TerminalController(tmp_path / "selected" / ".env", autoconnect=False)
    messages = []
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(app, "write", messages.append)
    await app.execute(parse_command("/mcp"))
    assert "--env" in messages[0]
    assert str(app.path).replace("\\", "\\\\") in messages[0]


async def test_cancel_dismisses_pending_confirmation(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)) as pilot:
        client = AsyncMock()
        app.client = client
        app.capabilities = {"tools": ["shell"]}
        app.nodes = {
            "node_test": {
                "id": "node_test",
                "name": "test",
                "online": True,
                "platform": "Windows",
                "tools": ["shell"],
            }
        }
        app.target = "node_test"
        worker = app.dispatch("/shell echo TEST_ONLY")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmOperation)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmOperation)
        assert worker.state.name == "CANCELLED"
        client.call.assert_not_awaited()


@pytest.mark.skipif(os.name != "nt", reason="Windows frozen terminal handles")
def test_windowed_terminal_restores_python_and_native_streams(monkeypatch):
    import ctypes
    import msvcrt

    kernel = Mock()
    kernel.GetConsoleWindow.return_value = None
    kernel.GetConsoleMode.return_value = 0
    kernel.AllocConsole.return_value = 1
    kernel.GetStdHandle.return_value = 111
    kernel.SetStdHandle.return_value = 1
    streams = [Mock() for _ in range(3)]
    for index, stream in enumerate(streams, 1):
        stream.fileno.return_value = index
    monkeypatch.setattr(ctypes.windll, "kernel32", kernel)
    monkeypatch.setattr(msvcrt, "get_osfhandle", lambda descriptor: 1000 + descriptor)
    monkeypatch.setattr("desktop.console.open", Mock(side_effect=streams), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "__stdin__", None)
    monkeypatch.setattr(sys, "__stdout__", None)
    monkeypatch.setattr(sys, "__stderr__", None)
    previous = sys.stdin, sys.stdout, sys.stderr
    with terminal_console("Kintler test"):
        assert (sys.stdin, sys.stdout, sys.stderr) == tuple(streams)
        assert (sys.__stdin__, sys.__stdout__, sys.__stderr__) == tuple(streams)
    assert (sys.stdin, sys.stdout, sys.stderr) == previous
    assert (sys.__stdin__, sys.__stdout__, sys.__stderr__) == (None, None, None)
    assert kernel.SetStdHandle.call_count == 6
    for stream in streams:
        stream.close.assert_called_once()
