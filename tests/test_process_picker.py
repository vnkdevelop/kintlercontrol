import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from textual.widgets import Button, Input, OptionList

from common.protocol import ProtocolError
from desktop.terminal_commands import parse_command
from desktop.terminal_controller import ProcessPicker, TerminalController
from tests.test_controller_commands import client_for

PROCESSES = [
    {"pid": 300, "name": "zabor.exe", "created": 123.75},
    {"pid": 200, "name": "notepad.exe", "created": 123.5},
    {"pid": 100, "name": "notepad.exe", "created": 123.25},
]


async def rendered_picker(app, pilot):
    await pilot.pause()
    dialog = app.screen
    assert isinstance(dialog, ProcessPicker)
    rendered = asyncio.Event()
    dialog.call_after_refresh(rendered.set)
    await asyncio.wait_for(rendered.wait(), 5)
    return dialog


@pytest.mark.parametrize("force", [False, True])
def test_kill_parser_supports_picker_and_keeps_explicit_identity(force):
    suffix = " --force" if force else ""
    assert parse_command("/kill" + suffix).args == {"force": force}
    assert parse_command("/kill 100 123.25" + suffix).args == {
        "pid": 100,
        "created": 123.25,
        "force": force,
    }
    with pytest.raises(ValueError, match="/help kill"):
        parse_command("/kill 100" + suffix)


@pytest.mark.parametrize("size", [(80, 24), (110, 36)])
@pytest.mark.parametrize("force", [False, True])
async def test_kill_lists_names_and_selects_exact_duplicate_process(tmp_path: Path, size, force):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=size) as pilot:
        client = client_for(app)
        client.checked.return_value = {"ok": True, "processes": PROCESSES}
        app.query_one("#prompt", Input).value = "/kill" + (" --force" if force else "")
        await pilot.press("enter")
        await rendered_picker(app, pilot)
        assert isinstance(app.screen, ProcessPicker)
        client.checked.assert_awaited_once_with("node_fixture", "ps", {"sort": "pid", "limit": 500})
        client.call.assert_not_awaited()
        options = app.screen.query_one("#process-list", OptionList)
        assert [options.get_option_at_index(index).prompt.plain for index in range(3)] == [
            "notepad.exe",
            "notepad.exe",
            "zabor.exe",
        ]
        assert len({options.get_option_at_index(index).id for index in range(3)}) == 3
        buttons = list(app.screen.query(Button))
        assert all(button.region.bottom <= size[1] for button in buttons)
        assert options.region.height >= 3
        await pilot.press("down", "enter")
        await pilot.pause()
        client.call.assert_awaited_once()
        assert client.call.call_args.args[:3] == (
            "node_fixture",
            "kill",
            {"pid": 200, "created": 123.5, "force": force},
        )
        assert not app.screen.is_modal


async def test_kill_search_filters_case_insensitively_and_handles_no_matches(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)) as pilot:
        client = client_for(app)
        client.checked.return_value = {"ok": True, "processes": PROCESSES}
        app.dispatch("/kill")
        await rendered_picker(app, pilot)
        dialog = app.screen
        assert isinstance(dialog, ProcessPicker)
        entry = dialog.query_one("#process-search", Input)
        options = dialog.query_one("#process-list", OptionList)
        entry.value = "missing"
        await pilot.pause()
        assert dialog.query_one("#process-kill", Button).disabled
        assert options.get_option_at_index(0).disabled
        await pilot.press("enter")
        client.call.assert_not_awaited()
        assert app.screen is dialog
        entry.value = "ZABOR"
        await pilot.pause()
        assert options.option_count == 1
        assert options.get_option_at_index(0).prompt.plain == "zabor.exe"
        assert not dialog.query_one("#process-kill", Button).disabled
        await pilot.click("#process-kill")
        await pilot.pause()
        assert client.call.call_args.args[2] == {
            "pid": 300,
            "created": 123.75,
            "force": False,
        }


@pytest.mark.parametrize("cancel", ["escape", "ctrl+c", "button"])
async def test_kill_picker_cancels_without_termination(tmp_path: Path, cancel):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)) as pilot:
        client = client_for(app)
        client.checked.return_value = {"ok": True, "processes": PROCESSES}
        worker = app.dispatch("/kill")
        await rendered_picker(app, pilot)
        assert isinstance(app.screen, ProcessPicker)
        if cancel == "button":
            assert await pilot.click("#process-cancel", offset=(8, 1))
        else:
            await pilot.press(cancel)
        await pilot.pause()
        assert not app.screen.is_modal
        client.call.assert_not_awaited()
        if cancel == "ctrl+c":
            assert worker.state.name == "CANCELLED"


async def test_kill_picker_list_focus_and_scroll_work(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)) as pilot:
        client = client_for(app)
        rows = [
            {"pid": 100 + index, "name": f"process-{index:03}.exe", "created": 123.25}
            for index in range(40)
        ]
        client.checked.return_value = {"ok": True, "processes": rows}
        app.dispatch("/kill")
        await rendered_picker(app, pilot)
        options = app.screen.query_one("#process-list", OptionList)
        options.focus()
        await pilot.pause()
        await pilot.press("end")
        assert options.highlighted == 39
        assert options.scroll_y > 0
        await pilot.press("enter")
        await pilot.pause()
        assert client.call.call_args.args[2]["pid"] == 139


def test_picker_excludes_processes_without_a_safe_identity_and_renders_names_literally():
    rows = [
        {"pid": 100, "name": "[red]literal.exe", "created": 123.25},
        {"pid": 101, "name": None, "created": 123.25},
        {"pid": 102, "name": "", "created": 123.25},
        {"pid": 103, "name": "unknown.exe", "created": None},
        {"pid": 104, "name": "unknown.exe", "created": float("nan")},
        {"pid": 105, "name": "unknown.exe", "created": float("inf")},
        {"pid": 106, "name": "unknown.exe", "created": True},
        {"pid": 107, "name": "unknown.exe", "created": 0},
        {"pid": True, "name": "unknown.exe", "created": 123.25},
        {"pid": 1, "name": "protected", "created": 123.25},
    ]
    picker = ProcessPicker(rows, "fixture")
    assert list(picker.processes.values()) == [rows[0]]


async def test_kill_picker_click_selects_literal_name_without_retry_on_pid_reuse(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)) as pilot:
        client = client_for(app)
        row = dict(PROCESSES[0], name="[red]literal.exe")
        client.checked.return_value = {"ok": True, "processes": [row]}
        client.call.return_value = {"ok": False, "error": "PID has been reused"}
        app.dispatch("/kill")
        await rendered_picker(app, pilot)
        options = app.screen.query_one("#process-list", OptionList)
        assert options.get_option_at_index(0).prompt.plain == row["name"]
        assert await pilot.click("#process-list", offset=(2, 1))
        await pilot.pause()
        assert not app.screen.is_modal
        client.call.assert_awaited_once()
        assert client.call.call_args.args[2] == {
            "pid": row["pid"],
            "created": row["created"],
            "force": False,
        }


async def test_explicit_kill_does_not_require_process_listing_permission(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        client = client_for(app)
        app.capabilities["tools"] = ["kill"]
        app.nodes[app.target]["tools"] = ["kill"]
        await app.execute(parse_command("/kill 100 123.25"))
        client.checked.assert_not_awaited()
        client.call.assert_awaited_once()
        assert client.call.call_args.args[2] == {"pid": 100, "created": 123.25, "force": False}


@pytest.mark.parametrize("tool", ["ps", "kill"])
@pytest.mark.parametrize("scope", ["operator", "device"])
async def test_kill_picker_checks_both_permissions_before_listing(tmp_path: Path, tool, scope):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        client = client_for(app)
        rights = app.capabilities if scope == "operator" else app.nodes[app.target]
        rights["tools"] = [name for name in rights["tools"] if name != tool]
        with pytest.raises(ValueError, match="Инструмент отключён"):
            await app.execute(parse_command("/kill"))
        client.checked.assert_not_awaited()
        client.call.assert_not_awaited()


@pytest.mark.parametrize("change", ["target", "client", "permission", "offline"])
async def test_kill_picker_rechecks_target_connection_and_rights(tmp_path: Path, change):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        client = client_for(app)
        client.checked.return_value = {"ok": True, "processes": PROCESSES}

        async def choose(picker):
            if change == "target":
                app.target = "node_other"
            elif change == "client":
                app.client = AsyncMock()
            elif change == "permission":
                app.capabilities["tools"] = ["ps"]
            else:
                app.nodes[app.target]["online"] = False
            return PROCESSES[0]

        app.push_screen_wait = AsyncMock(side_effect=choose)
        with pytest.raises(ValueError):
            await app.execute(parse_command("/kill"))
        client.call.assert_not_awaited()


async def test_kill_picker_target_switch_cancels_open_picker(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)) as pilot:
        client = client_for(app)
        client.checked.return_value = {"ok": True, "processes": PROCESSES}
        other = dict(app.nodes[app.target], id="node_other", name="other")
        client.nodes.return_value.append(other)
        worker = app.dispatch("/kill")
        await rendered_picker(app, pilot)
        assert isinstance(app.screen, ProcessPicker)
        await app.execute(parse_command("/use other"))
        await pilot.pause()
        assert app.target == "node_other"
        assert not app.screen.is_modal
        assert worker.state.name == "CANCELLED"
        client.call.assert_not_awaited()


async def test_kill_picker_discards_list_if_target_changes_during_request(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        client = client_for(app)

        async def listing(*args):
            app.target = "node_other"
            return {"ok": True, "processes": PROCESSES}

        client.checked.side_effect = listing
        with pytest.raises(ValueError, match="Цель изменилась"):
            await app.execute(parse_command("/kill"))
        assert not app.screen.is_modal
        client.call.assert_not_awaited()


async def test_kill_picker_handles_empty_list_and_remote_listing_error(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        client = client_for(app)
        client.checked.return_value = {"ok": True, "processes": []}
        await app.execute(parse_command("/kill"))
        assert not app.screen.is_modal
        client.call.assert_not_awaited()
        client.checked.side_effect = ProtocolError("Process listing denied")
        with pytest.raises(ProtocolError, match="listing denied"):
            await app.execute(parse_command("/kill"))
        assert not app.screen.is_modal
        client.call.assert_not_awaited()
