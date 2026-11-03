import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.modules.applications import find_in_folders, short_name
from agent.modules.desktop import open_application
from common.protocol import ProtocolError


def shortcut_fixture(tmp_path: Path, monkeypatch, links: dict):
    for name, target in links.items():
        (tmp_path / name).write_bytes(b"fixture")
        Path(target[0]).parent.mkdir(parents=True, exist_ok=True)
        Path(target[0]).write_bytes(b"fixture")
    monkeypatch.setattr("agent.modules.applications.read_shortcut", lambda path: links[path.name])


def test_short_name_never_turns_an_explicit_path_into_an_alias():
    assert short_name("ZABOR.exe") == "zabor"
    assert short_name("Zabor") == "zabor"
    for value in ("", ".exe", "C:\\Apps\\Zabor.exe", "apps/zabor.exe", "shell:AppsFolder"):
        assert short_name(value) is None


def test_start_menu_alias_preserves_target_arguments_and_cwd(tmp_path, monkeypatch):
    target = (str(tmp_path / "installed/AppActual.exe"), '--profile "some path"', str(tmp_path))
    shortcut_fixture(tmp_path, monkeypatch, {"ZABOR.lnk": target})
    assert find_in_folders("zabor.exe", [tmp_path]) == target
    assert find_in_folders("zabor", [tmp_path]) == target


def test_executable_name_matches_a_differently_named_shortcut(tmp_path, monkeypatch):
    target = (str(tmp_path / "installed/Zabor.exe"), "", None)
    shortcut_fixture(tmp_path, monkeypatch, {"Localized app name.lnk": target})
    assert find_in_folders("zabor.exe", [tmp_path]) == target


def test_duplicate_shortcuts_are_deduplicated_by_target(tmp_path, monkeypatch):
    target = (str(tmp_path / "installed/Zabor.exe"), "", None)
    shortcut_fixture(tmp_path, monkeypatch, {"ZABOR.lnk": target, "Zabor copy.lnk": target})
    assert find_in_folders("zabor.exe", [tmp_path]) == target


def test_distinct_matching_targets_are_not_selected_arbitrarily(tmp_path, monkeypatch):
    links = {
        "ZABOR.lnk": (str(tmp_path / "one/Zabor.exe"), "", None),
        "Other Zabor.lnk": (str(tmp_path / "two/Zabor.exe"), "", None),
    }
    shortcut_fixture(tmp_path, monkeypatch, links)
    with pytest.raises(ProtocolError, match="Several"):
        find_in_folders("zabor.exe", [tmp_path])


def test_no_fuzzy_matching_or_broken_shortcut_launch(tmp_path, monkeypatch):
    target = (str(tmp_path / "installed/ZaborHelper.exe"), "", None)
    shortcut_fixture(tmp_path, monkeypatch, {"Zabor Helper.lnk": target})
    assert find_in_folders("zabor.exe", [tmp_path]) is None
    Path(target[0]).unlink()
    assert find_in_folders("ZaborHelper.exe", [tmp_path]) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows open adapter")
def test_not_found_fallback_preserves_explicit_argument_boundaries(tmp_path, monkeypatch):
    start = Mock(side_effect=[FileNotFoundError(), None])
    monkeypatch.setattr(os, "startfile", start)
    lookup = Mock(return_value=("C:\\Apps\\Zabor.exe", "--default-profile", "C:\\Apps"))
    monkeypatch.setattr("agent.modules.desktop.find_installed", lookup)
    result = open_application({"app": "zabor.exe", "arguments": ["Привет & мир"]}, tmp_path)
    assert result["ok"] and result["resolution_source"] == "start_menu"
    lookup.assert_called_once_with("zabor.exe")
    assert start.call_args_list[1].args == (
        "C:\\Apps\\Zabor.exe",
        "open",
        '--default-profile "Привет & мир"',
        "C:\\Apps",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows open adapter")
def test_permission_denial_never_tries_an_alternative_program(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "startfile", Mock(side_effect=PermissionError()))
    lookup = Mock()
    monkeypatch.setattr("agent.modules.desktop.find_installed", lookup)
    with pytest.raises(PermissionError):
        open_application({"app": "zabor.exe"}, tmp_path)
    lookup.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows ShellLink")
def test_real_shell_link_can_be_read_without_launching_it(tmp_path):
    import sys

    from desktop.shortcut import create_shortcut, read_shortcut

    path = tmp_path / "Zabor.lnk"
    create_shortcut(Path(sys.executable), path)
    target, arguments, cwd = read_shortcut(path)
    assert Path(target).resolve() == Path(sys.executable).resolve()
    assert arguments == ""
    assert Path(cwd).resolve() == Path(sys.executable).resolve().parent
