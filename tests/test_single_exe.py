import os
import struct
from pathlib import Path

import pytest

from common.identity import new_private_key, protect
from desktop.agent_bundle import MARKER, personalize_agent
from desktop.settings import activate_profile, owner_profile_for_origin, profile
from scripts.configure import write_env


def test_agent_is_one_file_with_public_endpoint_only(tmp_path: Path):
    source = tmp_path / "generic.exe"
    source.write_bytes(b"generic-native-executable")
    target = tmp_path / "Agent.exe"
    personalize_agent(source, target, "https://relay.example.com:9443")
    data = target.read_bytes()
    assert data[-8:] == MARKER
    size = struct.unpack("<I", data[-12:-8])[0]
    assert data[-12 - size : -12] == b"https://relay.example.com:9443"
    assert len(list(tmp_path.glob("*.json"))) == 0
    with pytest.raises(FileExistsError):
        personalize_agent(source, target, "https://other.example.com")


def test_active_owner_survives_executable_relocation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    local = tmp_path / "KintlerControl/owners/test/.env"
    write_env(
        local,
        {
            "KINTLER_URL": "https://relay.example.com",
            "KINTLER_CONTROLLER_ID": "owner",
            "KINTLER_CONTROLLER_PRIVATE_KEY": protect(new_private_key(), "controller"),
        },
    )
    activate_profile(local)
    assert profile("controller") == local.resolve()
    assert owner_profile_for_origin("https://relay.example.com") == local.resolve()
    assert owner_profile_for_origin("https://another.example.com") != local.resolve()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows ShellLink")
def test_shortcut_uses_native_windows_api(tmp_path: Path):
    from desktop.shortcut import create_shortcut

    executable = tmp_path / "Controller.exe"
    executable.write_bytes(b"test executable path")
    link = create_shortcut(executable, tmp_path / "Controller.lnk")
    assert link.is_file() and link.stat().st_size > 100
    with pytest.raises(FileExistsError):
        create_shortcut(executable, link)
