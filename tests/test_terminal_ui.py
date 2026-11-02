import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Input, Label, OptionList, RichLog, Static

from common.activity import describe_action, display_action, redact
from desktop.console import terminal_console
from desktop.terminal_agent import TerminalAgent
from desktop.terminal_commands import COMMANDS, SPECS, parse_command
from desktop.terminal_controller import PairingDialog, TerminalController
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
    assert parse_command("/approve").args == {"target": "", "code": ""}
    assert parse_command("/approve 1").args == {"target": "1", "code": ""}
    assert parse_command("/approve 123456").args == {"target": "", "code": "123456"}
    assert parse_command("123456").name == "approve"
    assert parse_command("/connect").name == "reconnect"
    assert "connect" not in SPECS and "reconnect" in SPECS
    assert parse_command("/setup https://relay.example.com output").args["mode"] == "automatic"
    assert (
        parse_command("/setup https://relay.example.com output --mode existing-proxy").args["mode"]
        == "existing-proxy"
    )
    for value in (
        "/approve 1 12345678",
        "/approve 1 １２３４５６",
        "/approve 12345678",
        "12345678",
        "１２３４５６",
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
    assert display_action("AI · sysinfo") == display_action("Оператор · sysinfo") == "sysinfo"


@pytest.mark.parametrize("size", [(110, 36), (80, 24)])
async def test_terminal_prompt_palette_and_short_names(tmp_path: Path, size):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=size) as pilot:
        assert app.theme == "kintler"
        assert app.get_theme(app.theme).background == BACKGROUND
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/"
        await pilot.pause()
        options = app.query_one("#suggestions", OptionList)
        assert options.option_count == len(COMMANDS)
        for _ in range(len(COMMANDS) - 1):
            await pilot.press("down")
        assert options.get_option_at_index(options.highlighted).id == COMMANDS[-1].name
        assert options.scroll_y > 0
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
        app.backend_healthy = True
        app.update_status()
        assert app.query_one("#status", Static).content.plain == "Kintler подключён · доступно 1"
        assert "laptop" in prompt.placeholder


async def test_manual_approval_and_cancel_are_bound_to_request(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)):
        client = AsyncMock()
        client.request.return_value = {"status": "ok"}
        client.tools.return_value = {"can_approve": True}
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
        client.request.return_value = {"status": "ok"}
        client.tools.return_value = {"can_approve": True}

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


async def test_shell_sends_immediately_and_remains_cancellable(tmp_path: Path):
    import asyncio

    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)) as pilot:
        client = AsyncMock()
        client.request.return_value = {"status": "ok"}
        client.tools.return_value = {"tools": ["shell"]}
        started = asyncio.Event()

        async def wait_for_command(*args):
            started.set()
            await asyncio.Event().wait()

        client.call.side_effect = wait_for_command
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
        await started.wait()
        await pilot.pause()
        assert not app.screen.is_modal
        client.call.assert_awaited_once()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert worker.state.name == "CANCELLED"


@pytest.mark.parametrize("command", ["/approve", "/approve 1"])
@pytest.mark.parametrize("size", [(110, 36), (80, 24)])
async def test_approval_asks_for_code_and_never_logs_it(tmp_path: Path, command, size):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=size) as pilot:
        client = AsyncMock()
        client.request.return_value = {"status": "ok"}
        client.tools.return_value = {"can_approve": True}
        requests = [
            {"request_id": "req_test", "node_id": "node_test", "name": "laptop", "known": True}
        ]
        client.nodes.return_value = []
        client.approvals.return_value = requests
        app.client = client
        app.capabilities = {"can_approve": True}
        app.requests = requests
        app.dispatch(command)
        await pilot.pause()
        assert isinstance(app.screen, PairingDialog)
        entry = app.screen.query_one("#pairing-code", Input)
        assert entry.password
        entry.value = "12345678"
        await pilot.press("enter")
        assert "6" in str(app.screen.query_one("#pairing-error", Label).render())
        client.approve.assert_not_awaited()
        entry.value = "123456"
        await pilot.press("enter")
        await pilot.pause()
        client.approve.assert_awaited_once_with("req_test", True, "123456")
        assert not isinstance(app.screen, PairingDialog)
        assert all(
            "123456" not in line.text for line in app.query_one("#transcript", RichLog).lines
        )


async def test_code_only_requires_explicit_device_when_requests_are_ambiguous(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)) as pilot:
        client = AsyncMock()
        client.request.return_value = {"status": "ok"}
        client.tools.return_value = {"can_approve": True}
        client.nodes.return_value = []
        client.approvals.return_value = [
            {"request_id": "req_one", "node_id": "node_one", "name": "one"},
            {"request_id": "req_two", "node_id": "node_two", "name": "two"},
        ]
        app.client = client
        app.capabilities = {"can_approve": True}
        app.dispatch("123456")
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, PairingDialog) and dialog.selected is None
        dialog.approve()
        client.approve.assert_not_awaited()
        dialog.query_one("#pairing-devices", OptionList).focus()
        await pilot.press("down", "enter", "enter")
        await pilot.pause()
        client.approve.assert_awaited_once_with("req_two", True, "123456")


async def test_cancel_closes_pairing_without_approval(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)) as pilot:
        client = AsyncMock()
        client.request.return_value = {"status": "ok"}
        client.tools.return_value = {"can_approve": True}
        client.nodes.return_value = []
        client.approvals.return_value = [
            {"request_id": "req_test", "node_id": "node_test", "name": "laptop"}
        ]
        app.client = client
        app.capabilities = {"can_approve": True}
        app.dispatch("/approve")
        await pilot.pause()
        assert isinstance(app.screen, PairingDialog)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not isinstance(app.screen, PairingDialog)
        client.approve.assert_not_awaited()


async def test_agent_shows_only_ordered_actions_after_connection(tmp_path: Path):
    app = TerminalAgent(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 36)) as pilot:
        app.observe("waiting", {"code": "123456"})
        app.observe("connected", {})
        await pilot.pause()
        assert "online" in app.screen.classes
        app.observe("command", {"tool": "sysinfo", "activity": "AI · sysinfo"})
        app.observe("command", {"tool": "shell", "activity": "Оператор · shell echo test"})
        await pilot.pause()
        assert [line.text for line in app.query_one("#activity", RichLog).lines] == [
            "sysinfo",
            "shell echo test",
        ]
        footer = str(app.query_one("#footer", Static).render())
        assert "AI" not in footer and "оператор" not in footer and "журнал" not in footer


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
