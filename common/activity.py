import re

SENSITIVE = re.compile(
    r"(?i)((?:password|passwd|token|secret|api[_-]?key|private[_-]?key|authorization)\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s;&]+)"
)
BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"']+")
QUOTED_ASSIGNMENT = re.compile(
    r"(?i)(\bset\s+\"[^\"=]*(?:password|passwd|token|secret|api[_-]?key|private[_-]?key)\s*=)[^\"]*(\")"
)
SECRET_FLAGS = re.compile(
    r"(?i)((?:--?)(?:password|passwd|token|secret|api[_-]?key|private[_-]?key)\s+)(?:\"[^\"]*\"|'[^']*'|[^\s;&]+)"
)


def redact(value: str, limit: int = 1000) -> str:
    value = QUOTED_ASSIGNMENT.sub(r"\1[скрыто]\2", value)
    value = BEARER.sub("Bearer [скрыто]", value)
    value = SECRET_FLAGS.sub(r"\1[скрыто]", value)
    value = SENSITIVE.sub(r"\1[скрыто]", value)
    value = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[скрыто]@", value)
    value = "".join(
        character if character >= " " or character == "\n" else " " for character in value
    )
    return value[:limit] + ("…" if len(value) > limit else "")


def describe_action(tool: str, args: dict, origin: str = "controller") -> str:
    actor = "AI" if origin == "mcp" else "Оператор"
    operation = str(args.get("operation", ""))
    if tool == "shell":
        details = str(args.get("command", ""))
    elif tool in {"upload", "download"}:
        details = " ".join(str(args.get(key, "")) for key in ("operation", "path") if args.get(key))
    elif tool == "registry":
        details = f"{operation} {args.get('key', '')} {args.get('name', '')}"
        if operation == "set":
            details += " · изменение значения"
    elif tool == "services":
        details = f"{operation or 'list'} {args.get('name', '')}"
    elif tool == "tasks":
        details = f"{operation or 'list'} {args.get('name', '')} {args.get('command', '')}"
    elif tool == "kill":
        details = f"PID {args.get('pid', '')}"
    elif tool == "screenshot":
        details = f"монитор {args.get('monitor', 1)}"
    elif tool == "ps":
        details = f"сортировка {args.get('sort', 'ram')}, лимит {args.get('limit', 100)}"
    else:
        details = ""
    return redact(f"{actor} · {tool}  {details}".strip())


def display_action(value: str) -> str:
    for prefix in ("Оператор ·", "AI ·"):
        if value.startswith(prefix):
            value = value[len(prefix) :].lstrip()
            break
    return redact(value)
