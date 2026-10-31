import io
import json
import zipfile
from pathlib import Path

import pytest
from dotenv import dotenv_values

from desktop.deployment import create_deployment
from desktop.settings import profile
from scripts.privacy_check import inspect_text


def test_generic_kit_contains_only_public_owner_data(tmp_path: Path):
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    (binaries / "KintlerAgent.exe").write_bytes(b"test-native-client")
    output = tmp_path / "deployment"
    owner = tmp_path / "owner/.env"
    result = create_deployment("https://relay.example.com", output, binaries, owner)
    assert Path(result["server_kit"]).is_file()
    assert (output / "Agent/kintler-agent.json").read_text().find("relay.example.com") >= 0
    with zipfile.ZipFile(result["server_kit"]) as archive:
        assert "docker-compose.caddy.yml" in archive.namelist()
        credentials = json.loads(
            dotenv_values(stream=io.StringIO(archive.read(".env").decode()))["KINTLER_CREDENTIALS"]
        )
        assert credentials[0]["can_approve"] and "public_key" in credentials[0]
        assert "secret" not in credentials[0] and "private_key" not in credentials[0]
    with pytest.raises(ValueError, match="overwritten"):
        create_deployment("https://relay.example.com", output, binaries, owner)


def test_setup_rejects_bad_origins_before_overwriting(tmp_path: Path):
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    (binaries / "KintlerAgent.exe").write_bytes(b"client")
    for url in ("http://example.com", "https://user:pass@example.com", "https://example.com:8443"):
        with pytest.raises(ValueError):
            create_deployment(url, tmp_path / "deployment", binaries, tmp_path / "owner/.env")
        assert not (tmp_path / "deployment").exists()


def test_public_profiles_are_isolated(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert "KintlerControl" in str(profile("controller"))
    assert profile("controller") != tmp_path / "Kintler/controller/.env"


def test_privacy_policy_detects_deployment_information():
    assert inspect_text("sample.py", b"custom-private-domain", ["custom-private-domain"])
    assert inspect_text("sample.py", json.dumps({"secret": "x" * 32}).encode(), [])
    assert not inspect_text("sample.py", b"secret = secrets.token_urlsafe(48)", [])
