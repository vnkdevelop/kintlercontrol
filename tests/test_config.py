import argparse
import json
import secrets
from pathlib import Path

import pytest
from dotenv import dotenv_values

from agent.kintler_agent import AgentSettings
from scripts.configure import configure
from server.config import Settings


def test_enrollment_without_secret_output(tmp_path: Path, capsys, monkeypatch):
    server = tmp_path / ".env"
    configure(
        argparse.Namespace(
            env=str(server), action="init-server", url="https://localhost:8443", local_http=False
        )
    )
    configure(
        argparse.Namespace(
            env=str(server),
            action="add-agent",
            id="device",
            node_id="node_a",
            name="PC",
            tools="all",
            output_dir=str(tmp_path / "agent"),
        )
    )
    configure(
        argparse.Namespace(
            env=str(server),
            action="add-controller",
            id="operator",
            nodes="node_a",
            tools="all",
            output_dir=str(tmp_path / "operator"),
        )
    )
    values = dotenv_values(server)
    monkeypatch.setenv("KINTLER_CREDENTIALS", values["KINTLER_CREDENTIALS"])
    settings = Settings.from_env()
    output = capsys.readouterr().out
    for credential in settings.credentials.values():
        assert credential.secret not in output
    assert settings.credentials["operator"].permits("node_a", "shell")
    assert not settings.credentials["operator"].permits("node_b", "shell")
    assert dotenv_values(tmp_path / "agent" / ".env")["KINTLER_AGENT_TOOLS"]
    configure(argparse.Namespace(env=str(server), action="remove", id="device"))
    assert len(json.loads(dotenv_values(server)["KINTLER_CREDENTIALS"])) == 1


@pytest.mark.parametrize("value", ["", "[]", "{}", "not-json", '[{"id":"x"}]'])
def test_config_rejects_invalid_credentials(monkeypatch, value: str):
    monkeypatch.setenv("KINTLER_CREDENTIALS", value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_duplicate_secret_rejected(monkeypatch):
    secret = secrets.token_urlsafe(48)
    monkeypatch.setenv(
        "KINTLER_CREDENTIALS",
        json.dumps(
            [
                {"id": "a", "role": "agent", "secret": secret, "node_id": "node_a"},
                {"id": "b", "role": "agent", "secret": secret, "node_id": "node_b"},
            ]
        ),
    )
    with pytest.raises(ValueError, match="different secret"):
        Settings.from_env()


def test_agent_relative_workspace_uses_env_directory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KINTLER_URL", "https://localhost:8443")
    monkeypatch.setenv("KINTLER_AGENT_ID", "device")
    monkeypatch.setenv("KINTLER_AGENT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("KINTLER_WORKSPACE", "workspace")
    monkeypatch.setattr("agent.kintler_agent.node_id", lambda: "node_test")
    settings = AgentSettings.from_env(tmp_path)
    assert settings.root == tmp_path / "workspace"
