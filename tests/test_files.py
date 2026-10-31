import base64
import hashlib
from pathlib import Path

import pytest

from agent.modules.fs_transfer import Transfers
from common.protocol import ProtocolError


@pytest.fixture
def transfer(tmp_path: Path) -> Transfers:
    return Transfers(tmp_path / "workspace", max_size=100)


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        "C:/Windows/file",
        "folder\\file",
        ".kintler-hidden",
        "a/../../escape",
        "file:stream",
    ],
)
def test_paths(transfer: Transfers, path: str):
    with pytest.raises(ProtocolError):
        transfer.path(path)


def test_upload_ownership_hash_and_atomicity(transfer: Transfers):
    data = b"test-content"
    started = transfer.upload(
        {
            "operation": "start",
            "path": "file.bin",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "owner",
    )
    upload_id = started["upload_id"]
    with pytest.raises(ProtocolError, match="not found"):
        transfer.upload(
            {
                "operation": "chunk",
                "upload_id": upload_id,
                "offset": 0,
                "data": base64.b64encode(data).decode(),
            },
            "other",
        )
    assert not (transfer.root / "file.bin").exists()
    with pytest.raises(ProtocolError, match="offset"):
        transfer.upload(
            {
                "operation": "chunk",
                "upload_id": upload_id,
                "offset": 1,
                "data": base64.b64encode(data).decode(),
            },
            "owner",
        )
    transfer.upload(
        {
            "operation": "chunk",
            "upload_id": upload_id,
            "offset": 0,
            "data": base64.b64encode(data).decode(),
        },
        "owner",
    )
    result = transfer.upload({"operation": "finish", "upload_id": upload_id}, "owner")
    assert result["ok"]
    assert (transfer.root / "file.bin").read_bytes() == data
    assert not list(transfer.root.glob("*.part"))
    assert transfer.download({"path": "file.bin"})["sha256"] == hashlib.sha256(data).hexdigest()


def test_invalid_hash_cleanup(transfer: Transfers):
    upload_id = transfer.upload(
        {"operation": "start", "path": "file", "size": 1, "sha256": "0" * 64}, "owner"
    )["upload_id"]
    transfer.upload(
        {"operation": "chunk", "upload_id": upload_id, "offset": 0, "data": "YQ=="}, "owner"
    )
    with pytest.raises(ProtocolError, match="SHA-256"):
        transfer.upload({"operation": "finish", "upload_id": upload_id}, "owner")
    assert not transfer.uploads
    assert not list(transfer.root.iterdir())


def test_size_limit_and_disconnect_cleanup(transfer: Transfers):
    with pytest.raises(ProtocolError, match="size"):
        transfer.upload(
            {"operation": "start", "path": "big", "size": 101, "sha256": "0" * 64}, "owner"
        )
    transfer.upload(
        {
            "operation": "start",
            "path": "small",
            "size": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
        "owner",
    )
    transfer.close()
    assert not list(transfer.root.iterdir())


def test_symlink(transfer: Transfers, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.write_text("private")
    link = transfer.root / "link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlinks require Developer Mode or elevated permissions")
    with pytest.raises(ProtocolError):
        transfer.download({"path": "link"})
