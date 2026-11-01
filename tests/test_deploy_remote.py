import base64
import hashlib
import io
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts.configure import write_env
from scripts.deploy_remote import REMOTE, settings


def test_deployment_settings_are_local_and_isolated(tmp_path: Path):
    key = tmp_path / "key"
    key.write_text("fixture")
    source = tmp_path / ".env"
    write_env(
        source,
        {
            "KINTLER_SSH_HOST": "server.example.com",
            "KINTLER_SSH_USER": "deploy",
            "KINTLER_SSH_KEY_PATH": str(key),
            "KINTLER_SERVER_FOLDER": str(tmp_path / "server"),
            "KINTLER_REMOTE_DIRECTORY": "/opt/kintlercontrol",
            "KINTLER_COMPOSE_PROJECT": "kintlercontrol",
        },
    )
    assert settings(source)["KINTLER_REMOTE_DIRECTORY"] == "/opt/kintlercontrol"
    write_env(source, {"KINTLER_REMOTE_DIRECTORY": "/"})
    with pytest.raises(ValueError):
        settings(source)


def test_deployment_rejects_shell_injection_and_wrong_source(tmp_path: Path):
    key = tmp_path / "key"
    key.write_text("fixture")
    source = tmp_path / ".env"
    write_env(
        source,
        {
            "KINTLER_SSH_HOST": "host&command",
            "KINTLER_SSH_USER": "deploy",
            "KINTLER_SSH_KEY_PATH": str(key),
            "KINTLER_SERVER_FOLDER": str(tmp_path),
        },
    )
    with pytest.raises(ValueError, match="host"):
        settings(source)
    with pytest.raises(ValueError, match=".env"):
        settings(tmp_path / "config.txt")


@pytest.mark.parametrize("failure", ["owner", "pull", "up", None])
def test_remote_update_preserves_existing_deployment(tmp_path: Path, monkeypatch, failure):
    root = tmp_path / "deployment"
    root.mkdir()
    original = "KINTLER_CREDENTIALS='owner'\nKINTLER_IMAGE='old'\nKINTLER_BIND='127.0.0.1:7443'\n"
    (root / ".env").write_text(original)
    (root / "docker-compose.yml").write_text("previous compose")
    (root / "deploy").mkdir()
    (root / "deploy/nginx.conf").write_text("previous proxy")
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            ".env",
            "KINTLER_CREDENTIALS='other'\n"
            if failure == "owner"
            else "KINTLER_CREDENTIALS='owner'\n",
        )
        archive.writestr("docker-compose.yml", "updated compose")
        archive.writestr("deploy/nginx.conf", "updated proxy")
        archive.writestr("owner-public.json", "public registration")
    calls = []
    failed = False

    def run(arguments, **kwargs):
        nonlocal failed
        calls.append(arguments)
        output = ""
        code = 0
        if arguments[:2] == ["systemctl", "list-units"]:
            output = "nginx.service loaded active running\n"
        elif arguments[:2] == ["systemctl", "show"]:
            output = "MainPID=100\nActiveState=active\n"
        elif arguments[:2] == ["docker", "pull"] and failure == "pull":
            code = 1
        elif "up" in arguments and failure == "up" and not failed:
            code = 1
            failed = True
        elif "compose" in arguments and "ps" in arguments:
            output = "relay\n"
        elif arguments[:2] == ["docker", "inspect"]:
            output = json.dumps([{"State": {"Health": {"Status": "healthy"}}, "RestartCount": 0}])
        return subprocess.CompletedProcess(arguments, code, output, "")

    temporary_directory = tempfile.TemporaryDirectory

    def temporary(**kwargs):
        kwargs["dir"] = tmp_path
        return temporary_directory(**kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", temporary)
    config = {
        "directory": str(root),
        "project": "fixture",
        "image": "new",
        "package": base64.b64encode(payload.getvalue()).decode(),
        "checksum": hashlib.sha256(payload.getvalue()).hexdigest(),
        "dry_run": False,
        "caddy": False,
    }
    if failure:
        with pytest.raises(RuntimeError):
            exec(compile(REMOTE, "remote-deployment", "exec"), {"CFG": config})
        assert (root / ".env").read_text() == original
        assert (root / "docker-compose.yml").read_text() == "previous compose"
        assert (root / "deploy/nginx.conf").read_text() == "previous proxy"
        assert not (root / "owner-public.json").exists()
        if failure != "up":
            assert not any("up" in call or "down" in call for call in calls)
    else:
        exec(compile(REMOTE, "remote-deployment", "exec"), {"CFG": config})
        assert "KINTLER_BIND='127.0.0.1:7443'" in (root / ".env").read_text()
        assert "KINTLER_IMAGE='new'" in (root / ".env").read_text()
        assert (root / "docker-compose.yml").read_text() == "updated compose"
    assert not list(tmp_path.glob("fixture-stage-*"))
