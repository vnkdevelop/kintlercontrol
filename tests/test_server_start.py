import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from deploy import start_server


def image_file(directory: Path, image: str, architecture="amd64"):
    data = json.dumps(
        {
            "os": "linux",
            "architecture": architecture,
            "config": {"Env": []},
            "rootfs": {"type": "layers", "diff_ids": ["fixture-layer"]},
        }
    ).encode()
    manifest = json.dumps([{"RepoTags": [image], "Config": "config.json", "Layers": []}]).encode()
    path = directory / "kintlercontrol-image.tar"
    with tarfile.open(path, "w") as archive:
        for name, content in (("manifest.json", manifest), ("config.json", data)):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    (directory / "kintlercontrol-image.tar.sha256").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  kintlercontrol-image.tar\n"
    )
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def server_kit(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text("configured owner fixture")
    (tmp_path / "docker-compose.yml").write_text("fixture compose")
    monkeypatch.setattr(start_server.platform, "system", lambda: "Linux")
    monkeypatch.setattr(start_server.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(start_server.shutil, "which", lambda command: "docker")
    calls = []
    state = {"existing": [], "image_id": "unused", "healthy": True, "other_owner": False}
    image = "kintlercontrol:fixture"

    def run(arguments, timeout=180):
        calls.append(arguments)
        if "config" in arguments:
            return json.dumps(
                {
                    "services": {
                        "relay": {
                            "image": image,
                            "environment": {
                                "KINTLER_CREDENTIALS": json.dumps(
                                    [{"id": "owner", "role": "controller"}]
                                )
                            },
                        }
                    }
                }
            )
        if "ps" in arguments:
            return "\n".join(state["existing"]) if "-aq" in arguments else "relay"
        if arguments[:3] == ["docker", "image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": state["image_id"],
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {"Env": []},
                        "RootFS": {
                            "Layers": [
                                "different-layer"
                                if state["image_id"] == "different"
                                else "fixture-layer"
                            ]
                        },
                    }
                ]
            )
        if arguments[:2] == ["docker", "inspect"]:
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project.working_dir": "other"
                                if state["other_owner"]
                                else str(tmp_path.resolve()),
                                "com.docker.compose.service": "relay",
                            }
                        },
                        "State": {
                            "Status": "running",
                            "Health": {"Status": "healthy" if state["healthy"] else "unhealthy"},
                        },
                        "RestartCount": 0,
                    }
                ]
            )
        return "ready"

    monkeypatch.setattr(start_server, "run", run)
    return tmp_path, image, calls, state


def test_portable_start_verifies_loads_and_starts_without_registry_pull(server_kit):
    directory, image, calls, state = server_kit
    state["image_id"] = image_file(directory, image)
    original = (directory / ".env").read_bytes()
    result = start_server.start(directory)
    assert result["healthy"] and result["portable_image"]
    assert any(call[:2] == ["docker", "load"] for call in calls)
    assert not any(call[:2] == ["docker", "pull"] for call in calls)
    assert any(call[-5:] == ["up", "-d", "--no-build", "--pull", "missing"] for call in calls)
    assert not any("down" in call or "stop" in call or "rm" in call for call in calls)
    assert (directory / ".env").read_bytes() == original


def test_registry_start_without_portable_image(server_kit):
    directory, image, calls, _ = server_kit
    result = start_server.start(directory)
    assert result["healthy"] and not result["portable_image"]
    assert ["docker", "pull", image] in calls


def test_containerd_manifest_id_does_not_replace_content_verification(server_kit):
    directory, image, _, state = server_kit
    image_file(directory, image)
    state["image_id"] = "containerd-manifest-id"
    assert start_server.start(directory)["healthy"]


def test_image_config_omitted_null_fields_are_not_content_changes():
    assert start_server.without_null_fields(
        {
            "Env": ["A=1"],
            "Labels": None,
            "Healthcheck": {"Test": ["CMD", "true"], "StartInterval": None},
        }
    ) == {"Env": ["A=1"], "Healthcheck": {"Test": ["CMD", "true"]}}
    assert start_server.without_null_fields(
        {"ExposedPorts": {"7443/tcp": {}}, "ArgsEscaped": False}
    ) == {"ExposedPorts": {"7443/tcp": {}}, "ArgsEscaped": False}


def test_check_never_loads_pulls_or_starts(server_kit):
    directory, image, calls, state = server_kit
    state["image_id"] = image_file(directory, image)
    assert start_server.start(directory, check=True)["checked"]
    assert not any("load" in call or "pull" in call or "up" in call for call in calls)


@pytest.mark.parametrize(
    "failure", ["checksum", "checksum_missing", "architecture", "tag", "identity"]
)
def test_invalid_portable_image_never_starts_containers(server_kit, failure):
    directory, image, calls, state = server_kit
    state["image_id"] = image_file(
        directory,
        "wrong-tag" if failure == "tag" else image,
        "arm64" if failure == "architecture" else "amd64",
    )
    if failure == "checksum":
        (directory / "kintlercontrol-image.tar").write_bytes(b"corrupt")
    elif failure == "checksum_missing":
        (directory / "kintlercontrol-image.tar.sha256").unlink()
    elif failure == "identity":
        state["image_id"] = "different"
    with pytest.raises(RuntimeError):
        start_server.start(directory)
    assert not any("up" in call or "pull" in call for call in calls)


def test_another_compose_project_is_never_replaced(server_kit):
    directory, _, calls, state = server_kit
    state.update(existing=["other"], other_owner=True)
    with pytest.raises(RuntimeError, match="another directory"):
        start_server.start(directory)
    assert not any("load" in call or "pull" in call or "up" in call for call in calls)


def test_unhealthy_start_is_bounded_and_never_deletes_volumes(server_kit):
    directory, _, calls, state = server_kit
    state["healthy"] = False
    with pytest.raises(RuntimeError, match="did not become healthy"):
        start_server.start(directory, timeout=0)
    assert not any("down" in call or "rm" in call or "prune" in call for call in calls)


def test_missing_docker_and_wrong_platform_fail_before_actions(server_kit, monkeypatch):
    directory, _, calls, _ = server_kit
    monkeypatch.setattr(start_server.shutil, "which", lambda command: None)
    with pytest.raises(RuntimeError, match="Install Docker"):
        start_server.start(directory)
    monkeypatch.setattr(start_server.platform, "system", lambda: "Windows")
    with pytest.raises(RuntimeError, match="Linux server"):
        start_server.start(directory)
    assert not calls


def test_launcher_errors_never_print_command_credentials(monkeypatch):
    monkeypatch.setattr(
        start_server.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, "", "private server diagnostics")),
    )
    with pytest.raises(RuntimeError) as error:
        start_server.run(["docker", "compose", "config"])
    assert "private server diagnostics" not in str(error.value)
