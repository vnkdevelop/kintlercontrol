import argparse
import json
import os
import shlex
from dataclasses import dataclass, field
from typing import Any, Never


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    usage: str


COMMANDS = (
    CommandSpec("help", "Все команды и примеры", "/help [команда]"),
    CommandSpec("devices", "Устройства: связь, права и состояние", "/devices"),
    CommandSpec("use", "Выбрать доступный ПК из списка", "/use [имя_или_номер]"),
    CommandSpec("requests", "Ожидающие подключения", "/requests"),
    CommandSpec(
        "approve", "Выбрать устройство и ввести код из Agent", "/approve [имя_или_номер] [код]"
    ),
    CommandSpec("deny", "Отклонить подключение без доступа", "/deny номер_или_имя"),
    CommandSpec("sysinfo", "CPU, RAM, диски, сеть и права", "/sysinfo"),
    CommandSpec("ps", "Процессы и время их создания", "/ps --sort ram --limit 20"),
    CommandSpec(
        "shell", "Команда на выбранном ПК", "/shell команда | /shell --cwd папка -- команда"
    ),
    CommandSpec("message", "Показать окно с вашим текстом на Windows-ПК", "/message ваш текст"),
    CommandSpec(
        "open",
        "Открыть приложение на выбранном ПК",
        '/open notepad.exe | /open "путь к приложению.exe" [аргументы]',
    ),
    CommandSpec(
        "desktop",
        "Управление интерфейсом, мышью и клавиатурой",
        '/desktop help | /desktop snapshot {"app":"Notepad"}',
    ),
    CommandSpec(
        "kill", "Выбрать процесс из списка и завершить", "/kill [PID время_создания] [--force]"
    ),
    CommandSpec(
        "upload",
        "Файл с этого ПК на выбранное устройство",
        "/upload [локальный_файл] [удалённый_путь] [--overwrite]",
    ),
    CommandSpec(
        "download",
        "Файл с устройства на этот ПК",
        "/download [удалённый_путь] [локальный_файл] [--overwrite]",
    ),
    CommandSpec(
        "screenshot",
        "Снимок в Изображения / KintlerControl",
        "/screenshot [локальный.png] [--monitor 1]",
    ),
    CommandSpec(
        "registry",
        "Прочитать или изменить реестр Windows",
        "/registry query HKCU\\Software [--name имя]",
    ),
    CommandSpec(
        "services", "Список и управление службами", "/services list | /services status имя"
    ),
    CommandSpec(
        "tasks",
        "Задачи Windows: список или ONLOGON",
        "/tasks list | /tasks create --name имя --command команда",
    ),
    CommandSpec("permissions", "Доступные инструменты и ограничения", "/permissions"),
    CommandSpec(
        "reconnect", "Восстановить связь с Kintler; доступ к ПК — через /approve", "/reconnect"
    ),
    CommandSpec("profile", "Выбрать локальный профиль владельца", "/profile путь_к_.env"),
    CommandSpec(
        "setup",
        "Создать комплект своего сервера и Agent",
        "/setup https://relay.example.com папка [--mode existing-proxy]",
    ),
    CommandSpec("mcp", "Подключение AI без передачи ключа", "/mcp"),
    CommandSpec(
        "install-skill", "Установить скилл Kintler локальным AI-агентам", "/install-skill [host]"
    ),
    CommandSpec("shortcut", "Создать ярлык Controller", "/shortcut"),
    CommandSpec("cancel", "Отменить текущие операции на ПК", "/cancel"),
    CommandSpec("disconnect", "Завершить свою сессию Controller", "/disconnect"),
    CommandSpec("clear", "Очистить экран, не историю доступа", "/clear"),
    CommandSpec("exit", "Безопасно закрыть Controller", "/exit"),
)
SPECS = {command.name: command for command in COMMANDS}


@dataclass(frozen=True)
class Invocation:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


class CommandParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        if self.prog == "/approve":
            raise ValueError("Откройте /approve и введите 6 цифр из Agent в появившемся поле.")
        name = self.prog.lstrip("/")
        raise ValueError(
            f"Укажите параметры команды. Пример: {SPECS[name].usage}\n/help {name} — описание"
        )

    def exit(self, status: int = 0, message: str | None = None) -> Never:
        raise ValueError(message or "Некорректные аргументы команды")


def words(value: str) -> list[str]:
    values = shlex.split(value, posix=os.name != "nt")
    return [
        item[1:-1] if len(item) > 1 and item[0] == item[-1] and item[0] in "\"'" else item
        for item in values
    ]


def parse_command(value: str) -> Invocation:
    value = value.strip()
    if not value:
        raise ValueError("Введите команду или /help")
    if value.isdigit():
        if not value.isascii() or len(value) != 6:
            raise ValueError("Введите ровно 6 цифр из Agent или откройте /approve.")
        return Invocation("approve", {"target": "", "code": value}, value)
    if value.lower().startswith("open "):
        value = "/" + value
    if not value.startswith("/"):
        return Invocation(
            "shell", {"command": value, "shell": None, "cwd": ".", "timeout": 120}, value
        )
    name, _, tail = value[1:].partition(" ")
    name = name.lower()
    if name == "connect":
        name = "reconnect"
    if name not in SPECS:
        raise ValueError(f"Неизвестная команда /{name}. Откройте /help")
    if name == "message":
        if not tail.strip() or "\0" in tail or len(tail) > 2000:
            raise ValueError("Укажите текст сообщения: /message ваш текст (до 2000 символов).")
        return Invocation(name, {"text": tail}, value)
    if name == "open":
        tokens = words(tail)
        if not tokens or not tokens[0].strip():
            raise ValueError(SPECS[name].usage)
        return Invocation(name, {"app": tokens[0], "arguments": tokens[1:]}, value)
    if name == "desktop":
        action, _, payload = tail.strip().partition(" ")
        if not action:
            raise ValueError(SPECS[name].usage)
        try:
            data = json.loads(payload) if payload.strip() else {}
        except json.JSONDecodeError as error:
            raise ValueError("Параметры /desktop должны быть JSON-объектом") from error
        if not isinstance(data, dict):
            raise ValueError("Параметры /desktop должны быть JSON-объектом")
        headed = data.pop("headed", False)
        if not isinstance(headed, bool):
            raise ValueError("headed должен быть true или false")
        return Invocation(name, {"action": action, "params": data, "headed": headed}, value)
    parser = CommandParser(prog=f"/{name}", add_help=False)
    if name in {"help", "use", "profile", "approve", "deny"}:
        parser.add_argument(
            "target", nargs="?" if name in {"help", "approve", "use"} else None, default=""
        )
        if name == "approve":
            parser.add_argument("code", nargs="?", default="")
    elif name == "ps":
        parser.add_argument("--sort", choices=("ram", "cpu", "pid"), default="ram")
        parser.add_argument("--limit", type=int, default=20)
    elif name == "shell":
        if not tail.startswith("--"):
            if not tail.strip():
                raise ValueError(SPECS[name].usage)
            return Invocation(
                name, {"command": tail, "shell": None, "cwd": ".", "timeout": 120}, value
            )
        parser.add_argument("--interpreter", dest="shell", choices=("cmd", "powershell", "bash"))
        parser.add_argument("--cwd", default=".")
        parser.add_argument("--timeout", type=float, default=120)
        options, separator, command = tail.partition(" -- ")
        if not separator or not command.strip():
            raise ValueError(SPECS[name].usage)
        data = vars(parser.parse_args(words(options)))
        data["command"] = command
        return Invocation(name, data, value)
    elif name == "kill":
        parser.add_argument("pid", type=int, nargs="?")
        parser.add_argument("created", type=float, nargs="?")
        parser.add_argument("--force", action="store_true")
    elif name in {"upload", "download"}:
        for argument in (
            ("local_path", "remote_path") if name == "upload" else ("remote_path", "local_path")
        ):
            parser.add_argument(argument, nargs="?", default="")
        parser.add_argument("--overwrite", action="store_true")
    elif name == "screenshot":
        parser.add_argument("local_path", nargs="?", default="")
        parser.add_argument("--monitor", type=int, default=1)
    elif name == "registry":
        parser.add_argument("operation", choices=("query", "set"))
        parser.add_argument("key")
        parser.add_argument("--name", default="")
        parser.add_argument("--value", default="")
        parser.add_argument(
            "--type",
            dest="value_type",
            default="REG_SZ",
            choices=(
                "REG_SZ",
                "REG_EXPAND_SZ",
                "REG_DWORD",
                "REG_QWORD",
                "REG_MULTI_SZ",
                "REG_BINARY",
            ),
        )
    elif name == "services":
        parser.add_argument(
            "operation",
            nargs="?",
            default="list",
            choices=("list", "status", "start", "stop", "restart"),
        )
        parser.add_argument("name", nargs="?", default="")
    elif name == "tasks":
        parser.add_argument("operation", nargs="?", default="list", choices=("list", "create"))
        parser.add_argument("--name", default="")
        parser.add_argument("--command", default="")
    elif name == "setup":
        parser.add_argument("url")
        parser.add_argument("output")
        parser.add_argument("--mode", choices=("existing-proxy", "automatic"), default="automatic")
    elif name == "install-skill":
        parser.add_argument(
            "host",
            nargs="?",
            default="all",
            choices=(
                "all",
                "shared",
                "opencode",
                "codex",
                "claude",
                "gemini",
                "antigravity",
                "copilot",
                "cursor",
            ),
        )
    data = vars(parser.parse_args(words(tail)))
    if name == "kill":
        if (data["pid"] is None) != (data["created"] is None):
            parser.error("PID and creation time must be supplied together")
        if data["pid"] is None:
            data.pop("pid")
            data.pop("created")
    if (
        name == "approve"
        and not data["code"]
        and len(data["target"]) >= 6
        and data["target"].isdigit()
    ):
        data["code"], data["target"] = data["target"], ""
    if (
        name == "approve"
        and data["code"]
        and (not data["code"].isascii() or not data["code"].isdigit() or len(data["code"]) != 6)
    ):
        raise ValueError("Введите 6-значный код из Agent")
    if name == "ps" and not 1 <= data["limit"] <= 500:
        raise ValueError("Лимит процессов: 1–500")
    if name in {"services", "tasks"} and data["operation"] != "list" and not data["name"]:
        raise ValueError("Укажите системное имя службы или задачи")
    if name == "tasks" and data["operation"] == "create" and not data["command"]:
        raise ValueError("Укажите --command для задачи")
    return Invocation(name, data, value)


def printable_result(value: dict) -> str:
    return json.dumps(
        {key: item for key, item in value.items() if key not in {"command_id", "stdout", "stderr"}},
        ensure_ascii=False,
        indent=2,
    )
