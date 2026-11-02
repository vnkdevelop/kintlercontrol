import argparse
import asyncio
import getpass
import json
import os
import re
import shlex
from pathlib import Path

import aiohttp
from dotenv import load_dotenv, set_key

from client.kintler_sdk import ClientSettings, KintlerClient, LiveController
from common.protocol import ProtocolError, identifier


def printable(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)


def inventory(nodes: list[dict]) -> None:
    print(f"{'NODE ID':23} {'NAME':22} {'STATUS':8} {'PRIVILEGES':10} PLATFORM")
    for node in nodes:
        print(
            printable(
                f"{node['id']:23} {node['name'][:22]:22} {'Online' if node['online'] else 'Offline':8} {'Admin' if node['admin'] else 'User':10} {node['platform']}"
            )
        )


async def login(args: argparse.Namespace) -> None:
    url = args.url or (await asyncio.to_thread(input, "Relay URL (https://host:8443): ")).strip()
    principal = identifier(
        args.principal or (await asyncio.to_thread(input, "Operator ID: ")).strip()
    )
    secret = await asyncio.to_thread(getpass.getpass, "Operator key: ")
    settings = ClientSettings(url, principal, secret, args.local_http, args.ca_file)
    async with KintlerClient(settings) as client:
        capabilities = await client.tools()
        nodes = await client.nodes()
    path = Path(args.env).resolve()
    if path.name != ".env":
        raise ValueError("Credentials must be saved in a file named .env")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    for name, value in {
        "KINTLER_URL": url.rstrip("/"),
        "KINTLER_CONTROLLER_ID": principal,
        "KINTLER_CONTROLLER_SECRET": secret,
        "KINTLER_ALLOW_LOCAL_HTTP": "1" if args.local_http else "0",
        "KINTLER_CA_FILE": args.ca_file or "",
    }.items():
        set_key(str(path), name, value)
    if os.name != "nt":
        path.chmod(0o600)
    print(f"Signed in as {principal}. Tools: {', '.join(capabilities['tools'])}")
    print(f"Local configuration: {path}")
    inventory(nodes)


async def interactive(settings: ClientSettings) -> None:
    target: dict = {}
    running: set[str] = set()
    completed_early: set[str] = set()
    shell = "cmd"

    async def event(value: dict) -> None:
        command_id = value["command_id"]
        if value["kind"] == "result":
            if command_id in running:
                running.discard(command_id)
            else:
                completed_early.add(command_id)
            print("\n" + printable(json.dumps(value["result"], ensure_ascii=False)))
        else:
            print(printable(value["data"]), end="", flush=True)

    async with LiveController(settings, event) as live:
        inventory((await live.request("nodes"))["nodes"])
        print(
            "!nodes | !use <id/name> | !shell cmd/powershell/bash | !tool <name> <JSON> | !cancel | !upload <local> <remote> | !download <remote> <local> | !exit"
        )
        while True:
            label = target.get("name", "no-node")
            privilege = "Admin" if target.get("admin") else "User"
            try:
                line = await asyncio.to_thread(
                    input, f"kintler [{label} ({privilege})] ({shell})> "
                )
            except EOFError:
                return
            if not line.strip():
                continue
            try:
                if line == "!exit":
                    return
                if line == "!nodes":
                    inventory((await live.request("nodes"))["nodes"])
                elif line.startswith("!use "):
                    reply = await live.request("use", node=line[5:].strip())
                    target = reply["node"]
                    running.clear()
                    completed_early.clear()
                    shell = "cmd" if target["platform"].startswith("Windows") else "bash"
                elif line.startswith("!shell "):
                    value = line[7:].strip()
                    if value not in {"cmd", "powershell", "bash"}:
                        raise ProtocolError("Choose cmd, powershell or bash")
                    shell = value
                elif line == "!cancel":
                    for command in list(running):
                        await live.request("cancel", command_id=command)
                elif line.startswith(("!upload ", "!download ")):
                    if not target:
                        raise ProtocolError("Select a node first")
                    parts = shlex.split(line, posix=True)
                    if len(parts) != 3:
                        raise ProtocolError("Use quoted paths with forward slashes")
                    async with KintlerClient(settings) as client:
                        result = await (
                            client.upload(target["id"], parts[1], parts[2])
                            if parts[0] == "!upload"
                            else client.download(target["id"], parts[1], parts[2])
                        )
                        print(json.dumps(result, ensure_ascii=False))
                else:
                    if line.startswith("!tool "):
                        parts = line[6:].split(" ", 1)
                        tool, args = parts[0], json.loads(parts[1]) if len(parts) > 1 else {}
                    elif line.startswith("!"):
                        raise ProtocolError("Unknown console command")
                    else:
                        tool, args = "shell", {"shell": shell, "command": line}
                    reply = await live.request("command", tool=tool, args=args)
                    if reply["command_id"] in completed_early:
                        completed_early.discard(reply["command_id"])
                    else:
                        running.add(reply["command_id"])
            except (ProtocolError, ValueError, aiohttp.ClientError) as exc:
                print(f"Error: {printable(str(exc))}")


async def run(args: argparse.Namespace) -> None:
    if args.action == "login":
        await login(args)
        return
    load_dotenv(args.env)
    settings = ClientSettings.from_env()
    if args.action in {None, "console"}:
        await interactive(settings)
        return
    async with KintlerClient(settings) as client:
        if args.action == "nodes":
            inventory(await client.nodes())
        elif args.action == "tools":
            print(json.dumps(await client.tools(), ensure_ascii=False, indent=2))
        elif args.action == "call":
            print(
                json.dumps(
                    await client.call(args.node, args.tool, json.loads(args.args), args.timeout),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.action == "open":
            print(
                json.dumps(
                    await client.call(
                        args.node, "open", {"app": args.app, "arguments": args.arguments}
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.action == "upload":
            print(
                json.dumps(
                    await client.upload(args.node, args.source, args.destination, args.overwrite)
                )
            )
        elif args.action == "download":
            print(
                json.dumps(
                    await client.download(args.node, args.source, args.destination, args.overwrite)
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kintler operator console and automation CLI")
    parser.add_argument("--env", default=".env")
    commands = parser.add_subparsers(dest="action")
    connect = commands.add_parser("login", help="Validate operator credentials and save local .env")
    connect.add_argument("--url")
    connect.add_argument("--principal")
    connect.add_argument("--local-http", action="store_true")
    connect.add_argument("--ca-file")
    for name in ("console", "nodes", "tools"):
        commands.add_parser(name)
    call = commands.add_parser("call")
    call.add_argument("node")
    call.add_argument("tool")
    call.add_argument("--args", default="{}")
    call.add_argument("--timeout", type=float, default=120)
    launch = commands.add_parser(
        "open", help="Open an application on an explicitly selected device"
    )
    launch.add_argument("node")
    launch.add_argument("app")
    launch.add_argument("arguments", nargs=argparse.REMAINDER)
    for name in ("upload", "download"):
        transfer = commands.add_parser(name)
        transfer.add_argument("node")
        transfer.add_argument("source")
        transfer.add_argument("destination")
        transfer.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (ProtocolError, ValueError, KeyError, aiohttp.ClientError, OSError) as exc:
        parser.exit(1, f"Kintler: {printable(str(exc))}\n")


if __name__ == "__main__":
    main()
