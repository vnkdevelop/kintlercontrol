import argparse
import asyncio
import contextlib
import json
import subprocess
import sys
import tkinter as tk
from concurrent.futures import Future
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from client.kintler_cli import printable
from client.kintler_sdk import ClientSettings, KintlerClient
from common.protocol import ProtocolError
from desktop.settings import controller_settings, load_profile, profile, save_profile
from desktop.tools import LABELS, PARAMETERS, arguments, dangerous
from desktop.ui import Bridge, append, field, scroll_form, style, text_output


class ControllerApp:
    def __init__(self, root: tk.Tk, path: Path | None = None):
        self.root = root
        self.path = path or profile("controller")
        self.bridge = Bridge()
        self.client: KintlerClient | None = None
        self.poller: Future | None = None
        self.connection: Future | None = None
        self.jobs: list[Future] = []
        self.nodes: dict[str, dict] = {}
        self.selected = ""
        self.generation = 0
        self.closing = False
        self.extra: dict[str, str] = {}
        self.permitted: set[str] = set()
        self.can_approve = False
        self.requests: dict[str, dict] = {}
        self.parameters: dict[str, tk.StringVar | tk.Text] = {}
        self.tool_keys = list(LABELS)
        self.url = tk.StringVar()
        self.principal = tk.StringVar()
        self.secret = tk.StringVar()
        self.status = tk.StringVar(value="Войдите в учётную запись оператора")
        self.target = tk.StringVar(value="Выберите подключённый ПК слева")
        self.tool = tk.StringVar(value=LABELS["sysinfo"])
        self.action_hint = tk.StringVar()
        root.title("Kintler — управляющий ПК")
        root.geometry(f"1180x{min(860, root.winfo_screenheight() - 90)}")
        root.minsize(960, 680)
        style(root)
        outer = ttk.Frame(root, padding=22)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="KINTLER / CONTROL", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Подключить AI / MCP", command=self.mcp_config).pack(side="right")
        ttk.Button(header, text="Setup / Свой сервер", command=self.open_setup).pack(
            side="right", padx=8
        )
        ttk.Button(header, text="Desktop shortcut / Ярлык", command=self.shortcut).pack(
            side="right"
        )
        ttk.Label(outer, textvariable=self.status, style="Status.TLabel").pack(
            anchor="w", pady=(5, 14)
        )
        connection = ttk.LabelFrame(outer, text="Вход на сервер", padding=12)
        connection.pack(fill="x")
        field(connection, "HTTPS-сервер", 0, self.url)
        field(connection, "ID оператора", 1, self.principal)
        field(connection, "Ключ оператора", 2, self.secret, True)
        actions = ttk.Frame(connection)
        actions.grid(row=0, column=2, rowspan=3, sticky="nsew", padx=(16, 0))
        ttk.Button(actions, text="Импортировать .env", command=self.import_env).pack(
            fill="x", pady=3
        )
        self.connect_button = ttk.Button(
            actions, text="Войти", command=self.connect, style="Accent.TButton"
        )
        self.connect_button.pack(fill="x", pady=3)
        ttk.Button(actions, text="Отключиться", command=self.disconnect).pack(fill="x", pady=3)
        workspace = ttk.Panedwindow(outer, orient="horizontal")
        workspace.pack(fill="both", expand=True, pady=(16, 12))
        sidebar = ttk.Frame(workspace)
        workspace.add(sidebar, weight=2)
        self.side_tabs = ttk.Notebook(sidebar)
        self.side_tabs.pack(fill="both", expand=True)
        inventory = ttk.Frame(self.side_tabs, padding=10)
        self.approval_tab = ttk.Frame(self.side_tabs, padding=10)
        self.side_tabs.add(inventory, text="Устройства")
        self.side_tabs.add(self.approval_tab, text="Запросы (0)")
        self.tree = ttk.Treeview(
            inventory, columns=("state", "admin"), show="tree headings", height=8
        )
        self.tree.heading("#0", text="Устройство")
        self.tree.heading("state", text="Связь")
        self.tree.heading("admin", text="Права")
        self.tree.column("#0", width=180, minwidth=110)
        self.tree.column("state", width=75, minwidth=65)
        self.tree.column("admin", width=65, minwidth=55)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select_node)
        ttk.Label(
            inventory,
            text="Список обновляется автоматически.\nСмена ПК отменяет его текущие операции.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(10, 0))
        ttk.Label(
            self.approval_tab,
            text="Каждое новое соединение требует разрешения.\nУ первого подключения сверяйте код на агенте.",
            style="Muted.TLabel",
            wraplength=350,
        ).pack(anchor="w", pady=(0, 8))
        self.request_tree = ttk.Treeview(
            self.approval_tab, columns=("known", "peer"), show="tree headings", height=6
        )
        self.request_tree.heading("#0", text="Запрашивает подключение")
        self.request_tree.heading("known", text="Ключ")
        self.request_tree.heading("peer", text="IP")
        self.request_tree.column("#0", width=160)
        self.request_tree.column("known", width=85)
        self.request_tree.column("peer", width=100)
        self.request_tree.pack(fill="both", expand=True)
        approval_actions = ttk.Frame(self.approval_tab)
        approval_actions.pack(fill="x", pady=10)
        ttk.Button(
            approval_actions,
            text="Разрешить",
            style="Accent.TButton",
            command=lambda: self.decide_connection(True),
        ).pack(side="left")
        ttk.Button(
            approval_actions, text="Отклонить", command=lambda: self.decide_connection(False)
        ).pack(side="left", padx=6)
        operation = ttk.LabelFrame(workspace, text="Управление выбранным ПК", padding=12)
        workspace.add(operation, weight=3)
        ttk.Label(operation, textvariable=self.target, style="Status.TLabel", wraplength=560).pack(
            anchor="w", pady=(0, 10)
        )
        selector = ttk.Combobox(
            operation, textvariable=self.tool, values=list(LABELS.values()), state="readonly"
        )
        selector.pack(fill="x")
        selector.bind("<<ComboboxSelected>>", lambda event: self.render_parameters())
        buttons = ttk.Frame(operation)
        buttons.pack(side="bottom", fill="x")
        self.run_button = ttk.Button(
            buttons,
            text="Выполнить",
            command=self.execute,
            style="Accent.TButton",
            state="disabled",
        )
        self.run_button.pack(side="left")
        ttk.Button(buttons, text="Отменить операции", command=self.cancel_jobs).pack(
            side="left", padx=8
        )
        ttk.Label(
            operation, textvariable=self.action_hint, style="Muted.TLabel", wraplength=500
        ).pack(side="bottom", anchor="w", pady=(8, 0))
        self.form = scroll_form(operation)
        log = ttk.LabelFrame(outer, text="Результат / поток вывода", padding=6)
        log.pack(fill="both")
        self.output = text_output(log, 7)
        self.output.pack(fill="both", expand=True)
        self.render_parameters()
        if self.path.exists():
            try:
                self.fill(load_profile(self.path, "controller"))
                root.after(120, self.connect)
            except (ValueError, OSError) as exc:
                self.status.set(str(exc))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.pump)

    def fill(self, values: dict[str, str]) -> None:
        self.url.set(values.get("KINTLER_URL", self.url.get()))
        self.principal.set(values.get("KINTLER_CONTROLLER_ID", ""))
        self.secret.set(values.get("KINTLER_CONTROLLER_SECRET", ""))
        self.extra = {
            key: values[key]
            for key in (
                "KINTLER_CA_FILE",
                "KINTLER_ALLOW_LOCAL_HTTP",
                "KINTLER_CONTROLLER_PRIVATE_KEY",
            )
            if key in values
        }

    def open_setup(self) -> None:
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, "--setup"])
        else:
            subprocess.Popen([sys.executable, "-m", "desktop.setup_app"])

    def shortcut(self) -> None:
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Shortcut", "Use the packaged controller executable.")
            return
        from desktop.shortcut import create_shortcut

        try:
            destination = create_shortcut(Path(sys.executable))
            messagebox.showinfo("Shortcut", f"Created: {destination}")
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("Shortcut", str(error))

    def import_env(self) -> None:
        selected = filedialog.askopenfilename(
            title="Операторский .env", filetypes=[("Конфигурация", "*"), ("ENV", ".env")]
        )
        if selected:
            try:
                self.fill(load_profile(Path(selected), "controller"))
                self.status.set("Конфигурация загружена. Нажмите «Войти».")
            except (ValueError, OSError) as exc:
                messagebox.showerror("Неверный файл", str(exc))

    def connect(self) -> None:
        if self.client is not None:
            return
        values = {
            **self.extra,
            "KINTLER_URL": self.url.get(),
            "KINTLER_CONTROLLER_ID": self.principal.get(),
            "KINTLER_CONTROLLER_SECRET": self.secret.get(),
        }
        try:
            settings = controller_settings(values)
        except (ValueError, ProtocolError, OSError) as exc:
            messagebox.showerror("Проверьте настройки", str(exc))
            return
        self.connect_button.configure(state="disabled")
        self.status.set("Подключение к серверу…")
        self.connection = self.bridge.submit(self.login(settings, values), "connected")

    async def login(self, settings: ClientSettings, values: dict[str, str]) -> dict:
        client = KintlerClient(settings)
        await client.__aenter__()
        try:
            capabilities = await client.tools()
            nodes = await client.nodes()
            await asyncio.to_thread(save_profile, self.path, values, "controller")
        except BaseException:
            await client.__aexit__()
            raise
        self.client = client
        return {"capabilities": capabilities, "nodes": nodes}

    async def inventory(self) -> None:
        while self.client is not None:
            try:
                self.bridge.post("nodes", await self.client.nodes())
                if self.can_approve:
                    self.bridge.post("approvals", await self.client.approvals())
            except Exception as exc:
                self.bridge.post("inventory_error", str(exc))
            await asyncio.sleep(2)

    def update_requests(self, values: list[dict]) -> None:
        previous = set(self.requests)
        self.requests = {value["request_id"]: value for value in values}
        for item in self.request_tree.get_children():
            if item not in self.requests:
                self.request_tree.delete(item)
        for request in values:
            row = ("Знакомый" if request["known"] else "Новый", request["peer"])
            if self.request_tree.exists(request["request_id"]):
                self.request_tree.item(request["request_id"], text=request["name"], values=row)
            else:
                self.request_tree.insert(
                    "", "end", iid=request["request_id"], text=request["name"], values=row
                )
        self.side_tabs.tab(self.approval_tab, text=f"Запросы ({len(values)})")
        if set(self.requests) - previous:
            self.side_tabs.select(self.approval_tab)
            self.root.bell()

    def decide_connection(self, allow: bool) -> None:
        selected = self.request_tree.selection()
        if not selected or not self.can_approve or self.client is None:
            return
        request = self.requests.get(selected[0])
        if not request:
            return
        code = ""
        if allow and request["code_required"]:
            value = simpledialog.askstring(
                "Сопряжение нового устройства",
                f"{request['name']} / {request['node_id']}\nIP: {request['peer']}\nКлюч: {request['fingerprint']}\n\nВведите 8-значный код из окна нужного Agent.exe:",
                parent=self.root,
            )
            if value is None:
                return
            code = value.strip()
        elif allow and not messagebox.askyesno(
            "Подтвердить новое соединение",
            f"Разрешить новую сессию {request['name']}?\n{request['node_id']}\nIP: {request['peer']}\nКлюч: {request['fingerprint']}\n\nЗнакомое устройство не получает доступ автоматически.",
        ):
            return
        self.bridge.submit(self.client.approve(request["request_id"], allow, code), "approval_done")

    def update_nodes(self, nodes: list[dict]) -> None:
        self.nodes = {node["id"]: node for node in nodes}
        for item in self.tree.get_children():
            if item not in self.nodes:
                self.tree.delete(item)
        for node in nodes:
            values = (
                "Online" if node["online"] else "Offline",
                "Admin" if node["admin"] else "User",
            )
            if self.tree.exists(node["id"]):
                self.tree.item(node["id"], text=node["name"], values=values)
            else:
                self.tree.insert("", "end", iid=node["id"], text=node["name"], values=values)
        if self.selected not in self.nodes:
            self.selected = ""
            self.target.set("Нет выбранного ПК. Зарегистрируйте агент на сервере и запустите его.")
        self.update_action()

    def select_node(self, event: object = None) -> None:
        selected = self.tree.selection()
        if not selected or selected[0] == self.selected:
            return
        self.cancel_jobs()
        self.selected = selected[0]
        node = self.nodes[self.selected]
        self.target.set(
            f"{node['name']}  →  {node['id']}\n{node['platform']} / {'Admin' if node['admin'] else 'User'}"
        )
        append(self.output, f"\n━━ Выбран {node['name']} [{node['id']}] ━━\n")
        self.render_parameters()
        self.update_action()

    def current_tool(self) -> str:
        return next(key for key, label in LABELS.items() if label == self.tool.get())

    def update_action(self) -> None:
        tool = self.current_tool()
        node = self.nodes.get(self.selected, {})
        allowed = tool in self.permitted and tool in node.get("tools", [])
        if tool in {"registry", "services", "tasks"} and not node.get("admin"):
            allowed = False
            self.action_hint.set("На втором ПК нужны права администратора для этого инструмента.")
        elif node and not allowed:
            self.action_hint.set(
                "Инструмент не разрешён оператору или отключён владельцем второго ПК."
            )
        else:
            self.action_hint.set("Операция отправляется только выбранному NodeID.")
        self.run_button.configure(
            state="normal"
            if self.client is not None and node.get("online") and allowed
            else "disabled"
        )

    def render_parameters(self) -> None:
        for child in self.form.winfo_children():
            child.destroy()
        self.parameters.clear()
        tool = self.current_tool()
        for row, parameter in enumerate(PARAMETERS[tool]):
            default = parameter.default
            if (
                tool == "shell"
                and parameter.key == "shell"
                and self.nodes.get(self.selected, {}).get("platform", "").startswith("Linux")
            ):
                default = "bash"
            ttk.Label(self.form, text=parameter.label).grid(
                row=row, column=0, sticky="nw", pady=5, padx=(0, 12)
            )
            if parameter.kind == "multiline":
                widget: Any = tk.Text(self.form, height=4, font=("Consolas", 10), wrap="word")
                widget.grid(row=row, column=1, sticky="nsew", pady=5)
                self.parameters[parameter.key] = widget
            else:
                variable = tk.StringVar(value=default)
                self.parameters[parameter.key] = variable
                widget = (
                    ttk.Combobox(
                        self.form, textvariable=variable, values=parameter.choices, state="readonly"
                    )
                    if parameter.choices
                    else ttk.Entry(self.form, textvariable=variable)
                )
                widget.grid(row=row, column=1, sticky="ew", pady=5)
                if parameter.kind in {"open", "save"}:
                    ttk.Button(
                        self.form,
                        text="…",
                        width=3,
                        command=partial(self.choose_file, parameter.key, parameter.kind),
                    ).grid(row=row, column=2, padx=(6, 0))
        self.form.columnconfigure(1, weight=1)
        if not PARAMETERS[tool]:
            ttk.Label(
                self.form,
                text="CPU, RAM, диски, сеть, GPU и права доступа.\nИзменения на втором ПК не выполняются.",
                style="Muted.TLabel",
            ).grid(row=0, column=0, sticky="nw", pady=8)
        self.update_action()

    def choose_file(self, key: str, kind: str) -> None:
        path = (
            filedialog.askopenfilename()
            if kind == "open"
            else filedialog.asksaveasfilename(
                defaultextension=".png" if self.current_tool() == "screenshot" else ""
            )
        )
        if path:
            variable = self.parameters[key]
            if isinstance(variable, tk.StringVar):
                variable.set(path)
            remote = self.parameters.get("remote_path")
            if kind == "open" and isinstance(remote, tk.StringVar) and not remote.get():
                remote.set(Path(path).name)

    def execute(self) -> None:
        if not self.selected or self.client is None:
            return
        tool = self.current_tool()
        try:
            args = arguments(
                tool,
                {
                    key: value.get("1.0", "end-1c") if isinstance(value, tk.Text) else value.get()
                    for key, value in self.parameters.items()
                },
            )
        except ValueError as exc:
            messagebox.showerror("Параметры операции", str(exc))
            return
        if dangerous(tool, args) and not messagebox.askyesno(
            "Подтверждение",
            f"{LABELS[tool]} на {self.nodes[self.selected]['name']}.\n\nОперация может изменить состояние ПК. Выполнить?",
        ):
            return
        append(self.output, f"\n▶ {LABELS[tool]} — {self.nodes[self.selected]['name']}\n")
        self.jobs = [future for future in self.jobs if not future.done()]
        if len(self.jobs) >= 8:
            messagebox.showwarning("Подождите", "На этом ПК уже выполняется 8 операций.")
            return
        self.jobs.append(
            self.bridge.submit(
                self.operation(self.selected, tool, args, self.generation), "operation_done"
            )
        )

    async def operation(self, node: str, tool: str, args: dict, generation: int) -> dict:
        client = self.client
        if client is None:
            raise ProtocolError("Подключение закрыто")

        async def output(stream: str, data: str) -> None:
            if not self.bridge.post("output", {"generation": generation, "text": data}):
                raise ProtocolError("Окно не успевает читать вывод; операция отменена")

        try:
            if tool == "upload":
                result = await client.upload(
                    node, args["local_path"], args["remote_path"], args["overwrite"]
                )
            elif tool == "download":
                result = await client.download(
                    node, args["remote_path"], args["local_path"], args["overwrite"]
                )
            elif tool == "screenshot":
                capture = await client.checked(node, "screenshot", {"monitor": args["monitor"]})
                result = await client.download(node, capture["path"], args["local_path"])
            else:
                result = await client.call(node, tool, args, on_output=output)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        return {"generation": generation, "node": node, "tool": tool, "result": result}

    def process_window(self, result: dict, node: str) -> None:
        window = tk.Toplevel(self.root)
        window.title("Процессы выбранного ПК")
        window.geometry("980x480")
        columns = ("pid", "name", "user", "ram", "cpu", "created")
        tree = ttk.Treeview(window, columns=columns, show="headings")
        for key in columns:
            tree.heading(key, text=key)
            tree.column(key, width=100 if key not in {"name", "user"} else 180)
        rows = result["processes"]
        for index, process in enumerate(rows):
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    process["pid"],
                    process["name"],
                    process["user"],
                    f"{process['ram'] / 1024 / 1024:.1f} MiB",
                    process["cpu"],
                    process["created"],
                ),
            )
        tree.pack(fill="both", expand=True, padx=14, pady=14)

        def choose() -> None:
            selected = tree.selection()
            if not selected or node != self.selected:
                return
            process = rows[int(selected[0])]
            self.tool.set(LABELS["kill"])
            self.render_parameters()
            for key in ("pid", "created"):
                variable = self.parameters[key]
                if isinstance(variable, tk.StringVar):
                    variable.set(str(process[key]))
            window.destroy()

        ttk.Button(window, text="Подготовить завершение выбранного процесса", command=choose).pack(
            pady=(0, 14)
        )

    def cancel_jobs(self) -> None:
        self.generation += 1
        for future in self.jobs:
            future.cancel()
        self.jobs.clear()

    async def logout(self) -> None:
        client, self.client = self.client, None
        if client:
            try:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(client.__aexit__(), 10)
            finally:
                if client.http:
                    await client.http.close()

    def disconnect(self) -> None:
        self.cancel_jobs()
        if self.connection and not self.connection.done():
            self.connection.cancel()
        if self.poller:
            self.poller.cancel()
        self.bridge.submit(self.logout(), "disconnected")
        self.status.set("Отключение…")
        self.run_button.configure(state="disabled")

    def handle(self, kind: str, value: Any) -> None:
        if kind == "connected":
            self.status.set(f"Выполнен вход: {self.principal.get()}. Сначала выберите ПК.")
            self.permitted = set(value["capabilities"]["tools"])
            self.can_approve = value["capabilities"].get("can_approve", False)
            self.update_nodes(value["nodes"])
            self.poller = self.bridge.submit(self.inventory(), "poll_done")
        elif kind == "nodes":
            if self.client:
                self.update_nodes(value)
        elif kind == "approvals":
            self.update_requests(value)
        elif kind == "approval_done":
            append(self.output, "\nРешение по конкретному соединению отправлено.\n")
        elif kind == "output" and value["generation"] == self.generation:
            append(self.output, printable(value["text"]))
        elif kind == "operation_done" and value["generation"] == self.generation:
            result = value["result"]
            append(
                self.output,
                json.dumps(
                    {key: item for key, item in result.items() if key not in {"stdout", "stderr"}},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
            if value["tool"] == "ps" and result.get("ok"):
                self.process_window(result, value["node"])
        elif kind in {"error", "inventory_error"}:
            self.status.set("Нет связи: " + printable(value))
            append(self.output, "\nОшибка: " + printable(value) + "\n")
            if self.client is None:
                self.connect_button.configure(state="normal")
        elif kind == "disconnected":
            self.update_requests([])
            self.status.set("Отключено. Можно войти снова.")
            self.connect_button.configure(state="normal")
            self.update_action()

    def pump(self) -> None:
        if not self.closing:
            self.bridge.drain(self.handle)
            self.root.after(50, self.pump)

    def mcp_config(self) -> None:
        if not self.path.exists():
            messagebox.showinfo(
                "Сначала войдите",
                "Войдите на сервер: локальный профиль будет создан после проверки ключа.",
            )
            return
        executable = (
            Path(sys.executable)
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1] / ".venv/Scripts/kintler-mcp.exe"
        )
        config = {
            "mcpServers": {
                "kintler": {
                    "command": str(executable),
                    "args": (["--mcp"] if getattr(sys, "frozen", False) else [])
                    + ["--env", str(self.path)],
                }
            }
        }
        window = tk.Toplevel(self.root)
        window.title("Подключение AI-агента через MCP")
        window.geometry("760x390")
        ttk.Label(
            window,
            text="Добавьте этот блок в MCP-настройки вашего AI-клиента.\nКлюч не включён в JSON. MCP работает внутри этого же EXE.",
            padding=15,
        ).pack(anchor="w")
        text = tk.Text(window, font=("Consolas", 10), height=10)
        encoded = json.dumps(config, ensure_ascii=False, indent=2)
        text.insert("1.0", encoded)
        text.pack(fill="both", expand=True, padx=15)

        def copy() -> None:
            window.clipboard_clear()
            window.clipboard_append(encoded)

        ttk.Button(window, text="Скопировать конфигурацию", command=copy).pack(pady=15)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.cancel_jobs()
        if self.connection and not self.connection.done():
            self.connection.cancel()
        if self.poller:
            self.poller.cancel()
        future = self.bridge.submit(self.logout(), "closed")
        self.root.after(100, lambda: self.finish_close(future, 0))

    def finish_close(self, future: Future, attempt: int) -> None:
        if future.done() or attempt > 110:
            self.bridge.stop()
            self.root.destroy()
        else:
            self.root.after(100, lambda: self.finish_close(future, attempt + 1))


async def check(path: Path, node: str, result: Path) -> None:
    async with KintlerClient(controller_settings(load_profile(path, "controller"))) as client:
        info = await client.call(node, "sysinfo")
        command = await client.call(
            node, "shell", {"shell": "cmd", "command": "echo FROZEN_KINTLER_OK"}
        )
        result.write_text(
            json.dumps(
                {
                    "ok": bool(
                        info["ok"] and command["ok"] and "FROZEN_KINTLER_OK" in command["stdout"]
                    ),
                    "tools": (await client.tools())["tools"],
                }
            ),
            encoding="utf-8",
        )


def main() -> None:
    if "--mcp" in sys.argv:
        sys.argv.remove("--mcp")
        from desktop.mcp_entry import main as mcp_main
        from desktop.stdio import restored_stdio

        with restored_stdio():
            mcp_main()
        return
    if "--setup" in sys.argv:
        sys.argv.remove("--setup")
        from desktop.setup_app import main as setup_main

        setup_main()
        return
    if "--deploy" in sys.argv:
        sys.argv.remove("--deploy")
        from desktop.stdio import restored_stdio
        from scripts.deploy_remote import main as deploy_main

        with restored_stdio():
            deploy_main()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path)
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--check", type=Path)
    parser.add_argument("--node")
    parser.add_argument("--create-shortcut", action="store_true")
    parser.add_argument("--shortcut-destination", type=Path)
    parser.add_argument("--activate-profile", type=Path)
    args = parser.parse_args()
    if args.activate_profile:
        from desktop.settings import activate_profile

        activate_profile(args.activate_profile)
        return
    if args.create_shortcut:
        from desktop.shortcut import create_shortcut

        create_shortcut(Path(sys.executable), args.shortcut_destination)
        return
    if args.check:
        if not args.env or not args.node:
            raise ValueError("--check requires --env and --node")
        asyncio.run(check(args.env, args.node, args.check))
        return
    if args.self_test:
        root = tk.Tk()
        root.withdraw()
        style(root)
        root.update()
        root.destroy()
        args.self_test.write_text(
            json.dumps({"ok": True, "role": "controller", "tk": tk.TkVersion}), encoding="utf-8"
        )
        return
    root = tk.Tk()
    ControllerApp(root, args.env)
    root.mainloop()


if __name__ == "__main__":
    main()
