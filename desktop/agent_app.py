import argparse
import asyncio
import json
import logging
import socket
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from agent.fingerprint import is_admin, node_id
from agent.kintler_agent import Agent, AgentSettings
from client.kintler_cli import printable
from common.protocol import SAFE_TOOLS, ProtocolError
from desktop.settings import agent_settings, load_profile, profile, save_profile
from desktop.tools import LABELS
from desktop.ui import Bridge, append, field, style, text_output


class AgentApp:
    def __init__(self, root: tk.Tk, path: Path | None = None):
        self.root = root
        self.path = path or profile("agent")
        self.bridge = Bridge()
        self.connection: Future | None = None
        self.pending_values: dict[str, str] = {}
        self.extra: dict[str, str] = {}
        self.closing = False
        self.generation = 0
        self.id = node_id()
        self.url = tk.StringVar()
        self.principal = tk.StringVar()
        self.secret = tk.StringVar()
        self.name = tk.StringVar(value=socket.gethostname())
        self.workspace = tk.StringVar(value=str(self.path.parent / "workspace"))
        self.status = tk.StringVar(value="Этот ПК ещё не подключён")
        self.enabled = {tool: tk.BooleanVar(value=tool in SAFE_TOOLS) for tool in LABELS}
        root.title("Kintler — управляемый ПК")
        root.geometry(f"780x{min(740, root.winfo_screenheight() - 90)}")
        root.minsize(720, 650)
        style(root)
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="KINTLER / DEVICE", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, textvariable=self.status, style="Status.TLabel", wraplength=700).pack(
            anchor="w", pady=(6, 14)
        )
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        connection_tab = ttk.Frame(tabs, padding=10)
        policy_tab = ttk.Frame(tabs, padding=10)
        tabs.add(connection_tab, text="Подключение")
        tabs.add(policy_tab, text="Разрешения на этом ПК")
        identity = ttk.LabelFrame(connection_tab, text="Идентификатор этого ПК", padding=12)
        identity.pack(fill="x", pady=(0, 14))
        ttk.Label(identity, text=self.id, font=("Consolas", 14)).pack(side="left")
        ttk.Button(identity, text="Скопировать NodeID", command=self.copy_id).pack(side="right")
        ttk.Label(
            connection_tab,
            text="Передайте NodeID владельцу сервера для регистрации.\nЗатем импортируйте отдельный .env этого ПК или введите его ID и ключ.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 12))
        form = ttk.LabelFrame(connection_tab, text="Подключение", padding=12)
        form.pack(fill="x")
        field(form, "HTTPS-сервер", 0, self.url)
        field(form, "ID агента", 1, self.principal)
        field(form, "Ключ этого ПК", 2, self.secret, True)
        field(form, "Имя устройства", 3, self.name)
        field(form, "Каталог обмена файлами", 4, self.workspace)
        ttk.Button(form, text="…", width=3, command=self.choose_workspace).grid(
            row=4, column=2, padx=6
        )
        policy = ttk.LabelFrame(policy_tab, text="Что вы разрешаете делать на этом ПК", padding=12)
        policy.pack(fill="x", pady=14)
        for index, (tool, label) in enumerate(LABELS.items()):
            ttk.Checkbutton(policy, text=label, variable=self.enabled[tool]).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 18), pady=3
            )
        ttk.Button(policy, text="Разрешить все инструменты…", command=self.allow_all).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            policy,
            text="Изменения разрешений применяются после отключения и нового подключения.",
            style="Muted.TLabel",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        privilege = (
            "Admin — административные инструменты доступны при разрешении"
            if is_admin()
            else "User — для реестра/служб/задач запустите EXE от имени администратора"
        )
        ttk.Label(policy_tab, text=privilege, style="Muted.TLabel", wraplength=700).pack(anchor="w")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=14)
        ttk.Button(buttons, text="Импортировать .env", command=self.import_env).pack(side="left")
        self.start_button = ttk.Button(
            buttons, text="Разрешить подключение", style="Accent.TButton", command=self.start
        )
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(
            buttons, text="Отключить", command=self.stop, state="disabled"
        )
        self.stop_button.pack(side="left")
        log = ttk.LabelFrame(outer, text="Активность — без содержимого команд и ключей", padding=5)
        log.pack(fill="both", expand=True)
        self.output = text_output(log, 6)
        self.output.pack(fill="both", expand=True)
        if self.path.exists():
            try:
                self.fill(load_profile(self.path, "agent"))
                root.after(120, self.start)
            except (ValueError, OSError) as exc:
                self.status.set(str(exc))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.pump)

    def copy_id(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.id)

    def choose_workspace(self) -> None:
        path = filedialog.askdirectory(title="Каталог обмена файлами")
        if path:
            self.workspace.set(path)

    def allow_all(self) -> None:
        if messagebox.askyesno(
            "Разрешение владельца ПК",
            "Полный shell имеет права вашей учётной записи. Оператор сможет выполнять команды, получать файлы и снимать экран.\n\nРазрешить все инструменты на этом ПК?",
        ):
            for variable in self.enabled.values():
                variable.set(True)

    def fill(self, values: dict[str, str]) -> None:
        self.url.set(values.get("KINTLER_URL", self.url.get()))
        self.principal.set(values.get("KINTLER_AGENT_ID", ""))
        self.secret.set(values.get("KINTLER_AGENT_SECRET", ""))
        self.name.set(values.get("KINTLER_NODE_NAME") or socket.gethostname())
        self.workspace.set(values.get("KINTLER_WORKSPACE") or str(self.path.parent / "workspace"))
        tools = set(values.get("KINTLER_AGENT_TOOLS", ",".join(SAFE_TOOLS)).split(","))
        for key, variable in self.enabled.items():
            variable.set(key in tools)
        self.extra = {
            key: values[key]
            for key in ("KINTLER_CA_FILE", "KINTLER_ALLOW_LOCAL_HTTP")
            if key in values
        }

    def import_env(self) -> None:
        selected = filedialog.askopenfilename(
            title="Отдельный .env этого ПК", filetypes=[("Конфигурация", "*")]
        )
        if selected:
            try:
                self.fill(load_profile(Path(selected), "agent"))
                if not Path(self.workspace.get()).is_absolute():
                    self.workspace.set(
                        str((Path(selected).parent / self.workspace.get()).resolve())
                    )
                self.status.set("Конфигурация загружена. Нажмите «Разрешить подключение».")
            except (ValueError, OSError) as exc:
                messagebox.showerror("Неверный файл", str(exc))

    def start(self) -> None:
        if self.connection and not self.connection.done():
            return
        values = {
            **self.extra,
            "KINTLER_URL": self.url.get(),
            "KINTLER_AGENT_ID": self.principal.get(),
            "KINTLER_AGENT_SECRET": self.secret.get(),
            "KINTLER_NODE_NAME": self.name.get(),
            "KINTLER_WORKSPACE": self.workspace.get(),
            "KINTLER_AGENT_TOOLS": ",".join(
                tool for tool, value in self.enabled.items() if value.get()
            ),
        }
        try:
            settings = agent_settings(values, self.path)
        except (ValueError, ProtocolError, OSError) as exc:
            messagebox.showerror("Настройки подключения", str(exc))
            return
        self.pending_values = values
        self.generation += 1
        generation = self.generation
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Подключение… Если сервер недоступен, агент повторит попытку.")

        def observer(kind: str, detail: dict) -> None:
            self.bridge.post(
                "agent_event", {"event": kind, "detail": detail, "generation": generation}
            )

        self.connection = self.bridge.submit(self.session(settings, observer), "stopped")

    async def session(self, settings: AgentSettings, observer: Callable[[str, dict], None]) -> None:
        await Agent(settings, observer).run()

    def stop(self) -> None:
        self.generation += 1
        if self.connection:
            self.connection.cancel()
        self.status.set("Отключено. Новые команды не принимаются.")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def handle(self, kind: str, value: Any) -> None:
        if kind == "agent_event" and value["generation"] == self.generation:
            event, detail = value["event"], value["detail"]
            if event == "connected":
                self.status.set("Online — разрешённые операторы могут управлять этим ПК")
                try:
                    save_profile(self.path, self.pending_values, "agent")
                except OSError as exc:
                    append(
                        self.output, "Не удалось сохранить локальный профиль: " + str(exc) + "\n"
                    )
                append(self.output, "Соединение с сервером установлено.\n")
            elif event == "command":
                append(
                    self.output,
                    f"{LABELS.get(detail['tool'], detail['tool'])}  [{detail['command_id']}]\n",
                )
            elif event == "retry":
                self.status.set(
                    "Нет связи. Проверьте сервер, NodeID, ID и ключ. Повторяем подключение."
                )
                append(
                    self.output,
                    "Подключение недоступно: " + printable(detail.get("error", "")) + "\n",
                )
            elif event == "disconnected":
                if self.connection and not self.connection.cancelled():
                    self.status.set("Связь разорвана; ожидается переподключение")
        elif kind == "error":
            self.status.set("Ошибка: " + printable(value))
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")

    def pump(self) -> None:
        if not self.closing:
            self.bridge.drain(self.handle)
            self.root.after(50, self.pump)

    def close(self) -> None:
        self.closing = True
        self.stop()
        self.root.after(100, lambda: self.finish_close(0))

    def finish_close(self, attempt: int) -> None:
        if self.connection is None or self.connection.done() or attempt > 110:
            self.bridge.stop()
            self.root.destroy()
        else:
            self.root.after(100, lambda: self.finish_close(attempt + 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--self-test", type=Path)
    args = parser.parse_args()
    if args.self_test:
        root = tk.Tk()
        root.withdraw()
        style(root)
        root.update()
        root.destroy()
        args.self_test.write_text(
            json.dumps({"ok": bool(node_id()), "role": "agent", "tk": tk.TkVersion}),
            encoding="utf-8",
        )
        return
    if args.headless:
        logging.basicConfig(handlers=[logging.NullHandler()])
        path = args.env or profile("agent")
        asyncio.run(Agent(agent_settings(load_profile(path, "agent"), path)).run())
        return
    root = tk.Tk()
    AgentApp(root, args.env)
    root.mainloop()


if __name__ == "__main__":
    main()
