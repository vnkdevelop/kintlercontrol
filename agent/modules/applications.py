import time
from pathlib import Path

from common.protocol import ProtocolError
from desktop.shortcut import read_shortcut, start_menu_folders


def short_name(value: str) -> str | None:
    if not value or any(character in value for character in "\\/:\0"):
        return None
    return value.casefold().removesuffix(".exe") or None


def find_in_folders(
    name: str, folders: list[Path], timeout: float = 10
) -> tuple[str, str, str | None] | None:
    requested = short_name(name)
    if requested is None:
        return None
    pending = [(path, 0) for path in folders]
    deadline = time.monotonic() + timeout
    count = 0
    found = {}
    while pending:
        folder, depth = pending.pop()
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            count += 1
            if count > 10000 or time.monotonic() > deadline:
                raise ProtocolError("Start menu search limit reached; use the full executable path")
            if path.is_symlink() or path.is_junction():
                continue
            if path.is_dir():
                if depth >= 16:
                    raise ProtocolError(
                        "Start menu search depth reached; use the full executable path"
                    )
                pending.append((path, depth + 1))
            elif path.is_file() and path.suffix.casefold() == ".lnk":
                try:
                    target, arguments, cwd = read_shortcut(path)
                except (OSError, RuntimeError):
                    continue
                if path.stem.casefold() == requested or short_name(Path(target).name) == requested:
                    if Path(target).is_file():
                        found[(target.casefold(), arguments, cwd.casefold() if cwd else None)] = (
                            target,
                            arguments,
                            cwd,
                        )
    if len(found) > 1:
        raise ProtocolError(
            "Several installed applications match this name; use the full executable path"
        )
    return next(iter(found.values()), None)


def find_installed(name: str) -> tuple[str, str, str | None] | None:
    if short_name(name) is None:
        return None
    return find_in_folders(name, start_menu_folders())
