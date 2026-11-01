import asyncio
import contextlib
import json
import secrets
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Key, Paste
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from client.kintler_sdk import KintlerClient
from common.activity import redact
from common.protocol import ProtocolError
from desktop.clipboard import clipboard_text
from desktop.deployment import create_deployment
from desktop.file_viewer import open_image
from desktop.presets import message_command
from desktop.settings import activate_profile, controller_settings, load_profile, profile
from desktop.shortcut import create_shortcut
from desktop.terminal_commands import COMMANDS, SPECS, Invocation, parse_command, printable_result
from desktop.terminal_theme import (
    ACCENT,
    BACKGROUND,
    ERROR,
    FOREGROUND,
    LOGO,
    MUTED,
    PRIMARY,
    SURFACE,
)


class FileArguments(ModalScreen[dict[str, str] | None]):
    DEFAULT_CSS = """
    FileArguments { align: center middle; background: #090e0b 85%; }
    FileArguments > Vertical { width: 90%; max-width: 72; height: auto; max-height: 90%; overflow-y: auto; background: #111b14; border: solid #246943; padding: 1 2; }
    FileArguments Label { height: auto; margin-bottom: 1; }
    FileArguments Horizontal { height: 3; align: right middle; }
    FileArguments Button { margin-left: 1; }
    #file-error { color: #e4978d; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, tool: str, args: dict):
        super().__init__()
        self.tool = tool
        self.args = args

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Параметры /{self.tool}", markup=False)
            if self.tool == "upload":
                yield Label("Файл на этом компьютере (полный путь)")
                yield Input(self.args.get("local_path", ""), id="file-local")
                yield Label("Путь на устройстве; пусто — сохранить под тем же именем")
                yield Input(self.args.get("remote_path", ""), id="file-remote")
            else:
                yield Label("Файл на устройстве (путь внутри workspace)")
                yield Input(self.args.get("remote_path", ""), id="file-remote")
                yield Label("Сохранить на этом ПК; пусто — Загрузки / KintlerControl")
                yield Input(self.args.get("local_path", ""), id="file-local")
            yield Label("", id="file-error", markup=False)
            with Horizontal():
                yield Button("Отмена", id="file-cancel")
                yield Button("Продолжить", variant="success", id="file-submit")

    def on_mount(self) -> None:
        self.query_one("#file-local" if self.tool == "upload" else "#file-remote", Input).focus()

    @on(Input.Submitted)
    def submit_input(self) -> None:
        self.submit()

    @on(Button.Pressed)
    def selected(self, event: Button.Pressed) -> None:
        if event.button.id == "file-cancel":
            self.action_cancel()
        else:
            self.submit()

    def submit(self) -> None:
        local = self.query_one("#file-local", Input).value.strip()
        remote = self.query_one("#file-remote", Input).value.strip()
        if not (local if self.tool == "upload" else remote):
            self.query_one("#file-error", Label).update("Укажите файл для передачи.")
        elif self.tool == "upload" and not Path(local).expanduser().is_file():
            self.query_one("#file-error", Label).update(
                "Локальный файл не найден. Укажите полный путь."
            )
        else:
            self.dismiss({"local_path": local, "remote_path": remote})

    def action_cancel(self) -> None:
        self.dismiss(None)


class PairingDialog(ModalScreen[tuple[str, str] | None]):
    DEFAULT_CSS = """
    PairingDialog { align: center middle; background: #090e0b 85%; }
    PairingDialog > Vertical { width: 90%; max-width: 72; height: auto; max-height: 90%; overflow-y: auto; background: #111b14; border: solid #246943; padding: 1 2; }
    PairingDialog Label { height: auto; margin-bottom: 1; }
    PairingDialog OptionList { height: auto; max-height: 8; margin-bottom: 1; }
    PairingDialog Input { margin-bottom: 1; }
    PairingDialog Horizontal { height: 3; align: right middle; }
    PairingDialog Button { margin-left: 1; }
    #pairing-error { color: #e4978d; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, requests: list[dict], code: str = ""):
        super().__init__()
        self.requests = requests
        self.selected = requests[0]["request_id"] if len(requests) == 1 else None
        self.code = code

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Разрешить подключение устройства")
            yield Label(
                f"Устройство: {self.requests[0]['name']}"
                if self.selected
                else "Выберите нужное устройство ↓ ↑ Enter",
                id="pairing-device",
                markup=False,
            )
            if len(self.requests) > 1:
                yield OptionList(
                    *(
                        Option(Text(f"{index}. {request['name']}"), id=request["request_id"])
                        for index, request in enumerate(self.requests, 1)
                    ),
                    id="pairing-devices",
                )
            yield Label("Введите 6 цифр, показанных в Agent на этом устройстве")
            yield Input(self.code, password=True, id="pairing-code")
            yield Label("", id="pairing-error", markup=False)
            with Horizontal():
                yield Button("Отмена", id="pairing-cancel")
                yield Button("Разрешить доступ", variant="success", id="pairing-approve")

    def on_mount(self) -> None:
        self.query_one("#pairing-code" if self.selected else "#pairing-devices").focus()

    @on(OptionList.OptionSelected, "#pairing-devices")
    def select_device(self, event: OptionList.OptionSelected) -> None:
        self.selected = event.option.id
        request = next(
            request for request in self.requests if request["request_id"] == self.selected
        )
        self.query_one("#pairing-device", Label).update(Text(f"Устройство: {request['name']}"))
        self.query_one("#pairing-code", Input).focus()

    @on(Input.Submitted, "#pairing-code")
    def submit_code(self) -> None:
        self.approve()

    @on(Button.Pressed)
    def selected_button(self, event: Button.Pressed) -> None:
        if event.button.id == "pairing-cancel":
            self.action_cancel()
        else:
            self.approve()

    def approve(self) -> None:
        code = self.query_one("#pairing-code", Input).value.strip()
        if self.selected is None:
            self.query_one("#pairing-error", Label).update(
                "Сначала выберите нужное устройство из списка."
            )
        elif not (len(code) == 6 and code.isascii() and code.isdigit()):
            self.query_one("#pairing-error", Label).update("Нужны ровно 6 цифр из окна Agent.")
            self.query_one("#pairing-code", Input).focus()
        else:
            self.dismiss((self.selected, code))

    def action_cancel(self) -> None:
        self.dismiss(None)


class TerminalController(App[None]):
    TITLE = "Kintler · Controller"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("ctrl+p", "commands", "Команды"),
        ("ctrl+d", "devices", "Устройства"),
        ("ctrl+l", "clear", "Очистить"),
        Binding("ctrl+a", "select_input", "Выделить всё", priority=True),
        Binding("ctrl+v", "paste_clipboard", "Вставить", priority=True),
        Binding("ctrl+shift+v", "paste_clipboard", "Вставить", priority=True),
        Binding("shift+insert", "paste_clipboard", "Вставить", priority=True),
        Binding("ctrl+c", "cancel", "Отменить операцию", priority=True),
        Binding("ctrl+q", "close", "Выход", priority=True),
    ]
    CSS = """
    Screen { background: #090e0b; color: #d8e5db; }
    Input { border: solid #246943 !important; height: 3; padding: 0 1; background: #111b14; color: #d8e5db; }
    Input:focus { background: #182a1e; border: solid #8fcca4 !important; }
    Button { border: solid #246943 !important; height: 3; background: #163322; color: #d8e5db; }
    Button:focus, Button:hover { background: #246943; border: solid #8fcca4 !important; text-style: bold; }
    Button:disabled { background: #111b14; color: #7f9485; }
    OptionList, RichLog, Vertical { scrollbar-size: 0 0; }
    #workspace { height: 1fr; align: center middle; padding: 1 3; }
    #console { width: 100%; max-width: 90; height: auto; }
    #wordmark { color: #8fcca4; height: auto; margin-bottom: 2; }
    #intro { color: #7f9485; height: auto; margin-bottom: 2; }
    #transcript { display: none; height: 1fr; background: #090e0b; border: none; padding: 0 1; }
    #prompt { height: 3; background: #111b14; border: none !important; border-left: thick #246943 !important; padding: 1 2; }
    #prompt:focus { border-left: thick #8fcca4 !important; }
    #suggestions { display: none; height: auto; max-height: 8; background: #111b14; border: none; padding: 0 1; }
    #hint { height: 1; color: #7f9485; margin-top: 1; }
    #status { dock: bottom; height: 2; color: #7f9485; padding: 0 3; }
    Screen.working #workspace { align: center top; }
    Screen.working #console { height: 100%; }
    Screen.working #wordmark, Screen.working #intro { display: none; }
    Screen.commands-open #wordmark, Screen.commands-open #intro { display: none; }
    Screen.working #transcript { display: block; }
    ToastRack { align: right bottom; margin: 0 2 2 0; }
    Toast { background: #111b14; border-left: thick #246943; }
    """

    def __init__(self, path: Path | None = None, autoconnect: bool = True):
        super().__init__()
        self.path = path or profile("controller")
        self.autoconnect = autoconnect
        self.client: KintlerClient | None = None
        self.nodes: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.capabilities: dict = {}
        self.target: str | None = None
        self.refreshing = False
        self.seen_requests: set[str] = set()
        self.jobs: set[asyncio.Task] = set()
        self.closing = False
        self.connecting = False
        self.suggestion_kind = "commands"
        self.device_options: dict[str, dict] = {}
        self.register_theme(
            Theme(
                name="kintler",
                primary=PRIMARY,
                secondary=SURFACE,
                accent=ACCENT,
                foreground=FOREGROUND,
                background=BACKGROUND,
                surface=SURFACE,
                panel=SURFACE,
                error=ERROR,
                success=ACCENT,
                dark=True,
            )
        )
        self.theme = "kintler"

    def compose(self) -> ComposeResult:
        with Container(id="workspace"):
            with Vertical(id="console"):
                yield Static(LOGO, id="wordmark", markup=False)
                yield Static(
                    "Связь с Kintler подключается автоматически.\n/approve — ввести код из Agent    /devices — ваши ПК    /help — команды",
                    id="intro",
                )
                yield RichLog(
                    id="transcript", markup=False, highlight=False, wrap=True, max_lines=2500
                )
                yield Input(placeholder="Введите команду…  /help — помощь", id="prompt")
                yield OptionList(id="suggestions")
                yield Static(
                    "ctrl+p команды    ctrl+d устройства    ctrl+c отмена    ctrl+q выход",
                    id="hint",
                )
        yield Static(
            "Подключение к Kintler…"
            if self.path.is_file()
            else "Первый запуск: /setup — настроить свой Kintler",
            id="status",
        )

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self.set_interval(3, self.refresh_remote)
        if self.autoconnect and self.path.is_file():
            self.connect_saved()

    def write(self, message: str, color: str = FOREGROUND) -> None:
        self.screen.add_class("working")
        self.query_one("#transcript", RichLog).write(Text(redact(message, 10000), style=color))

    def update_status(self) -> None:
        if self.client is None:
            value = (
                "Подключение к Kintler…"
                if self.connecting
                else "Нет связи с Kintler · /reconnect — повторить"
                if self.path.is_file()
                else "Первый запуск: /setup — настроить свой Kintler"
            )
        elif self.target and self.target in self.nodes:
            node = self.nodes[self.target]
            value = f"{node['name']} · {'Online' if node['online'] else 'Offline'} · {'Admin' if node.get('admin') else 'User'}"
        else:
            value = (
                f"Kintler подключён · {len(self.requests)} ожидают доступа · /approve — ввести код"
                if self.requests
                else f"Kintler подключён · {sum(bool(node['online']) for node in self.nodes.values())} ПК доступны · /devices — выбрать ПК"
            )
        self.query_one("#status", Static).update(Text(value, style=MUTED))
        self.query_one("#prompt", Input).placeholder = (
            f"{self.nodes[self.target]['name']} › команда или /help"
            if self.target in self.nodes
            else "Введите команду…  /help — помощь"
        )

    @work(exclusive=True, group="connection")
    async def connect_saved(self) -> None:
        await self.disconnect()
        self.connecting = True
        self.update_status()
        try:
            settings = controller_settings(load_profile(self.path, "controller"))
            client = KintlerClient(settings)
            await client.__aenter__()
            self.client = client
            self.capabilities = await client.tools()
            await self.refresh_remote()
            self.notify(
                "Связь с Kintler установлена. Новый ПК: /approve и код из Agent. Ваши ПК: /devices.",
                title="Kintler",
                timeout=5,
            )
        except (ValueError, OSError, aiohttp.ClientError, ProtocolError, TimeoutError):
            await self.disconnect()
            self.write(
                "Нет связи с Kintler. Проверьте интернет, затем /reconnect — повторить. Настройка нужна только для нового сервера: /setup; другой профиль: /profile.",
                ERROR,
            )
        finally:
            self.connecting = False
            self.update_status()

    async def disconnect(self) -> None:
        await self.cancel_jobs()
        client, self.client = self.client, None
        if client:
            with contextlib.suppress(aiohttp.ClientError, ProtocolError, TimeoutError):
                await client.__aexit__()
        self.target = None
        self.nodes = {}
        self.requests = []

    async def refresh_remote(self) -> None:
        if self.client is None or self.refreshing:
            return
        client = self.client
        self.refreshing = True
        try:
            nodes = {node["id"]: node for node in await client.nodes()}
            requests = await client.approvals() if self.capabilities.get("can_approve") else []
            if self.client is not client:
                return
            self.nodes, self.requests = nodes, requests
            for request in self.requests:
                if request["request_id"] not in self.seen_requests:
                    self.seen_requests.add(request["request_id"])
                    self.notify(
                        f"{request['name']} ожидает разрешения.\n/approve — выбрать устройство и ввести код из Agent",
                        title="Запрос доступа",
                        timeout=12,
                    )
            active = {request["request_id"] for request in self.requests}
            self.seen_requests.intersection_update(active)
            self.update_status()
            entry = self.query_one("#prompt", Input)
            if entry.has_focus and entry.value.lower().lstrip().startswith("/use"):
                self.suggest(Input.Changed(entry, entry.value))
        except (aiohttp.ClientError, ProtocolError, TimeoutError):
            if self.client is client:
                self.query_one("#status", Static).update(
                    "Нет связи с Kintler · проверяем автоматически · /reconnect — повторить сейчас"
                )
        finally:
            self.refreshing = False

    @on(Input.Changed, "#prompt")
    def suggest(self, event: Input.Changed) -> None:
        options = self.query_one("#suggestions", OptionList)
        highlighted = None
        if (
            self.suggestion_kind == "devices"
            and options.option_count
            and options.highlighted is not None
        ):
            highlighted = options.get_option_at_index(options.highlighted).id
        options.clear_options()
        prefix = event.value.lstrip()
        self.device_options = {}
        if prefix.lower() == "/use" or prefix.lower().startswith("/use "):
            self.suggestion_kind = "devices"
            query = prefix[4:].strip().casefold()
            for index, node in enumerate(self.nodes.values(), 1):
                if node["online"] and (
                    not query or query in node["name"].casefold() or str(index) == query
                ):
                    self.device_options[node["id"]] = node
                    options.add_option(
                        Option(
                            Text(
                                f"{index}. {node['name']} · {node.get('platform', '')} · {'Admin' if node.get('admin') else 'User'}"
                            ),
                            id=node["id"],
                        )
                    )
            if not self.device_options:
                options.add_option(
                    Option(
                        Text("Нет доступных ПК. Запустите Agent и подтвердите /approve."),
                        disabled=True,
                    )
                )
            elif highlighted in self.device_options:
                options.highlighted = list(self.device_options).index(highlighted)
            else:
                options.highlighted = 0
            options.display = True
        elif prefix.startswith("/") and " " not in prefix:
            self.suggestion_kind = "commands"
            matches = [
                command for command in COMMANDS if command.name.startswith(prefix[1:].lower())
            ]
            for command in matches:
                options.add_option(
                    Option(
                        Text.assemble(
                            (f"/{command.name:<13}", ACCENT), (command.description, MUTED)
                        ),
                        id=command.name,
                    )
                )
            options.display = bool(matches)
            if matches:
                options.highlighted = 0
        else:
            options.display = False
        self.screen.set_class(options.display, "commands-open")
        self.query_one("#hint", Static).update(
            (
                "↑ ↓ доступные ПК    Enter / Tab выбрать    Esc закрыть"
                if self.suggestion_kind == "devices"
                else f"↑ ↓ все команды ({options.option_count})    Tab выбрать    Esc закрыть"
            )
            if options.display
            else "ctrl+p команды    ctrl+d устройства    ctrl+c отмена    ctrl+q выход"
        )

    @on(OptionList.OptionSelected, "#suggestions")
    def choose_command(self, event: OptionList.OptionSelected) -> None:
        if self.suggestion_kind == "devices":
            self.pick_device(event.option.id)
            return
        entry = self.query_one("#prompt", Input)
        entry.value = f"/{event.option.id} "
        entry.focus()

    def pick_device(self, node_id: str | None) -> None:
        if node_id not in self.device_options:
            return
        self.query_one("#prompt", Input).value = ""
        self.query_one("#suggestions", OptionList).display = False
        self.dispatch(f"/use {node_id}")

    @on(Input.Submitted, "#prompt")
    def submit_command(self, event: Input.Submitted) -> None:
        value = event.value
        suggestions = self.query_one("#suggestions", OptionList)
        if suggestions.display and self.suggestion_kind == "devices" and self.device_options:
            self.pick_device(suggestions.get_option_at_index(suggestions.highlighted or 0).id)
            return
        if (
            suggestions.display
            and value.startswith("/")
            and " " not in value
            and value[1:] not in SPECS
        ):
            index = suggestions.highlighted or 0
            option = suggestions.get_option_at_index(index)
            event.input.value = f"/{option.id} "
            event.input.cursor_position = len(event.input.value)
            return
        event.input.value = ""
        self.query_one("#suggestions", OptionList).display = False
        if not value.strip():
            return
        self.dispatch(value)

    def on_key(self, event: Key) -> None:
        suggestions = self.query_one("#suggestions", OptionList)
        entry = self.query_one("#prompt", Input)
        if not suggestions.display or not entry.has_focus:
            return
        if event.key == "down":
            suggestions.action_cursor_down()
        elif event.key == "up":
            suggestions.action_cursor_up()
        elif event.key == "tab":
            option = suggestions.get_option_at_index(suggestions.highlighted or 0)
            if self.suggestion_kind == "devices":
                self.pick_device(option.id)
            else:
                entry.value = f"/{option.id} "
                entry.cursor_position = len(entry.value)
        elif event.key == "escape":
            suggestions.display = False
            self.screen.remove_class("commands-open")
            self.query_one("#hint", Static).update(
                "ctrl+p команды    ctrl+d устройства    ctrl+c отмена    ctrl+q выход"
            )
        else:
            return
        event.stop()
        event.prevent_default()

    @work(group="command", exit_on_error=False)
    async def dispatch(self, text: str) -> None:
        task = asyncio.current_task()
        if task:
            self.jobs.add(task)
        try:
            invocation = parse_command(text)
            shown = (
                f"/approve {invocation.args['target']} {'••••••' if invocation.args.get('code') else ''}".rstrip()
                if invocation.name == "approve"
                else text
            )
            if invocation.name == "use" and invocation.args.get("target") in self.nodes:
                shown = f"/use {self.nodes[invocation.args['target']]['name']}"
            self.write(f"› {shown}", ACCENT)
            await self.execute(invocation)
        except (ValueError, OSError, ProtocolError) as error:
            self.write(str(error), ERROR)
        except (aiohttp.ClientError, TimeoutError):
            self.write(
                "Связь или срок операции истекли. Проверьте состояние ПК перед повтором изменения.",
                ERROR,
            )
        except Exception as error:
            self.write(
                f"Не удалось выполнить команду ({type(error).__name__}). Проверьте параметры и состояние выбранного ПК; /help — описание команды.",
                ERROR,
            )
        finally:
            if task:
                self.jobs.discard(task)

    def require_client(self) -> KintlerClient:
        if self.client is None:
            raise ValueError(
                "Нет связи с Kintler. /reconnect повторяет подключение к сервису; доступ к ПК разрешается через /approve."
            )
        return self.client

    def require_target(self, tool: str, args: dict) -> tuple[KintlerClient, str, dict]:
        client = self.require_client()
        if self.target not in self.nodes:
            raise ValueError("Выберите устройство: /devices, затем /use имя")
        node = self.nodes[self.target]
        if not node["online"]:
            raise ValueError(
                "Устройство Offline. Запустите Agent и подтвердите новый код: /requests"
            )
        if tool not in self.capabilities.get("tools", []) or tool not in node.get("tools", []):
            raise ValueError("Инструмент отключён владельцем или не разрешён вашему профилю")
        if (
            tool in {"registry", "services", "tasks"}
            and args.get("operation", "list") not in {"list", "status", "query"}
            and not node.get("admin")
        ):
            raise ValueError(
                "Для этого инструмента нужны права Admin/root на устройстве. Обычный User не повышается автоматически."
            )
        return client, self.target, node

    def request_for(self, target: str) -> dict:
        if target.isdigit() and 1 <= int(target) <= len(self.requests):
            return self.requests[int(target) - 1]
        matches = [
            request
            for request in self.requests
            if request["node_id"] == target or request["name"].casefold() == target.casefold()
        ]
        if len(matches) != 1:
            raise ValueError("Запрос не найден или имя неоднозначно. Обновите /requests")
        return matches[0]

    async def execute(self, invocation: Invocation) -> None:
        name, args = invocation.name, dict(invocation.args)
        if name == "help":
            commands = [SPECS[args["target"]]] if args.get("target") in SPECS else COMMANDS
            for command in commands:
                self.write(
                    f"/{command.name:<13} {command.description}\n               {command.usage}"
                )
            self.write(
                "Без / обычная строка выполняется как shell на выбранном ПК. /shell --cwd задаёт каталог внутри workspace. Смена ПК отменяет ваши текущие операции.",
                MUTED,
            )
        elif name == "reconnect":
            if self.connecting:
                self.write("Подключение к Kintler уже выполняется.", MUTED)
            else:
                self.write(
                    "Восстанавливаем связь с Kintler по сохранённым настройкам. Это не разрешает доступ к устройствам; для нового ПК используйте /approve.",
                    MUTED,
                )
                self.connect_saved()
        elif name == "profile":
            selected = Path(args["target"]).expanduser().resolve()
            controller_settings(load_profile(selected, "controller"))
            if selected.is_relative_to(profile("controller").parents[2]):
                with contextlib.suppress(ValueError):
                    activate_profile(selected)
            self.path = selected
            self.connect_saved()
        elif name == "setup":
            binaries = Path(
                getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1] / "dist/windows")
            )
            if getattr(sys, "frozen", False):
                binaries /= "embedded"
            result = await asyncio.to_thread(
                create_deployment,
                args["url"],
                Path(args["output"]),
                binaries,
                mode="existing" if args["mode"] == "existing-proxy" else "automatic",
            )
            self.write(
                f"Комплект создан: {result['server_kit']}\nAgent: {result['agent_folder']}\nПрофиль сохранён локально. Деплой выполняйте отдельно, не заменяя чужие службы."
            )
            self.path = profile("controller")
        elif name == "devices":
            self.require_client()
            await self.refresh_remote()
            if not self.nodes:
                self.write(
                    "Устройств пока нет. Запустите настроенный Agent и проверьте /requests.", MUTED
                )
            for index, node in enumerate(self.nodes.values(), 1):
                marker = "●" if node["online"] else "○"
                self.write(
                    f"{index:>2} {marker} {node['name']:<20} {'Online' if node['online'] else 'Offline':<8} {'Admin' if node.get('admin') else 'User'}",
                    ACCENT if node["online"] else MUTED,
                )
        elif name == "use":
            client = self.require_client()
            await self.refresh_remote()
            if self.client is not client:
                raise ValueError("Подключение изменилось. Выберите ПК ещё раз.")
            if not args["target"]:
                self.query_one("#prompt", Input).value = "/use "
                self.query_one("#prompt", Input).focus()
                return
            matches = [
                node
                for node in self.nodes.values()
                if node["id"] == args["target"]
                or node["name"].casefold() == args["target"].casefold()
            ]
            if args["target"].isdigit() and 1 <= int(args["target"]) <= len(self.nodes):
                matches = [list(self.nodes.values())[int(args["target"]) - 1]]
            if len(matches) != 1:
                raise ValueError(
                    "Устройство не найдено или имя неоднозначно. Укажите короткий номер из /devices"
                )
            if not matches[0]["online"]:
                raise ValueError("ПК сейчас недоступен. Запустите Agent и подтвердите /approve.")
            await self.cancel_jobs()
            self.target = matches[0]["id"]
            self.update_status()
            self.write(
                f"Цель: {matches[0]['name']}. Последующие команды адресуются только этому ПК.",
                ACCENT,
            )
        elif name == "requests":
            self.require_client()
            await self.refresh_remote()
            for index, request in enumerate(self.requests, 1):
                self.write(
                    f"{index}. {request['name']} · ожидает разрешения\n   /approve {index} — ввести код из Agent    /deny {index} — отказать"
                )
            if not self.requests:
                self.write("Ожидающих подключений нет.", MUTED)
        elif name in {"approve", "deny"}:
            client = self.require_client()
            if not self.capabilities.get("can_approve"):
                raise ValueError("Профиль не имеет права подтверждать подключения")
            if name == "deny":
                request = self.request_for(args["target"])
                await client.approve(request["request_id"], False)
            else:
                if not args["target"]:
                    await self.refresh_remote()
                pending_request = (
                    self.request_for(args["target"])
                    if args["target"]
                    else self.requests[0]
                    if len(self.requests) == 1
                    else None
                )
                code = args.get("code", "")
                if pending_request is None or not code:
                    requests = [pending_request] if pending_request else list(self.requests)
                    if not requests:
                        self.write(
                            "Нет ожидающих устройств. Запустите настроенный Agent на нужном ПК: он покажет код, а здесь появится запрос.",
                            MUTED,
                        )
                        return
                    decision = await self.push_screen_wait(PairingDialog(requests, code))
                    if decision is None:
                        return
                    request_id, code = decision
                    pending_request = next(
                        request for request in requests if request["request_id"] == request_id
                    )
                if self.client is not client:
                    raise ValueError("Связь изменилась. Повторите /approve для текущего запроса.")
                assert pending_request is not None
                request = pending_request
                try:
                    await client.approve(request["request_id"], True, code)
                except ProtocolError as error:
                    if "Pairing code does not match" in str(error):
                        raise ValueError(
                            "Код не совпал. Проверьте 6 цифр на выбранном ПК и повторите /approve."
                        ) from None
                    if "approval is no longer valid" in str(error):
                        raise ValueError(
                            "Этот запрос уже закрыт или истёк. Получите новый код в Agent (r), затем повторите /approve."
                        ) from None
                    raise
            self.write(
                f"Доступ к {request['name']} разрешён. /devices — выбрать ПК для работы."
                if name == "approve"
                else "Подключение отклонено.",
                ACCENT,
            )
            await self.refresh_remote()
        elif name == "permissions":
            client = self.require_client()
            self.capabilities = await client.tools()
            self.write("Права оператора: " + ", ".join(self.capabilities.get("tools", [])))
            if self.target in self.nodes:
                self.write(
                    "Разрешено устройством: " + ", ".join(self.nodes[self.target].get("tools", []))
                )
        elif name == "mcp":
            config = (
                {
                    "command": str(Path(sys.executable).resolve()),
                    "args": ["--mcp", "--env", str(self.path)],
                }
                if getattr(sys, "frozen", False)
                else {
                    "command": sys.executable,
                    "args": ["-m", "desktop.mcp_entry", "--env", str(self.path)],
                }
            )
            self.write(
                "Локальный MCP stdio. AI-среда запускает процесс сама; ключ не передаётся в конфиг.\n"
                + json.dumps(config, ensure_ascii=False, indent=2)
            )
        elif name == "install-skill":
            from scripts.install_skill import install

            report = await asyncio.to_thread(install, args["host"])
            self.write(
                "Скилл установлен/проверен. MCP подключается по отдельному запросу агенту; доступ к устройствам подтверждает человек.\n"
                + json.dumps(report, ensure_ascii=False, indent=2)
            )
        elif name == "shortcut":
            if not getattr(sys, "frozen", False):
                raise ValueError("Ярлык создаётся для упакованного Controller EXE")
            try:
                self.write(f"Ярлык создан: {create_shortcut(Path(sys.executable))}")
            except FileExistsError:
                self.write("Ярлык уже существует; чужой ярлык не заменён.", MUTED)
        elif name == "cancel":
            await self.cancel_jobs()
            self.write("Текущие операции отменены.", MUTED)
        elif name == "disconnect":
            await self.disconnect()
            self.update_status()
        elif name == "clear":
            self.action_clear()
        elif name == "exit":
            await self.action_close()
        else:
            client, target, node = self.require_target("shell" if name == "message" else name, args)
            if name == "message":
                if not node["platform"].lower().startswith("windows"):
                    raise ValueError(
                        "/message поддерживается на Windows-ПК с открытым рабочим столом."
                    )
                args = message_command(args["text"])
                self.write(
                    "Отправляю сообщение на выбранный ПК. Ожидаю закрытия окна (OK); /cancel — отменить, лимит 120 секунд.",
                    MUTED,
                )
            if name == "shell":
                args["shell"] = args.get("shell") or (
                    "cmd" if node["platform"].lower().startswith("windows") else "bash"
                )
            if (
                name == "services"
                and args["operation"] == "restart"
                and node["platform"].lower().startswith("windows")
            ):
                raise ValueError("Windows: используйте stop/start; restart поддерживается Linux")
            if name in {"upload", "download"} and not args.get(
                "local_path" if name == "upload" else "remote_path"
            ):
                paths = await self.push_screen_wait(FileArguments(name, args))
                if paths is None:
                    return
                args.update(paths)
            if target != self.target or self.client is not client:
                raise ValueError("Цель изменилась; действие не отправлено на другой ПК")
            if name == "upload":
                source = Path(args["local_path"]).expanduser()
                result = await client.upload(
                    target, source, args["remote_path"] or source.name, args["overwrite"]
                )
            elif name == "download":
                destination = (
                    Path(args["local_path"]).expanduser()
                    if args["local_path"]
                    else Path.home()
                    / "Downloads/KintlerControl"
                    / Path(args["remote_path"].replace("\\", "/")).name
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                result = await client.download(
                    target, args["remote_path"], destination, args["overwrite"]
                )
            elif name == "screenshot":
                destination = (
                    Path(args["local_path"]).expanduser()
                    if args["local_path"]
                    else Path.home()
                    / "Pictures/KintlerControl"
                    / f"screenshot-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.png"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                self.write("Запрашиваю снимок выбранного ПК…", MUTED)
                shot = await client.checked(target, "screenshot", {"monitor": args["monitor"]})
                self.write("Снимок получен. Скачиваю PNG…", MUTED)
                result = await client.download(target, shot["path"], destination)
                if not result.get("ok"):
                    raise ProtocolError(result.get("error", "Не удалось скачать снимок."))
                self.write(f"Снимок сохранён: {destination}", ACCENT)
                try:
                    open_image(destination)
                except OSError as error:
                    self.write(
                        f"PNG сохранён, но просмотрщик не открылся: {error}. Откройте файл по указанному пути.",
                        ERROR,
                    )
            else:
                timeout = float(args.pop("timeout", 120))

                async def output(stream: str, data: str) -> None:
                    self.write(data.rstrip("\n"), ERROR if stream == "stderr" else FOREGROUND)

                result = await client.call(
                    target, "shell" if name == "message" else name, args, timeout, output
                )
            self.write(
                f"{'Готово' if result.get('ok') else 'Ошибка'} · {node['name']} · {name}",
                ACCENT if result.get("ok") else ERROR,
            )
            self.write(printable_result(result), MUTED)

    async def cancel_jobs(self) -> None:
        current = asyncio.current_task()
        pending = [task for task in self.jobs if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        while self.screen_stack and isinstance(self.screen, (FileArguments, PairingDialog)):
            self.screen.dismiss(None)

    def action_commands(self) -> None:
        entry = self.query_one("#prompt", Input)
        entry.value = "/"
        entry.focus()

    def action_paste_clipboard(self) -> None:
        entry = self.focused
        if not isinstance(entry, Input) or entry.disabled:
            return
        try:
            text = clipboard_text()
            if text is None and sys.platform != "win32":
                text = self.clipboard
        except OSError as error:
            self.notify(str(error), title="Вставка", severity="warning")
            return
        if text:
            entry.post_message(Paste(text))

    def action_select_input(self) -> None:
        entry = self.focused
        if isinstance(entry, Input) and not entry.disabled:
            entry.action_select_all()

    def action_devices(self) -> None:
        self.dispatch("/devices")

    def action_clear(self) -> None:
        self.query_one("#transcript", RichLog).clear()
        self.screen.remove_class("working")

    async def action_cancel(self) -> None:
        await self.cancel_jobs()
        self.write("Операции отменены.", MUTED)

    async def action_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        await self.disconnect()
        self.exit()

    async def on_unmount(self) -> None:
        await self.disconnect()
