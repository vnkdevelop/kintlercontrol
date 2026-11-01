import asyncio
import json
import socket
from pathlib import Path

import aiohttp
from dotenv import dotenv_values
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.theme import Theme
from textual.widgets import RichLog, Static

from agent.fingerprint import node_id
from agent.kintler_agent import Agent, AgentSettings
from common.identity import unlock
from common.protocol import SAFE_TOOLS, TOOLS
from desktop.settings import profile
from desktop.terminal_theme import ACCENT, BACKGROUND, FOREGROUND, PRIMARY, SURFACE, big_code


class TerminalAgent(App[None]):
    TITLE = "Kintler · Agent"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("q", "close", "Выход"), ("r", "reconnect", "Подключиться снова")]
    CSS = """
    Screen { background: #090e0b; color: #d8e5db; padding: 2 4; }
    #heading { height: 2; color: #8fcca4; text-style: bold; }
    #state { height: 2; color: #7f9485; }
    #code { height: 1fr; content-align: center middle; color: #8fcca4; }
    #activity { display: none; height: 1fr; background: #090e0b; padding: 1 0; }
    #footer { height: 2; color: #7f9485; }
    Screen.online #code { display: none; }
    Screen.online #activity { display: block; }
    """

    def __init__(self, path: Path | None = None, autoconnect: bool = True):
        super().__init__()
        self.path = path or profile("agent")
        self.autoconnect = autoconnect
        self.connection: asyncio.Task | None = None
        self.register_theme(
            Theme(
                name="kintler",
                primary=PRIMARY,
                accent=ACCENT,
                foreground=FOREGROUND,
                background=BACKGROUND,
                surface=SURFACE,
                panel=SURFACE,
                dark=True,
            )
        )
        self.theme = "kintler"

    def compose(self) -> ComposeResult:
        yield Static("╱  KINTLER / AGENT", id="heading")
        yield Static("Подключаемся · до подтверждения доступа нет", id="state")
        yield Static("Ожидаем код подключения…", id="code", markup=False)
        yield RichLog(id="activity", wrap=True, max_lines=600, markup=False)
        yield Static(
            "Действия оператора и AI отображаются здесь.\nq выход    r новое подключение",
            id="footer",
        )

    def on_mount(self) -> None:
        if self.autoconnect:
            self.start_connection()

    def observe(self, event: str, detail: dict) -> None:
        state = self.query_one("#state", Static)
        if event == "waiting":
            self.screen.remove_class("online")
            self.query_one("#code", Static).update(
                "КОД ДОСТУПА\n\n" + big_code(detail["code"]) + "\n\nВведите 6 цифр в Controller"
            )
            state.update("Доступ закрыт · каждое соединение подтверждается отдельно")
        elif event == "connected":
            self.screen.add_class("online")
            self.query_one("#code", Static).update("")
            state.update("Online · подключение разрешено владельцем")
        elif event in {"denied", "disconnected", "retry"}:
            self.screen.remove_class("online")
            self.query_one("#code", Static).update(
                "Доступ закрыт\n\nr — запросить новое подключение"
            )
            state.update("Нет доступа · новое подключение требует нового кода")
        elif event == "command":
            self.query_one("#activity", RichLog).write(
                Text("› " + detail.get("activity", detail["tool"]), style=FOREGROUND)
            )

    @work(exclusive=True, group="agent")
    async def start_connection(self) -> None:
        try:
            values = {
                key: value for key, value in dotenv_values(self.path).items() if value is not None
            }
            private = (
                unlock(values["KINTLER_AGENT_PRIVATE_KEY"], "agent")
                if values.get("KINTLER_AGENT_PRIVATE_KEY")
                else b""
            )
            secret = values.get("KINTLER_AGENT_SECRET", "")
            if not private and len(secret) < 32:
                raise ValueError("Нужен настроенный профиль Agent")
            tools = frozenset(
                values.get("KINTLER_AGENT_TOOLS", ",".join(sorted(SAFE_TOOLS))).split(",")
            )
            if not tools <= TOOLS:
                raise ValueError("Неизвестный инструмент")
            workspace = Path(values.get("KINTLER_WORKSPACE", "workspace"))
            if not workspace.is_absolute():
                workspace = self.path.parent / workspace
            device = node_id()
            settings = AgentSettings(
                values["KINTLER_URL"],
                values.get("KINTLER_AGENT_ID", device),
                secret,
                device,
                values.get("KINTLER_NODE_NAME", socket.gethostname()),
                workspace,
                tools,
                values.get("KINTLER_ALLOW_LOCAL_HTTP") == "1",
                values.get("KINTLER_CA_FILE"),
                private,
            )
            agent = Agent(settings, self.observe)
            self.connection = asyncio.create_task(agent.run())
            await self.connection
        except asyncio.CancelledError:
            raise
        except (ValueError, KeyError, OSError, aiohttp.ClientError):
            self.query_one("#state", Static).update(
                "Не удалось прочитать профиль или подключиться. Используйте настроенный Agent."
            )

    async def action_reconnect(self) -> None:
        if self.connection and not self.connection.done():
            self.connection.cancel()
            await asyncio.gather(self.connection, return_exceptions=True)
        self.start_connection()

    async def action_close(self) -> None:
        if self.connection and not self.connection.done():
            self.connection.cancel()
            await asyncio.gather(self.connection, return_exceptions=True)
        self.exit()

    async def on_unmount(self) -> None:
        if self.connection and not self.connection.done():
            self.connection.cancel()
            await asyncio.gather(self.connection, return_exceptions=True)


async def self_test(path: Path) -> None:
    app = TerminalAgent(autoconnect=False)
    async with app.run_test(size=(100, 36)) as pilot:
        app.observe("waiting", {"code": "123456"})
        await pilot.pause()
    path.write_text(json.dumps({"ok": True, "role": "agent", "terminal": True}), encoding="utf-8")
