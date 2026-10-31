import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from common.protocol import CHUNK_SIZE, ProtocolError


@dataclass
class Upload:
    temporary: Path
    destination: str
    size: int
    digest: str
    owner: str
    offset: int = 0
    updated: float = 0


class Transfers:
    def __init__(self, root: Path, max_size: int = 256 * 1024 * 1024):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.max_size = max_size
        self.uploads: dict[str, Upload] = {}

    def path(self, value: object, must_exist: bool = False) -> Path:
        if (
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or "\\" in value
            or ":" in value
        ):
            raise ProtocolError("Use a relative path with forward slashes")
        relative = Path(value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or any(p.startswith(".kintler-") for p in relative.parts)
        ):
            raise ProtocolError("Path outside workspace")
        result = self.root / relative
        for part in [result, *result.parents]:
            if part == self.root:
                break
            if part.is_symlink() or part.is_junction():
                raise ProtocolError("Symlinks and junctions are forbidden")
        if not result.resolve().is_relative_to(self.root):
            raise ProtocolError("Path outside workspace")
        if must_exist and not result.is_file():
            raise ProtocolError("File not found")
        return result

    def prune(self) -> None:
        for upload_id, upload in list(self.uploads.items()):
            if time.monotonic() - upload.updated > 600:
                self.abort(upload_id, upload.owner)

    def owned(self, upload_id: object, owner: str) -> tuple[str, Upload]:
        if (
            not isinstance(upload_id, str)
            or upload_id not in self.uploads
            or self.uploads[upload_id].owner != owner
        ):
            raise ProtocolError("Upload not found")
        return upload_id, self.uploads[upload_id]

    def abort(self, upload_id: object, owner: str) -> dict:
        key, upload = self.owned(upload_id, owner)
        upload.temporary.unlink(missing_ok=True)
        del self.uploads[key]
        return {"ok": True}

    def close(self) -> None:
        for key, upload in list(self.uploads.items()):
            self.abort(key, upload.owner)

    def upload(self, args: dict, owner: str) -> dict:
        if not isinstance(args.get("overwrite", False), bool):
            raise ProtocolError("overwrite must be a boolean")
        self.prune()
        operation = args.get("operation")
        if operation == "start":
            path = args.get("path")
            destination = self.path(path)
            size, digest = args.get("size"), args.get("sha256")
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or not 0 <= size <= self.max_size
            ):
                raise ProtocolError("File size limit exceeded")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ProtocolError("Invalid SHA-256")
            if len(self.uploads) >= 8 or any(u.destination == path for u in self.uploads.values()):
                raise ProtocolError("Upload capacity reached or path busy")
            if not destination.parent.is_dir():
                raise ProtocolError("Destination directory does not exist")
            upload_id = secrets.token_hex(16)
            temporary = destination.parent / (".kintler-" + upload_id + ".part")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            self.uploads[upload_id] = Upload(
                temporary, str(path), size, digest, owner, updated=time.monotonic()
            )
            return {"ok": True, "upload_id": upload_id, "chunk_size": CHUNK_SIZE}
        key, upload = self.owned(args.get("upload_id"), owner)
        upload.updated = time.monotonic()
        if operation == "abort":
            return self.abort(key, owner)
        if operation == "chunk":
            try:
                data = base64.b64decode(args.get("data", ""), validate=True)
            except (ValueError, TypeError):
                raise ProtocolError("Invalid base64 chunk") from None
            if (
                not data
                or len(data) > CHUNK_SIZE
                or args.get("offset") != upload.offset
                or upload.offset + len(data) > upload.size
            ):
                raise ProtocolError("Invalid chunk size or offset")
            self.path(upload.destination)
            with upload.temporary.open("ab") as handle:
                handle.write(data)
            upload.offset += len(data)
            return {"ok": True, "offset": upload.offset}
        if operation == "finish":
            if upload.offset != upload.size:
                raise ProtocolError("Upload is incomplete")
            if sha256_file(upload.temporary) != upload.digest:
                self.abort(key, owner)
                raise ProtocolError("SHA-256 mismatch")
            destination = self.path(upload.destination)
            if destination.exists() and not args.get("overwrite", False):
                raise ProtocolError("Destination exists; explicit overwrite is required")
            with upload.temporary.open("ab") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            if args.get("overwrite", False):
                os.replace(upload.temporary, destination)
            else:
                os.link(upload.temporary, destination)
                upload.temporary.unlink()
            del self.uploads[key]
            return {"ok": True, "size": upload.size, "sha256": upload.digest}
        raise ProtocolError("Unknown upload operation")

    def download(self, args: dict) -> dict:
        path = self.path(args.get("path"), must_exist=True)
        size = path.stat().st_size
        if size > self.max_size:
            raise ProtocolError("File size limit exceeded")
        if args.get("operation", "stat") == "stat":
            return {"ok": True, "size": size, "sha256": sha256_file(path), "chunk_size": CHUNK_SIZE}
        if args.get("operation") != "chunk":
            raise ProtocolError("Unknown download operation")
        offset = args.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= size:
            raise ProtocolError("Invalid download offset")
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(CHUNK_SIZE)
        return {
            "ok": True,
            "offset": offset,
            "data": base64.b64encode(data).decode(),
            "eof": offset + len(data) >= size,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while data := handle.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()
