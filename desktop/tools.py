from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Parameter:
    key: str
    label: str
    default: str = ""
    kind: str = "text"
    choices: tuple[str, ...] = ()


LABELS = {
    "shell": "Командная строка",
    "sysinfo": "Информация о системе",
    "ps": "Процессы",
    "kill": "Завершить процесс",
    "screenshot": "Снимок экрана",
    "upload": "Отправить файл",
    "download": "Получить файл",
    "registry": "Реестр Windows",
    "services": "Службы",
    "tasks": "Планировщик Windows",
}
PARAMETERS = {
    "shell": [
        Parameter("shell", "Оболочка", "cmd", choices=("cmd", "powershell", "bash")),
        Parameter("cwd", "Каталог в workspace", "."),
        Parameter("command", "Команда", kind="multiline"),
    ],
    "sysinfo": [],
    "ps": [
        Parameter("sort", "Сортировка", "ram", choices=("ram", "cpu", "pid")),
        Parameter("limit", "Количество", "100", "int"),
    ],
    "kill": [
        Parameter("pid", "PID", kind="int"),
        Parameter("created", "Время создания из списка процессов", kind="float"),
        Parameter("force", "Принудительно после ожидания", "false", "bool", ("false", "true")),
    ],
    "screenshot": [
        Parameter("monitor", "Монитор: 1 — первый, 0 — все", "1", "int"),
        Parameter("local_path", "Сохранить PNG на этом ПК", kind="save"),
    ],
    "upload": [
        Parameter("local_path", "Файл на этом ПК", kind="open"),
        Parameter("remote_path", "Путь в workspace второго ПК"),
        Parameter(
            "overwrite", "Перезаписать существующий файл", "false", "bool", ("false", "true")
        ),
    ],
    "download": [
        Parameter("remote_path", "Путь в workspace второго ПК"),
        Parameter("local_path", "Сохранить на этом ПК", kind="save"),
        Parameter(
            "overwrite", "Перезаписать существующий файл", "false", "bool", ("false", "true")
        ),
    ],
    "registry": [
        Parameter("operation", "Действие", "query", choices=("query", "set")),
        Parameter("key", "Ключ реестра", "HKCU\\Software"),
        Parameter("name", "Имя значения"),
        Parameter("value", "Новое значение"),
        Parameter(
            "value_type",
            "Тип значения",
            "REG_SZ",
            choices=(
                "REG_SZ",
                "REG_EXPAND_SZ",
                "REG_DWORD",
                "REG_QWORD",
                "REG_MULTI_SZ",
                "REG_BINARY",
            ),
        ),
    ],
    "services": [
        Parameter(
            "operation", "Действие", "list", choices=("list", "status", "start", "stop", "restart")
        ),
        Parameter("name", "Имя службы"),
    ],
    "tasks": [
        Parameter("operation", "Действие", "list", choices=("list", "create")),
        Parameter("name", "Имя задачи"),
        Parameter("command", "Команда ONLOGON-задачи"),
    ],
}


def arguments(tool: str, values: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for parameter in PARAMETERS[tool]:
        value = values.get(parameter.key, parameter.default)
        if parameter.kind == "int":
            result[parameter.key] = int(value)
        elif parameter.kind == "float":
            result[parameter.key] = float(value)
        elif parameter.kind == "bool":
            if value not in {"true", "false"}:
                raise ValueError("Ожидается true или false")
            result[parameter.key] = value == "true"
        else:
            result[parameter.key] = value
    if tool in {"upload", "download", "screenshot"} and not result.get("local_path"):
        raise ValueError("Выберите локальный файл для операции")
    return result


def dangerous(tool: str, args: dict) -> bool:
    return (
        tool in {"shell", "kill"}
        or bool(args.get("overwrite"))
        or (tool == "registry" and args.get("operation") == "set")
        or (tool in {"services", "tasks"} and args.get("operation") not in {"list", "status"})
    )
