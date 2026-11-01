import asyncio
import contextlib
import json
import sys
from pathlib import Path

import aiohttp
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from client.kintler_sdk import KintlerClient
from common.activity import redact
from common.protocol import ProtocolError
from desktop.deployment import create_deployment
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
from desktop.tools import dangerous


class ConfirmOperation(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmOperation { align: center middle; background: #090e0b 85%; }
    ConfirmOperation > Vertical { width: 76; height: auto; background: #111b14; border: solid #246943; padding: 1 2; }
    ConfirmOperation Label { height: auto; margin-bottom: 1; }
    ConfirmOperation Horizontal { height: 3; align: right middle; }
    ConfirmOperation Button { margin-left: 1; }
    """
    BINDINGS = [("escape", "cancel", "Отмена")]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Подтвердите действие на выбранном ПК")
            yield Label(self.message, markup=False)
            with Horizontal():
                yield Button("Отмена", id="no")
                yield Button("Выполнить", variant="success", id="yes")

    @on(Button.Pressed)
    def selected(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class TerminalController(App[None]):
    TITLE = "Kintler · Controller"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("ctrl+p", "commands", "Команды"),
        ("ctrl+d", "devices", "Устройства"),
        ("ctrl+l", "clear", "Очистить"),
        Binding("ctrl+c", "cancel", "Отменить операцию", priority=True),
        Binding("ctrl+q", "close", "Выход", priority=True),
    ]
    CSS = """
    Screen { background: #090e0b; color: #d8e5db; }
    #workspace { height: 1fr; align: center middle; padding: 1 3; }
    #console { width: 100%; max-width: 90; height: auto; }
    #wordmark { color: #8fcca4; height: auto; margin-bottom: 2; }
    #intro { color: #7f9485; height: auto; margin-bottom: 2; }
    #transcript { display: none; height: 1fr; background: #090e0b; border: none; padding: 0 1; }
    #prompt { height: 3; background: #111b14; border: none; border-left: thick #246943; padding: 0 2; }
    #prompt:focus { border-left: thick #8fcca4; }
    #suggestions { display: none; height: auto; max-height: 12; background: #111b14; border: none; padding: 0 1; }
    #hint { height: 1; color: #7f9485; margin-top: 1; }
    #status { dock: bottom; height: 2; color: #7f9485; padding: 0 3; }
    Screen.working #workspace { align: center top; }
    Screen.working #console { height: 100%; }
    Screen.working #wordmark, Screen.working #intro { display: none; }
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
                    "Выберите устройство и работайте командами.\n/devices — список ПК    /help — команды и примеры",
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
        yield Static("Не подключён · /connect или /profile", id="status")

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
            value = "Не подключён · /connect или /profile"
        elif self.target and self.target in self.nodes:
            node = self.nodes[self.target]
            value = f"{node['name']} · {'Online' if node['online'] else 'Offline'} · {'Admin' if node.get('admin') else 'User'}"
        else:
            value = f"Подключено · {sum(bool(node['online']) for node in self.nodes.values())} online · выберите ПК: /use имя"
        self.query_one("#status", Static).update(Text(value, style=MUTED))
        self.query_one("#prompt", Input).placeholder = (
            f"{self.nodes[self.target]['name']} › команда или /help"
            if self.target in self.nodes
            else "Введите команду…  /help — помощь"
        )

    @work(exclusive=True, group="connection")
    async def connect_saved(self) -> None:
        await self.disconnect()
        try:
            settings = controller_settings(load_profile(self.path, "controller"))
            client = KintlerClient(settings)
            await client.__aenter__()
            self.client = client
            self.capabilities = await client.tools()
            await self.refresh_remote()
            self.notify(
                "Профиль подключён. Выберите устройство: /devices", title="Kintler", timeout=5
            )
        except (ValueError, OSError, aiohttp.ClientError, ProtocolError, TimeoutError):
            await self.disconnect()
            self.write(
                "Не удалось подключиться с сохранённым профилем. Проверьте сеть и /profile путь_к_.env. Для нового своего сервера: /setup.",
                ERROR,
            )
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
            for index, request in enumerate(self.requests, 1):
                if request["request_id"] not in self.seen_requests:
                    self.seen_requests.add(request["request_id"])
                    self.notify(
                        f"{request['name']} хочет подключиться.\n/approve {index} <6-значный код из Agent>",
                        title="Запрос доступа",
                        timeout=12,
                    )
            active = {request["request_id"] for request in self.requests}
            self.seen_requests.intersection_update(active)
            self.update_status()
        except (aiohttp.ClientError, ProtocolError, TimeoutError):
            if self.client is client:
                self.query_one("#status", Static).update(
                    "Связь недоступна · /connect для повторного входа"
                )
        finally:
            self.refreshing = False

    @on(Input.Changed, "#prompt")
    def suggest(self, event: Input.Changed) -> None:
        options = self.query_one("#suggestions", OptionList)
        options.clear_options()
        prefix = event.value.lstrip()
        if prefix.startswith("/") and " " not in prefix:
            matches = [
                command for command in COMMANDS if command.name.startswith(prefix[1:].lower())
            ]
            for command in matches[:8]:
                options.add_option(
                    Option(
                        Text.assemble(
                            (f"/{command.name:<13}", ACCENT), (command.description, MUTED)
                        ),
                        id=command.name,
                    )
                )
            options.display = bool(matches)
        else:
            options.display = False

    @on(OptionList.OptionSelected, "#suggestions")
    def choose_command(self, event: OptionList.OptionSelected) -> None:
        entry = self.query_one("#prompt", Input)
        entry.value = f"/{event.option.id} "
        entry.focus()

    @on(Input.Submitted, "#prompt")
    def submit_command(self, event: Input.Submitted) -> None:
        value = event.value
        suggestions = self.query_one("#suggestions", OptionList)
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
            entry.value = f"/{option.id} "
            entry.cursor_position = len(entry.value)
        elif event.key == "escape":
            suggestions.display = False
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
                f"/approve {invocation.args['target']} ••••••"
                if invocation.name == "approve"
                else text
            )
            self.write(f"› {shown}", ACCENT)
            await self.execute(invocation)
        except (ValueError, OSError, ProtocolError) as error:
            self.write(str(error), ERROR)
        except (aiohttp.ClientError, TimeoutError):
            self.write(
                "Связь или срок операции истекли. Проверьте состояние ПК перед повтором изменения.",
                ERROR,
            )
        finally:
            if task:
                self.jobs.discard(task)

    def require_client(self) -> KintlerClient:
        if self.client is None:
            raise ValueError("Сначала подключитесь: /connect")
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
        elif name == "connect":
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
                    f"{index}. {request['name']} · {'знакомый ключ' if request['known'] else 'новый ключ'}\n   /approve {index} <код>  или  /deny {index}"
                )
            if not self.requests:
                self.write("Ожидающих подключений нет.", MUTED)
        elif name in {"approve", "deny"}:
            client = self.require_client()
            if not self.capabilities.get("can_approve"):
                raise ValueError("Профиль не имеет права подтверждать подключения")
            request = self.request_for(args["target"])
            await client.approve(request["request_id"], name == "approve", args.get("code", ""))
            self.write(
                "Подключение разрешено по коду." if name == "approve" else "Подключение отклонено.",
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
            client, target, node = self.require_target(name, args)
            if name == "shell":
                args["shell"] = args.get("shell") or (
                    "cmd" if node["platform"].lower().startswith("windows") else "bash"
                )
            if (
                name == "services"
                and args["operation"] == "restart"
                and node["platform"].lower().startswith("windows")
            ):
                raise ValueError(
                    "Windows: используйте stop/start с подтверждением; restart поддерживается Linux"
                )
            if dangerous(name, args):
                accepted = await self.push_screen_wait(
                    ConfirmOperation(f"{node['name']}\n{name}\n{redact(invocation.raw)}")
                )
                if not accepted:
                    self.write("Операция не отправлена.", MUTED)
                    return
            if target != self.target:
                raise ValueError("Цель изменилась; действие не отправлено на другой ПК")
            if name == "upload":
                result = await client.upload(
                    target, Path(args["local_path"]), args["remote_path"], args["overwrite"]
                )
            elif name == "download":
                result = await client.download(
                    target, args["remote_path"], Path(args["local_path"]), args["overwrite"]
                )
            elif name == "screenshot":
                shot = await client.checked(target, "screenshot", {"monitor": args["monitor"]})
                result = await client.download(target, shot["path"], Path(args["local_path"]))
            else:
                timeout = float(args.pop("timeout", 120))

                async def output(stream: str, data: str) -> None:
                    self.write(data.rstrip("\n"), ERROR if stream == "stderr" else FOREGROUND)

                result = await client.call(target, name, args, timeout, output)
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
        while self.screen_stack and isinstance(self.screen, ConfirmOperation):
            self.screen.dismiss(False)

    def action_commands(self) -> None:
        entry = self.query_one("#prompt", Input)
        entry.value = "/"
        entry.focus()

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
