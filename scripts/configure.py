import argparse
import json
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

from common.protocol import SAFE_TOOLS, TOOLS, client_tls, identifier


def write_env(path: Path, values: dict[str, str], create_only: bool = False) -> None:
    if path.name != ".env":
        raise ValueError("Secret configuration files must be named .env")
    if create_only and path.exists():
        raise ValueError(f"Configuration already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    for key, value in values.items():
        set_key(str(path), key, value)
    if os.name != "nt":
        path.chmod(0o600)


def selected_tools(value: str) -> list[str]:
    tools = (
        sorted(TOOLS)
        if value == "all"
        else sorted({t.strip() for t in value.split(",") if t.strip()})
    )
    if not tools or not set(tools) <= TOOLS:
        raise ValueError("Unknown or empty tool list")
    return tools


def configure(args: argparse.Namespace) -> None:
    path = Path(args.env).resolve()
    if args.action == "init-server":
        client_tls(args.url, args.local_http)
        write_env(
            path,
            {
                "KINTLER_CREDENTIALS": "[]",
                "KINTLER_URL": args.url.rstrip("/"),
                "KINTLER_DATABASE": "data/kintler.sqlite3",
                "KINTLER_ALLOW_LOCAL_HTTP": "1" if args.local_http else "0",
                "KINTLER_TLS_CERT": f"/etc/letsencrypt/live/{urlsplit(args.url).hostname}/fullchain.pem",
                "KINTLER_TLS_KEY": f"/etc/letsencrypt/live/{urlsplit(args.url).hostname}/privkey.pem",
                "KINTLER_IMAGE": "ghcr.io/vnkdevelop/kintlercontrol:1.5.1",
            },
            create_only=True,
        )
        print(
            f"Created server configuration: {path}. Enroll an operator with --can-approve before startup."
        )
        return
    values = dotenv_values(path)
    if not values.get("KINTLER_CREDENTIALS") or not values.get("KINTLER_URL"):
        raise ValueError("Initialize server configuration first")
    items = json.loads(str(values["KINTLER_CREDENTIALS"]))
    principal = identifier(args.id)
    if args.action == "grant-approval":
        matches = [
            item for item in items if item["id"] == principal and item["role"] == "controller"
        ]
        if len(matches) != 1:
            raise ValueError("Controller not found")
        matches[0]["can_approve"] = True
        write_env(path, {"KINTLER_CREDENTIALS": json.dumps(items, separators=(",", ":"))})
        print("Approval permission granted. Recreate relay to apply environment changes.")
        return
    if args.action == "remove":
        filtered = [item for item in items if item["id"] != principal]
        if len(filtered) == len(items):
            raise ValueError("Principal not found")
        write_env(path, {"KINTLER_CREDENTIALS": json.dumps(filtered, separators=(",", ":"))})
        print("Principal removed. Restart relay to revoke active credentials and sessions.")
        return
    if any(item["id"] == principal for item in items):
        raise ValueError("Principal already exists; remove it explicitly before re-enrollment")
    output = Path(args.output_dir).resolve() / ".env"
    if output == path or output.exists():
        raise ValueError("Use a separate empty output directory for the device/operator .env")
    secret = secrets.token_urlsafe(48)
    local = {
        "KINTLER_URL": str(values["KINTLER_URL"]),
        "KINTLER_ALLOW_LOCAL_HTTP": str(values.get("KINTLER_ALLOW_LOCAL_HTTP") or "0"),
    }
    if args.action == "add-agent":
        node = identifier(args.node_id)
        if any(item.get("node_id") == node for item in items):
            raise ValueError("NodeID is already enrolled")
        tools = selected_tools(args.tools)
        item: dict[str, Any] = {"id": principal, "role": "agent", "secret": secret, "node_id": node}
        local.update(
            {
                "KINTLER_AGENT_ID": principal,
                "KINTLER_AGENT_SECRET": secret,
                "KINTLER_NODE_NAME": args.name or principal,
                "KINTLER_AGENT_TOOLS": ",".join(tools),
                "KINTLER_WORKSPACE": "workspace",
            }
        )
    else:
        nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]
        if not nodes:
            raise ValueError("At least one allowed NodeID is required")
        for node in nodes:
            if node != "*":
                identifier(node)
        item = {
            "id": principal,
            "role": "controller",
            "secret": secret,
            "nodes": nodes,
            "tools": selected_tools(args.tools),
            "can_approve": getattr(args, "can_approve", False),
        }
        local.update({"KINTLER_CONTROLLER_ID": principal, "KINTLER_CONTROLLER_SECRET": secret})
    write_env(output, local, create_only=True)
    items.append(item)
    try:
        write_env(path, {"KINTLER_CREDENTIALS": json.dumps(items, separators=(",", ":"))})
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f"Enrolled {principal}. Private local configuration: {output}")
    print(
        "Transfer .env securely to its owner; never commit it. Restart relay after credential changes."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline enrollment; secrets are written only to ignored .env files"
    )
    parser.add_argument("--env", default=".env", help="Server .env")
    sub = parser.add_subparsers(dest="action", required=True)
    initialize = sub.add_parser("init-server")
    initialize.add_argument("--url", required=True)
    initialize.add_argument("--local-http", action="store_true")
    device = sub.add_parser("add-agent")
    device.add_argument("--id", required=True)
    device.add_argument("--node-id", required=True)
    device.add_argument("--name")
    device.add_argument("--output-dir", required=True)
    device.add_argument("--tools", default=",".join(sorted(SAFE_TOOLS)))
    operator = sub.add_parser("add-controller")
    operator.add_argument("--id", required=True)
    operator.add_argument("--nodes", required=True)
    operator.add_argument("--tools", default="all")
    operator.add_argument("--output-dir", required=True)
    operator.add_argument("--can-approve", action="store_true")
    approval = sub.add_parser("grant-approval")
    approval.add_argument("--id", required=True)
    remove = sub.add_parser("remove")
    remove.add_argument("--id", required=True)
    args = parser.parse_args()
    try:
        configure(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Configuration error: {exc}\n")


if __name__ == "__main__":
    main()
