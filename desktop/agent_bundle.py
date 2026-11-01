import struct
from pathlib import Path

from common.protocol import client_tls

MARKER = b"KCAGENT1"


def personalize_agent(source: Path, destination: Path, url: str) -> None:
    url = url.strip().rstrip("/")
    client_tls(url)
    payload = url.encode("utf-8")
    if not 1 <= len(payload) <= 4096:
        raise ValueError("Public endpoint is too long")
    data = source.read_bytes()
    if len(data) >= 12 and data[-8:] == MARKER:
        size = struct.unpack("<I", data[-12:-8])[0]
        if not 1 <= size <= 4096 or len(data) < size + 12:
            raise ValueError("Source agent has malformed endpoint data")
        data = data[: -size - 12]
    if destination.exists():
        raise FileExistsError("Existing configured agent will not be overwritten")
    destination.write_bytes(data + payload + struct.pack("<I", len(payload)) + MARKER)
