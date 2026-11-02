import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def installation_file(home: Path | None = None) -> Path:
    if home is not None:
        base = home / "AppData/Local" if sys.platform == "win32" else home / ".local/share"
    else:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share")))
    return (base / "KintlerControl/installation.json").resolve()


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("Installation registry must not be a symlink")
    if not path.exists():
        return {"schema": 1, "product": "kintlercontrol", "controller": {}, "deployments": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema") != 1
        or data.get("product") != "kintlercontrol"
        or not isinstance(data.get("controller"), dict)
        or not isinstance(data.get("deployments"), list)
        or any(
            not isinstance(entry, dict) or not isinstance(entry.get("root"), str)
            for entry in data.get("deployments", [])
        )
    ):
        raise ValueError("Existing installation registry is not a managed Kintler map")
    return data


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(".installation.lock")
    if lock_path.is_symlink():
        raise ValueError("Installation lock must not be a symlink")
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def register_installation(
    controller: Path,
    deployment: Path | None = None,
    owner_profile: Path | None = None,
    home: Path | None = None,
    agent_executable: Path | None = None,
) -> Path:
    controller = controller.resolve()
    if not controller.is_file():
        raise ValueError("Controller executable is missing")
    path = installation_file(home)
    with _locked(path):
        data = _load(path)
        data["controller"]["path"] = str(controller)
        if deployment is not None:
            folder = deployment.resolve()
            record = {
                "root": str(folder),
                "server_kit": str(folder / "server-kit.zip"),
                "server_folder": str(folder / "server"),
                "server_settings": str(folder / "server/.env"),
                "server_launcher": str(folder / "server/start_server.py"),
                "server_image": str(folder / "server/kintlercontrol-image.tar"),
                "server_image_checksum": str(folder / "server/kintlercontrol-image.tar.sha256"),
                "server_readme": str(folder / "server/START_HERE.md"),
                "agent_executable": str(agent_executable.resolve())
                if agent_executable
                else str(folder / "Agent/KintlerAgent.exe"),
                "owner_profile_path": str(owner_profile.resolve()) if owner_profile else None,
            }
            data["deployments"] = [
                entry for entry in data["deployments"] if entry.get("root") != str(folder)
            ]
            data["deployments"].append(record)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".kintler-installation-",
                dir=path.parent,
                delete=False,
                mode="w",
                encoding="utf-8",
            ) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=2)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return path


def installation_information(home: Path | None = None) -> dict[str, Any]:
    path = installation_file(home)
    root = path.parent
    active = root / "controller/.env"
    selector = root / "active-controller.json"
    try:
        if selector.is_file():
            selected = Path(json.loads(selector.read_text(encoding="utf-8"))["profile"])
            if (
                selected.name == ".env"
                and selected.is_file()
                and selected.resolve().is_relative_to(root.resolve())
            ):
                active = selected.resolve()
    except (ValueError, KeyError, OSError):
        pass
    valid = True
    try:
        data = _load(path)
    except (ValueError, OSError):
        data = {"controller": {}, "deployments": []}
        valid = False
    controller = data["controller"].get("path")
    controller = controller if isinstance(controller, str) else None
    exists = bool(controller and Path(controller).is_file())
    deployments = []
    for entry in data["deployments"]:
        if isinstance(entry, dict) and isinstance(entry.get("root"), str):
            record = {
                key: entry.get(key)
                for key in (
                    "root",
                    "server_kit",
                    "server_folder",
                    "server_settings",
                    "server_launcher",
                    "server_image",
                    "server_image_checksum",
                    "server_readme",
                    "agent_executable",
                    "owner_profile_path",
                )
                if isinstance(entry.get(key), str)
            }
            record["exists"] = Path(entry["root"]).is_dir()
            record["active"] = entry.get("owner_profile_path") == str(active)
            deployments.append(record)
    return {
        "schema": 1,
        "product": "kintlercontrol",
        "registered": valid and path.is_file(),
        "installation_file": str(path),
        "controller": {"path": controller, "exists": exists},
        "user_data": {
            "root": str(root),
            "active_profile_selector": str(selector),
            "active_owner_profile": str(active),
            "owners_root": str(root / "owners"),
            "agent_profiles_root": str(root / "agents"),
        },
        "deployments": deployments,
        "mcp": {"transport": "stdio", "command": controller if exists else None, "args": ["--mcp"]},
    }


def skill_local_context(home: Path | None = None, controller: Path | None = None) -> bytes:
    information = installation_information(home)
    hint = str(controller.resolve()) if controller else information["controller"]["path"]
    return json.dumps(
        {
            "schema": 1,
            "product": "kintlercontrol",
            "installation_file": information["installation_file"],
            "controller_path_hint": hint,
            "user_data_root": information["user_data"]["root"],
            "active_profile_selector": information["user_data"]["active_profile_selector"],
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
