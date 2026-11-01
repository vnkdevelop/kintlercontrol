import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.release_layout import SKILL_FILES

HOST_PATHS = {
    "shared": (".agents/skills",),
    "opencode": (".config/opencode/skills",),
    "codex": (".agents/skills",),
    "claude": (".claude/skills",),
    "gemini": (".gemini/skills",),
    "antigravity": (".gemini/config/skills", ".gemini/antigravity/skills"),
    "copilot": (".copilot/skills",),
    "cursor": (".cursor/skills",),
}


def skill_source() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    candidates = [
        root / "skills/kintler-remote",
        root / ".agents/skills/kintler-remote",
        Path(__file__).resolve().parents[1] / "desktop/resources/skills/kintler-remote",
    ]
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file():
            return candidate
    raise FileNotFoundError("The packaged Kintler skill is missing")


def targets(home: Path, host: str) -> list[Path]:
    if host != "all" and host not in HOST_PATHS:
        raise ValueError("Unsupported skill host")
    paths = []
    for name, locations in HOST_PATHS.items():
        if host != "all" and host != name:
            continue
        for location in locations:
            path = home / location
            if host != "all" or name == "shared" or path.parent.is_dir() or shutil.which(name):
                paths.append(path / "kintler-remote")
    if host in {"all", "codex"} and (home / ".codex/skills").is_dir():
        paths.append(home / ".codex/skills/kintler-remote")
    return list(dict.fromkeys(paths))


def install(
    host: str = "all",
    dry_run: bool = False,
    replace_existing: bool = False,
    home: Path | None = None,
    source: Path | None = None,
) -> dict:
    source = source or skill_source()
    home = (home or Path.home()).resolve()
    payload = {name: (source / name).read_bytes() for name in SKILL_FILES}
    result: dict[str, Any] = {
        "skill": "kintler-remote",
        "dry_run": dry_run,
        "installed": [],
        "unchanged": [],
        "kept_existing": [],
    }
    for destination in targets(home, host):
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise ValueError("Refusing to replace a symlink or non-directory skill destination")
        if destination.is_dir():
            if all(
                (destination / name).is_file() and (destination / name).read_bytes() == data
                for name, data in payload.items()
            ):
                result["unchanged"].append(str(destination))
                continue
            if not replace_existing:
                result["kept_existing"].append(str(destination))
                continue
            entry = destination / "SKILL.md"
            text = entry.read_text(encoding="utf-8") if entry.is_file() else ""
            if not re.search(r"(?m)^name:\s*kintler-remote\s*$", text) or not re.search(
                r"(?m)^\s+product:\s*kintlercontrol\s*$", text
            ):
                raise ValueError(
                    "Existing skill is not a managed Kintler package; refusing to overwrite it"
                )
        if not dry_run:
            previous = {
                name: (destination / name).read_bytes() if (destination / name).is_file() else None
                for name in payload
            }
            destination.mkdir(parents=True, exist_ok=True)
            try:
                for name, data in payload.items():
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_symlink() or not target.parent.resolve().is_relative_to(
                        destination.resolve()
                    ):
                        raise ValueError(
                            "Skill payload destination points outside its managed directory"
                        )
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            prefix=".kintler-skill-", dir=target.parent, delete=False
                        ) as handle:
                            temporary = Path(handle.name)
                            handle.write(data)
                        temporary.replace(target)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
            except BaseException:
                for name, saved_data in previous.items():
                    target = destination / name
                    if saved_data is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_bytes(saved_data)
                raise
        result["installed"].append(str(destination))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install only the generic Kintler skill into supported local AI hosts; does not configure MCP or owner profiles"
    )
    parser.add_argument("--host", choices=("all", *HOST_PATHS), default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            install(args.host, args.dry_run, args.replace_existing), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
