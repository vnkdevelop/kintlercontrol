import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.assemble_release import assemble
from scripts.check_release import check
from scripts.release_layout import BUNDLE_NAME, SKILL_ARCHIVE_NAME, SKILL_FILES


def add_file(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def prepare(directory: Path, private_layer=False):
    windows = directory / "windows"
    backend = directory / "backend"
    windows.mkdir()
    backend.mkdir()
    for name in ("KintlerController.exe", "KintlerAgent.exe"):
        (windows / name).write_bytes(("generic-" + name).encode())
    (windows / "SHA256.json").write_text(
        json.dumps(
            {
                name: hashlib.sha256((windows / name).read_bytes()).hexdigest()
                for name in ("KintlerController.exe", "KintlerAgent.exe")
            }
        )
    )
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as archive:
        add_file(archive, ".env" if private_layer else "generic.txt", b"fixture")
    with tarfile.open(backend / "kintlercontrol-image.tar", "w") as archive:
        add_file(
            archive,
            "manifest.json",
            json.dumps([{"Config": "config.json", "Layers": ["layer.tar"]}]).encode(),
        )
        add_file(archive, "config.json", json.dumps({"config": {"Env": []}}).encode())
        add_file(archive, "layer.tar", layer.getvalue())


def test_complete_bundle_contains_only_required_generic_payload(tmp_path: Path):
    prepare(tmp_path)
    assemble(tmp_path)
    check(tmp_path)
    with zipfile.ZipFile(tmp_path / BUNDLE_NAME) as archive:
        names = archive.namelist()
        assert len([name for name in names if name.endswith(".exe")]) == 2
        assert len([name for name in names if name.endswith(".tar")]) == 1
        assert "skills/kintler-remote/SKILL.md" in names
        assert "server/deploy/existing-nginx.conf" in names
        assert "docs/AI_SETUP.md" in names
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert manifest["version"] == "1.6.3"
        assert "server/start_server.py" in names
        assert "server/kintlercontrol-image.tar.sha256" in names
        assert not any(name.startswith(".private/") or name.endswith("/.env") for name in names)
    with zipfile.ZipFile(tmp_path / SKILL_ARCHIVE_NAME) as archive:
        assert set(archive.namelist()) == {"kintler-remote/" + name for name in SKILL_FILES}
    entries = {line.split()[1] for line in (tmp_path / "SHA256SUMS.txt").read_text().splitlines()}
    assert entries == {BUNDLE_NAME, SKILL_ARCHIVE_NAME, "KintlerController.exe"}


def test_bundle_rejects_private_extra_and_different_bootstrap(tmp_path: Path):
    prepare(tmp_path)
    assemble(tmp_path)
    (tmp_path / "KintlerController.exe").write_bytes(b"different")
    with pytest.raises(ValueError, match="bootstrap"):
        check(tmp_path)
    assemble(tmp_path)
    with zipfile.ZipFile(tmp_path / BUNDLE_NAME, "a") as archive:
        archive.writestr("profiles/.env", "not allowed")
    with pytest.raises(ValueError, match="unexpected"):
        check(tmp_path)


def test_release_rejects_private_docker_layer(tmp_path: Path):
    prepare(tmp_path, private_layer=True)
    assemble(tmp_path)
    with pytest.raises(ValueError, match="Private deployment"):
        check(tmp_path)
