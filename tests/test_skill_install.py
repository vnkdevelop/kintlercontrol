import json
from pathlib import Path

import pytest

from scripts.install_skill import install, skill_source, targets
from scripts.mcp_config import connection
from scripts.release_layout import SKILL_FILES


def test_skill_installation_is_dry_run_safe_and_idempotent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("scripts.install_skill.shutil.which", lambda name: None)
    for name in (".config/opencode", ".claude", ".gemini/config", ".copilot", ".codex/skills"):
        (tmp_path / name).mkdir(parents=True)
    report = install("all", dry_run=True, home=tmp_path)
    assert report["installed"] and not (tmp_path / ".agents/skills/kintler-remote").exists()
    report = install("all", home=tmp_path)
    for directory in report["installed"]:
        assert all((Path(directory) / name).is_file() for name in SKILL_FILES)
    again = install("all", home=tmp_path)
    assert not again["installed"] and len(again["unchanged"]) == len(report["installed"])
    assert not list(tmp_path.rglob(".kintler-skill-*"))


def test_existing_unmanaged_skill_is_preserved(tmp_path: Path):
    destination = targets(tmp_path, "shared")[0]
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("user work")
    result = install("shared", home=tmp_path)
    assert result["kept_existing"] == [str(destination)]
    with pytest.raises(ValueError, match="managed"):
        install("shared", replace_existing=True, home=tmp_path)
    assert (destination / "SKILL.md").read_text() == "user work"


def test_managed_skill_update_preserves_unrelated_files(tmp_path: Path):
    install("shared", home=tmp_path)
    destination = targets(tmp_path, "shared")[0]
    (destination / "notes.txt").write_text("user notes")
    entry = destination / "SKILL.md"
    entry.write_text(
        entry.read_text(encoding="utf-8").replace('version: "1.6.0"', 'version: "old"'),
        encoding="utf-8",
    )
    result = install("shared", replace_existing=True, home=tmp_path)
    assert result["installed"] == [str(destination)]
    assert entry.read_bytes() == (skill_source() / "SKILL.md").read_bytes()
    assert (destination / "notes.txt").read_text() == "user notes"


def test_mcp_config_is_host_specific_and_never_contains_credentials(tmp_path: Path):
    executable = tmp_path / "Controller.exe"
    operator = tmp_path / ".env"
    generic = connection("generic", executable, operator)
    assert generic["args"] == ["--mcp", "--env", str(operator.resolve())]
    opencode = connection("opencode", executable)
    assert opencode == {"type": "local", "command": [str(executable.resolve()), "--mcp"]}
    assert "enabled" not in opencode
    assert "[mcp_servers.kintler]" in connection("codex", executable)
    assert connection("vscode", executable)["type"] == "stdio"
    assert not any(
        name in json.dumps(generic) for name in ("PASSWORD", "PRIVATE_KEY", "KINTLER_URL")
    )
