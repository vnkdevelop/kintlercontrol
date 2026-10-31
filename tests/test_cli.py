import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from client import kintler_cli
from tests.conftest import Environment


async def test_login_saves_validated_env(
    environment: Environment, tmp_path: Path, monkeypatch, capsys
):
    destination = tmp_path / "operator" / ".env"
    monkeypatch.setattr(kintler_cli.getpass, "getpass", lambda prompt: environment.operator.secret)
    args = argparse.Namespace(
        url=environment.url,
        principal=environment.operator.principal,
        env=str(destination),
        local_http=True,
        ca_file=None,
    )
    await kintler_cli.login(args)
    values = dotenv_values(destination)
    assert values["KINTLER_CONTROLLER_SECRET"] == environment.operator.secret
    assert environment.operator.secret not in capsys.readouterr().out


async def test_cli_nodes_and_call(environment: Environment, monkeypatch, capsys):
    monkeypatch.setenv("KINTLER_URL", environment.url)
    monkeypatch.setenv("KINTLER_CONTROLLER_ID", environment.operator.principal)
    monkeypatch.setenv("KINTLER_CONTROLLER_SECRET", environment.operator.secret)
    monkeypatch.setenv("KINTLER_ALLOW_LOCAL_HTTP", "1")
    await kintler_cli.run(argparse.Namespace(action="nodes", env="missing.env"))
    assert "node_a" in capsys.readouterr().out
    await kintler_cli.run(
        argparse.Namespace(
            action="call", env="missing.env", node="node_a", tool="sysinfo", args="{}", timeout=10
        )
    )
    assert json.loads(capsys.readouterr().out)["ok"]


async def test_console_switching(environment: Environment, monkeypatch, capsys):
    lines = iter(
        [
            "!nodes",
            "!use node_a",
            "!tool sysinfo {}",
            "!use node_b",
            "!shell bash",
            "!cancel",
            "!exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    await kintler_cli.interactive(environment.operator)
    assert "node_a" in capsys.readouterr().out


def test_terminal_control_characters_removed():
    assert "\x1b" not in kintler_cli.printable("\x1b[2Jhello\x00")
