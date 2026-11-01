import base64
import ctypes
import os
import re
from pathlib import Path
from unittest.mock import Mock

import pytest
from textual.widgets import Input

from desktop.clipboard import clipboard_text
from desktop.presets import message_command
from desktop.terminal_commands import parse_command
from desktop.terminal_controller import PairingDialog, TerminalController
from tests.test_controller_commands import client_for


@pytest.mark.parametrize("key", ["ctrl+v", "ctrl+shift+v", "shift+insert"])
async def test_system_clipboard_paste_inserts_unicode_without_running(
    tmp_path: Path, monkeypatch, key
):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    dispatch = Mock()
    monkeypatch.setattr("desktop.terminal_controller.clipboard_text", lambda: "/shell echo Привет")
    monkeypatch.setattr(app, "dispatch", dispatch)
    async with app.run_test(size=(100, 32)) as pilot:
        entry = app.query_one("#prompt", Input)
        entry.value = "old command"
        await pilot.press("ctrl+a", key)
        await pilot.pause()
        assert entry.value == "/shell echo Привет"
        dispatch.assert_not_called()


async def test_clipboard_paste_also_works_in_code_dialog(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("desktop.terminal_controller.clipboard_text", lambda: "123456")
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)) as pilot:
        await app.push_screen(PairingDialog([{"request_id": "req_test", "name": "fixture"}]))
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert app.screen.query_one("#pairing-code", Input).value == "123456"
        assert app.screen.query_one("#pairing-code", Input).password


@pytest.mark.skipif(os.name != "nt", reason="Windows clipboard API")
def test_clipboard_reads_utf16_and_releases_handles(monkeypatch):
    data = ctypes.create_unicode_buffer("Привет, Windows")
    user, kernel = Mock(), Mock()
    user.OpenClipboard.return_value = 1
    user.IsClipboardFormatAvailable.return_value = 1
    user.GetClipboardData.return_value = 123
    kernel.GlobalSize.return_value = ctypes.sizeof(data)
    kernel.GlobalLock.return_value = ctypes.addressof(data)
    monkeypatch.setattr(ctypes.windll, "user32", user)
    monkeypatch.setattr(ctypes.windll, "kernel32", kernel)
    assert clipboard_text() == "Привет, Windows"
    user.GetClipboardData.assert_called_once_with(13)
    kernel.GlobalUnlock.assert_called_once_with(123)
    user.CloseClipboard.assert_called_once()


def test_message_preserves_russian_quotes_and_cannot_interpolate_commands():
    text = 'Привет! "Текст"; $(Get-Process); одинарная: \' конец'
    invocation = parse_command("/message " + text)
    assert invocation.args["text"] == text
    command = message_command(text)
    assert command["shell"] == "powershell"
    script = command["command"]
    payload = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
    assert base64.b64decode(payload).decode("utf-8") == text
    assert text not in script and "Get-Process" not in script
    assert "MessageBox" in script
    assert "Start-Process" not in script and command["timeout"] == 120
    for invalid in ("", " ", "x" * 2001, "hello\0world"):
        with pytest.raises(ValueError):
            message_command(invalid)


async def test_message_uses_existing_shell_permission_on_exact_target(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)):
        client = client_for(app)
        app.nodes["node_fixture"]["tools"] = ["shell"]
        app.capabilities["tools"] = ["shell"]
        await app.execute(parse_command("/message Привет, устройство!"))
        client.call.assert_awaited_once()
        assert client.call.call_args.args[:2] == ("node_fixture", "shell")
        assert client.call.call_args.args[2]["shell"] == "powershell"
        app.nodes["node_fixture"]["platform"] = "Linux"
        with pytest.raises(ValueError, match="Windows"):
            await app.execute(parse_command("/message Привет"))


async def test_screenshot_reports_progress_and_opens_saved_image(tmp_path: Path, monkeypatch):
    viewer = Mock()
    monkeypatch.setattr("desktop.terminal_controller.open_image", viewer)
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)):
        client = client_for(app)
        destination = tmp_path / "capture.png"
        messages = []
        monkeypatch.setattr(app, "write", lambda message, *args: messages.append(message))
        await app.execute(parse_command(f'/screenshot "{destination}"'))
        viewer.assert_called_once_with(destination)
        assert any("Запрашиваю" in message for message in messages)
        assert any("Снимок сохранён" in message for message in messages)
        client.checked.side_effect = KeyError("sensitive internals")
        app.dispatch(f'/screenshot "{destination}"')
        await app.workers.wait_for_complete()
        assert any("KeyError" in message for message in messages)
        assert not any("sensitive internals" in message for message in messages)


async def test_viewer_failure_does_not_hide_saved_screenshot(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "desktop.terminal_controller.open_image", Mock(side_effect=OSError("viewer unavailable"))
    )
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)):
        client_for(app)
        messages = []
        monkeypatch.setattr(app, "write", lambda message, *args: messages.append(message))
        await app.execute(parse_command(f'/screenshot "{tmp_path / "capture.png"}"'))
        assert any("PNG сохранён, но просмотрщик" in message for message in messages)
