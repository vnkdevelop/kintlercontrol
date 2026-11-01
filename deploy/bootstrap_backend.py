import argparse
import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def initialize(
    root: Path, public_file: Path, certificate: str | None = None, key: str | None = None
) -> None:
    registration = json.loads(public_file.read_text(encoding="utf-8"))
    owner = registration["id"]
    public = registration["public_key"]
    url = registration["url"]
    parsed = urlsplit(url)
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", owner)
        or len(base64.b64decode(public, validate=True)) != 32
    ):
        raise ValueError("Invalid owner public registration")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or any(c.isspace() or c in "'\\" for c in url)
    ):
        raise ValueError("Public URL must be an HTTPS origin without credentials")
    tools = [
        "shell",
        "sysinfo",
        "ps",
        "kill",
        "screenshot",
        "upload",
        "download",
        "registry",
        "services",
        "tasks",
    ]
    credential = {
        "id": owner,
        "role": "controller",
        "public_key": public,
        "nodes": ["*"],
        "tools": tools,
        "can_approve": True,
    }
    values = {
        "KINTLER_CREDENTIALS": json.dumps([credential], separators=(",", ":")),
        "KINTLER_URL": url,
        "KINTLER_IMAGE": "ghcr.io/vnkdevelop/kintlercontrol:1.3.0",
        "KINTLER_DATABASE": "/app/data/kintler.sqlite3",
        "KINTLER_ALLOW_LOCAL_HTTP": "0",
        "KINTLER_TLS_CERT": certificate or f"/etc/letsencrypt/live/{parsed.hostname}/fullchain.pem",
        "KINTLER_TLS_KEY": key or f"/etc/letsencrypt/live/{parsed.hostname}/privkey.pem",
    }
    root.mkdir(parents=True, exist_ok=True)
    for value in values.values():
        if "'" in value or "\n" in value or "\r" in value:
            raise ValueError("Invalid environment value")
    target = root / ".env"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        for name, value in values.items():
            output.write(f"{name}='{value}'\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time server setup using only the controller's public key"
    )
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    parser.add_argument("--owner", type=Path, default=Path("owner-public.json"))
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()
    initialize(args.directory, args.owner, args.tls_cert, args.tls_key)
    print("Server .env created. No controller or agent private keys were uploaded.")


if __name__ == "__main__":
    main()
