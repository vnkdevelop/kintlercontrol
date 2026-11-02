import json
import os
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from desktop import installation, terminal_entry
from scripts.install_skill import install
from scripts.release_layout import SKILL_FILES


def executable(tmp_path: Path, name="portable/Controller.exe"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"generic executable fixture")
    return path


def test_portable_location_and_profile_paths_are_recorded_without_secret_values(tmp_path: Path):
    controller = executable(tmp_path)
    owner = tmp_path / "owner/.env"
    owner.parent.mkdir()
    owner.write_text(
        "KINTLER_URL=https://private-relay.example\nKINTLER_CONTROLLER_PRIVATE_KEY=private-key-fixture\n"
    )
    deployment = tmp_path / "custom deployment"
    deployment.mkdir()
    path = installation.register_installation(controller, deployment, owner, home=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "private-relay.example" not in text and "private-key-fixture" not in text
    data = installation.installation_information(tmp_path)
    assert data["registered"] and data["controller"] == {
        "path": str(controller.resolve()),
        "exists": True,
    }
    assert data["deployments"][0]["server_kit"] == str(deployment / "server-kit.zip")
    assert data["deployments"][0]["agent_executable"] == str(deployment / "Agent/KintlerAgent.exe")
    assert not list(path.parent.glob(".kintler-installation-*"))


def test_move_refreshes_one_map_while_skill_pointer_remains_stable(tmp_path: Path):
    controller = executable(tmp_path)
    installation.register_installation(controller, home=tmp_path)
    pointer = json.loads(installation.skill_local_context(tmp_path, controller))
    moved = tmp_path / "another folder/Controller.exe"
    moved.parent.mkdir()
    controller.rename(moved)
    assert not installation.installation_information(tmp_path)["controller"]["exists"]
    installation.register_installation(moved, home=tmp_path)
    assert pointer["installation_file"] == str(installation.installation_file(tmp_path))
    assert installation.installation_information(tmp_path)["controller"]["path"] == str(
        moved.resolve()
    )


def test_concurrent_updates_retain_multiple_deployments(tmp_path: Path):
    controller = executable(tmp_path)
    folders = [tmp_path / f"deployment-{index}" for index in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda folder: installation.register_installation(
                    controller, folder, home=tmp_path
                ),
                folders,
            )
        )
    assert {
        entry["root"] for entry in installation.installation_information(tmp_path)["deployments"]
    } == {str(folder.resolve()) for folder in folders}


def test_unknown_registry_is_not_overwritten(tmp_path: Path):
    controller = executable(tmp_path)
    path = installation.installation_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"user_work":"keep"}')
    with pytest.raises(ValueError, match="managed"):
        installation.register_installation(controller, home=tmp_path)
    assert json.loads(path.read_text()) == {"user_work": "keep"}
    assert not installation.installation_information(tmp_path)["registered"]


def test_skill_installer_writes_personal_pointer_but_dry_run_does_not(tmp_path: Path):
    controller = executable(tmp_path)
    result = install("shared", dry_run=True, home=tmp_path, controller=controller)
    assert result["installed"] and not installation.installation_file(tmp_path).exists()
    assert not (tmp_path / ".agents").exists()
    result = install("shared", home=tmp_path, controller=controller)
    sidecar = Path(result["installed"][0]) / "references/local-installation.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["controller_path_hint"] == str(
        controller.resolve()
    )
    assert json.loads(sidecar.read_text(encoding="utf-8"))["installation_file"] == str(
        installation.installation_file(tmp_path)
    )
    assert installation.installation_file(tmp_path).is_file()
    assert install("shared", home=tmp_path, controller=controller)["unchanged"]


def test_read_only_installation_info_creates_no_registry(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["Controller.exe", "--installation-info"])
    network = Mock()
    monkeypatch.setattr(terminal_entry, "KintlerClient", network)
    terminal_entry.main()
    data = json.loads(capsys.readouterr().out)
    assert not data["registered"] and not data["controller"]["exists"]
    assert not list(tmp_path.iterdir())
    network.assert_not_called()


def test_first_gui_start_registers_actual_executable(tmp_path: Path, monkeypatch):
    controller = executable(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(controller))
    monkeypatch.setattr(sys, "argv", [str(controller)])
    monkeypatch.setattr(terminal_entry, "terminal_console", lambda title: nullcontext())
    monkeypatch.setattr("desktop.terminal_controller.TerminalController", Mock())
    terminal_entry.main()
    assert installation.installation_information()["controller"]["path"] == str(
        controller.resolve()
    )


def test_normal_frozen_setup_records_chosen_output_without_touching_other_profiles(
    tmp_path: Path, monkeypatch
):
    from desktop.deployment import create_deployment

    controller = executable(tmp_path)
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    (binaries / "KintlerAgent.exe").write_bytes(b"generic agent fixture")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(controller))
    output = tmp_path / "user selected deployment"
    result = create_deployment("https://relay.example.com", output, binaries, mode="existing")
    data = installation.installation_information()
    assert data["deployments"][0]["root"] == str(output.resolve())
    assert data["deployments"][0]["owner_profile_path"] == result["owner_profile"]
    assert data["deployments"][0]["active"]
    assert "relay.example.com" not in installation.installation_file().read_text(encoding="utf-8")


def test_public_skill_and_wheel_allowlists_exclude_personal_context():
    root = Path(__file__).resolve().parents[1]
    assert "references/local-installation.json" not in SKILL_FILES
    configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert ".agents/skills/kintler-remote" not in force_include
    assert not any("local-installation.json" in key for key in force_include)


def test_exe_packaging_uses_only_generic_skill_files(tmp_path: Path, monkeypatch):
    from scripts import build_windows

    root = tmp_path / "source"
    (root / "dist/windows").mkdir(parents=True)
    (root / "dist/windows/KintlerAgent.exe").write_bytes(b"agent fixture")
    monkeypatch.setattr(build_windows, "__file__", str(root / "scripts/build_windows.py"))
    monkeypatch.setattr(sys, "argv", ["build", "--only", "controller"])
    monkeypatch.setattr(build_windows, "make_icon", lambda path, source: path.write_bytes(b"icon"))
    calls = []

    def package(arguments, env=None):
        calls.append(arguments)
        (root / "dist/windows/KintlerController.exe").write_bytes(b"controller fixture")

    monkeypatch.setattr(build_windows, "command", package)
    if os.name == "nt":
        build_windows.main()
        data = [
            calls[0][index + 1] for index, value in enumerate(calls[0]) if value == "--add-data"
        ]
        skill_entries = [
            value for value in data if "skills/kintler-remote" in value.replace("\\", "/")
        ]
        assert len(skill_entries) == len(SKILL_FILES)
        assert all("local-installation.json" not in value for value in data)
