from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Button, Input, OptionList

from client.kintler_sdk import KintlerClient
from common.protocol import TOOLS
from desktop.terminal_commands import COMMANDS, parse_command
from desktop.terminal_controller import FileArguments, PairingDialog, TerminalController
from tests.conftest import Environment


def client_for(app, admin=False, platform="Windows"):
    client = AsyncMock()
    client.request.return_value = {"status": "ok"}
    node = {
        "id": "node_fixture",
        "name": "fixture",
        "online": True,
        "admin": admin,
        "platform": platform,
        "tools": sorted(TOOLS),
    }
    client.nodes.return_value = [node]
    client.tools.return_value = {"tools": sorted(TOOLS), "can_approve": True}
    client.approvals.return_value = []
    client.call.return_value = {"ok": True}
    client.upload.return_value = {"ok": True}
    client.download.return_value = {"ok": True, "path": "downloaded"}
    client.checked.return_value = {"ok": True, "path": "shot.png"}
    app.client = client
    app.backend_healthy = True
    app.nodes = {node["id"]: node}
    app.target = node["id"]
    app.capabilities = client.tools.return_value
    return client


REMOTE = [
    ("/shell echo fixture", "shell"),
    ("/sysinfo", "sysinfo"),
    ("/ps --sort pid --limit 3", "ps"),
    ("/kill 1234 123.5", "kill"),
    ("/registry query HKCU\\Software", "registry"),
    ("/registry set HKCU\\Software --name fixture --value test", "registry"),
    ("/services list", "services"),
    ("/services status fixture", "services"),
    ("/services start fixture", "services"),
    ("/services stop fixture", "services"),
    ("/tasks list", "tasks"),
    ('/tasks create --name fixture --command "cmd /c echo test"', "tasks"),
]


@pytest.mark.parametrize("command,tool", REMOTE)
async def test_remote_command_routes_without_confirmation(tmp_path: Path, command, tool):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)):
        client = client_for(app, admin=True)
        await app.execute(parse_command(command))
        client.call.assert_awaited_once()
        assert client.call.call_args.args[:2] == ("node_fixture", tool)
        assert not app.screen.is_modal


@pytest.mark.parametrize(
    "command",
    [
        "/registry set HKCU\\Software --value fixture",
        "/services start fixture",
        "/services stop fixture",
        '/tasks create --name fixture --command "cmd /c echo fixture"',
    ],
)
async def test_removing_confirmation_does_not_bypass_admin(tmp_path: Path, command):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)):
        client = client_for(app)
        with pytest.raises(ValueError, match="Admin/root"):
            await app.execute(parse_command(command))
        client.call.assert_not_awaited()


async def test_services_restart_is_linux_only(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)):
        client = client_for(app, admin=True)
        with pytest.raises(ValueError, match="Linux"):
            await app.execute(parse_command("/services restart fixture"))
        app.nodes["node_fixture"]["platform"] = "Linux"
        await app.execute(parse_command("/services restart fixture"))
        client.call.assert_awaited_once()


@pytest.mark.parametrize("size", [(80, 24), (110, 36)])
async def test_use_dropdown_filters_offline_and_selects_exact_id(tmp_path: Path, size):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=size) as pilot:
        client = client_for(app)
        nodes = [
            {"id": "node_one", "name": "same", "online": True},
            {"id": "node_offline", "name": "offline", "online": False},
            {"id": "node_two", "name": "same", "online": True},
        ]
        client.nodes.return_value = nodes
        app.nodes = {node["id"]: node for node in nodes}
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/use"
        await pilot.pause()
        options = app.query_one("#suggestions", OptionList)
        assert options.option_count == 2
        assert {options.get_option_at_index(index).id for index in range(2)} == {
            "node_one",
            "node_two",
        }
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.target == "node_two"
        assert not app.screen.is_modal


async def test_use_choices_refresh_without_resetting_highlight(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)) as pilot:
        client = client_for(app)
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/use "
        await pilot.pause()
        options = app.query_one("#suggestions", OptionList)
        assert options.option_count == 1
        new_node = dict(app.nodes["node_fixture"], id="node_new", name="new")
        client.nodes.return_value.append(new_node)
        await app.refresh_remote()
        assert options.option_count == 2
        assert options.get_option_at_index(options.highlighted).id == "node_fixture"


async def test_file_commands_supply_defaults_and_create_destination_folder(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    viewer = Mock()
    monkeypatch.setattr("desktop.terminal_controller.open_image", viewer)
    source = tmp_path / "source.bin"
    source.write_bytes(b"fixture")
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)):
        client = client_for(app)
        await app.execute(parse_command(f'/upload "{source}"'))
        client.upload.assert_awaited_once_with("node_fixture", source, "source.bin", False)
        await app.execute(parse_command("/download remote.bin"))
        destination = tmp_path / "Downloads/KintlerControl/remote.bin"
        client.download.assert_awaited_once_with("node_fixture", "remote.bin", destination, False)
        assert destination.parent.is_dir()
        client.download.reset_mock()
        await app.execute(parse_command("/screenshot"))
        client.checked.assert_awaited_once_with("node_fixture", "screenshot", {"monitor": 1})
        args = client.download.call_args.args
        assert args[0:2] == ("node_fixture", "shot.png")
        assert args[2].parent == tmp_path / "Pictures/KintlerControl"
        assert args[2].suffix == ".png" and args[2].parent.is_dir()
        viewer.assert_called_once_with(args[2])


async def test_upload_missing_path_opens_arguments_not_confirmation(tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"fixture")
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)) as pilot:
        client = client_for(app)
        app.dispatch("/upload")
        await pilot.pause()
        assert isinstance(app.screen, FileArguments)
        app.screen.query_one("#file-local", Input).value = str(source)
        await pilot.press("enter")
        await pilot.pause()
        client.upload.assert_awaited_once_with("node_fixture", source, "source.bin", False)


async def test_controls_do_not_render_unsupported_partial_block_borders(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)) as pilot:
        await app.push_screen(PairingDialog([{"request_id": "req_fixture", "name": "fixture"}]))
        await pilot.pause()
        for widget in [app.screen.query_one("#pairing-code", Input), *app.screen.query(Button)]:
            assert all(
                edge[0] == "solid"
                for edge in (
                    widget.styles.border_top,
                    widget.styles.border_bottom,
                    widget.styles.border_left,
                    widget.styles.border_right,
                )
            )
        rendered = "".join(
            segment.text
            for segment in app.console.render(app.screen._compositor.render_full_update())
        )
        assert not set("▊▔▎▁").intersection(rendered)
        assert "─" in rendered and "│" in rendered


LOCAL = [
    "help",
    "devices",
    "use",
    "requests",
    "approve",
    "deny",
    "permissions",
    "reconnect",
    "profile",
    "setup",
    "mcp",
    "install-skill",
    "shortcut",
    "cancel",
    "disconnect",
    "clear",
    "exit",
]


@pytest.mark.parametrize("name", LOCAL)
async def test_local_command_routes(tmp_path: Path, monkeypatch, name):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(100, 32)) as pilot:
        client = client_for(app)
        request = {"request_id": "req_fixture", "node_id": "node_fixture", "name": "fixture"}
        client.approvals.return_value = [request]
        app.requests = [request]
        connect = Mock()
        monkeypatch.setattr(app, "connect_saved", connect)
        monkeypatch.setattr("desktop.terminal_controller.load_profile", Mock(return_value={}))
        monkeypatch.setattr("desktop.terminal_controller.controller_settings", Mock())
        monkeypatch.setattr(
            "desktop.terminal_controller.create_deployment",
            Mock(
                return_value={
                    "server_kit": str(tmp_path / "kit.zip"),
                    "agent_folder": str(tmp_path / "Agent"),
                }
            ),
        )
        monkeypatch.setattr(
            "desktop.terminal_controller.create_shortcut",
            Mock(return_value=tmp_path / "fixture.lnk"),
        )
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("scripts.install_skill.install", Mock(return_value={"installed": []}))
        command = {
            "use": "/use fixture",
            "approve": "/approve 1 123456",
            "deny": "/deny 1",
            "profile": f'/profile "{tmp_path / ".env"}"',
            "setup": f'/setup https://relay.example.com "{tmp_path / "output"}" --mode existing-proxy',
        }.get(name, "/" + name)
        await app.execute(parse_command(command))
        await pilot.pause()
        if name in {"approve", "deny"}:
            client.approve.assert_awaited_once()
        elif name in {"profile", "reconnect"}:
            connect.assert_called_once()
        elif name == "use":
            assert app.target == "node_fixture"
        elif name in {"disconnect", "exit"}:
            assert app.client is None


def test_every_advertised_command_has_execution_coverage():
    covered = (
        set(LOCAL) | {tool for _, tool in REMOTE} | {"upload", "download", "screenshot", "message"}
    )
    assert {command.name for command in COMMANDS} == covered


def test_missing_parameters_have_readable_help_not_argparse_errors():
    for command in ("/kill", "/registry", "/setup", "/profile", "/ps --limit wrong"):
        with pytest.raises(ValueError) as error:
            parse_command(command)
        assert "required:" not in str(error.value)
        assert "/help" in str(error.value)


async def test_controller_executes_shell_inventory_and_files_against_real_relay(
    environment: Environment, tmp_path: Path
):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(110, 36)):
        messages = []
        app.write = lambda message, *args: messages.append(message)
        client = await KintlerClient(environment.operator).__aenter__()
        app.client = client
        app.capabilities = await client.tools()
        await app.refresh_remote()
        await app.execute(parse_command("/use device-0"))
        await app.execute(parse_command("/sysinfo"))
        await app.execute(parse_command("/ps --limit 3"))
        await app.execute(parse_command("echo CONTROLLER_EXECUTION_OK"))
        source = tmp_path / "source.bin"
        source.write_bytes(b"controller-roundtrip" * 10000)
        await app.execute(parse_command(f'/upload "{source}" remote.bin'))
        destination = tmp_path / "nested" / "received.bin"
        await app.execute(parse_command(f'/download remote.bin "{destination}"'))
        assert source.read_bytes() == destination.read_bytes()
        assert any("CONTROLLER_EXECUTION_OK" in message for message in messages)
        assert not any(message.startswith("Ошибка ·") for message in messages)
        assert not app.screen.is_modal


def test_user_icons_keep_original_pixels_and_multi_size_ico():
    from PIL import Image

    root = Path(__file__).resolve().parents[1] / "desktop/assets"
    for role in ("agent", "controller"):
        with (
            Image.open(root / f"kintler-{role}.png") as source,
            Image.open(root / f"kintler-{role}.ico") as icon,
        ):
            assert source.size == icon.size == (256, 256)
            assert source.convert("RGBA").tobytes() == icon.convert("RGBA").tobytes()
            assert (16, 16) in icon.ico.sizes()
