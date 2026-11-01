import argparse
import json
from pathlib import Path

from common.identity import new_private_key, protect, public_key, unlock
from common.protocol import client_tls, identifier
from desktop.settings import profile
from scripts.configure import write_env


def prepare(url: str, destination: Path, local_profile: Path | None = None) -> Path:
    client_tls(url)
    path = local_profile or profile("controller")
    if path.exists():
        from dotenv import dotenv_values

        values = dotenv_values(path)
        if not values.get("KINTLER_CONTROLLER_PRIVATE_KEY"):
            raise ValueError(
                "An existing legacy controller profile will not be overwritten; migrate it explicitly"
            )
        private = unlock(str(values["KINTLER_CONTROLLER_PRIVATE_KEY"]), "controller")
        owner = identifier(values.get("KINTLER_CONTROLLER_ID"))
        if str(values.get("KINTLER_URL", "")).rstrip("/") != url.rstrip("/"):
            raise ValueError(
                "Existing owner profile points to another server; it will not be changed"
            )
    else:
        private = new_private_key()
        owner = "owner"
        write_env(
            path,
            {
                "KINTLER_URL": url.rstrip("/"),
                "KINTLER_CONTROLLER_ID": owner,
                "KINTLER_CONTROLLER_PRIVATE_KEY": protect(private, "controller"),
            },
            create_only=True,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {"id": owner, "public_key": public_key(private), "url": url.rstrip("/")}, indent=2
        ),
        encoding="utf-8",
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a local DPAPI owner key and export only public server registration"
    )
    parser.add_argument("--url", required=True, help="Your own public HTTPS relay origin")
    parser.add_argument(
        "--output", type=Path, default=Path(".private/deployment/server/owner-public.json")
    )
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    path = prepare(args.url, args.output, args.profile)
    print(f"Owner profile protected locally: {path}")
    print(f"Only public registration exported: {args.output}")


if __name__ == "__main__":
    main()
