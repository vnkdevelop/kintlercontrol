import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path

import aiohttp

from client.diagnostics import connection_snapshot
from client.kintler_sdk import KintlerClient
from common.protocol import ProtocolError
from desktop.console import terminal_console
from desktop.deployment import create_deployment
from desktop.settings import activate_profile, controller_settings, load_profile, profile
from desktop.shortcut import create_shortcut


async def check(path: Path, node: str, result: Path) -> None:
    async with KintlerClient(controller_settings(load_profile(path, "controller"))) as client:
        info = await client.call(node, "sysinfo")
        command = await client.call(
            node, "shell", {"shell": "cmd", "command": "echo FROZEN_KINTLER_OK"}
        )
        result.write_text(
            json.dumps(
                {
                    "ok": bool(
                        info["ok"] and command["ok"] and "FROZEN_KINTLER_OK" in command["stdout"]
                    ),
                    "tools": (await client.tools())["tools"],
                }
            ),
            encoding="utf-8",
        )


async def self_test(path: Path, role: str = "controller") -> None:
    from desktop.terminal_controller import ProcessPicker, TerminalController

    app = TerminalController(autoconnect=False)
    async with app.run_test(size=(110, 36)) as pilot:
        await app.push_screen(
            ProcessPicker(
                [{"pid": 100, "name": "kintler-self-test.exe", "created": 123.25}],
                "self-test",
            )
        )
        await pilot.pause()
        assert isinstance(app.screen, ProcessPicker)
        await pilot.press("escape")
        assert not app.screen.is_modal
    path.write_text(json.dumps({"ok": True, "role": role, "terminal": True}), encoding="utf-8")


async def probe(path: Path, node: str = "") -> dict:
    try:
        async with asyncio.timeout(15):
            async with KintlerClient(
                controller_settings(load_profile(path, "controller"))
            ) as client:
                return await connection_snapshot(client, node)
    except (ValueError, OSError, aiohttp.ClientError, ProtocolError, TimeoutError) as error:
        if isinstance(error, (ValueError, OSError)):
            reason = "profile_or_connection_unavailable"
        else:
            reason = "connection_failed"
        return {
            "ok": False,
            "connected": False,
            "available": 0,
            "target": None,
            "selection": reason,
        }


def json_stdout() -> None:
    stream = sys.stdout
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    with contextlib.suppress(ValueError, OSError):
        reconfigure(encoding="utf-8", errors="strict")


def setup_main() -> None:
    parser = argparse.ArgumentParser(description="Create your own deployment and configured Agent")
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--binaries", type=Path)
    parser.add_argument("--owner-profile", type=Path)
    parser.add_argument("--existing-proxy", action="store_true")
    parser.add_argument("--self-test", type=Path)
    args = parser.parse_args()
    if args.self_test:
        asyncio.run(self_test(args.self_test, "setup"))
        return
    if args.url:
        if not args.binaries:
            args.binaries = Path(
                getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1] / "dist/windows")
            )
            if getattr(sys, "frozen", False):
                args.binaries /= "embedded"
        if not args.output:
            parser.error("--url requires --output")
        result = create_deployment(
            args.url,
            args.output,
            args.binaries,
            args.owner_profile,
            "existing" if args.existing_proxy else "automatic",
        )
        if sys.stdout is not None:
            print(json.dumps(result, indent=2))
        return
    with terminal_console("Kintler · Setup"):
        from desktop.terminal_controller import TerminalController

        app = TerminalController(autoconnect=False)
        app.run()


def main() -> None:
    modes = {
        "--probe",
        "--mcp",
        "--mcp-config",
        "--install-skill",
        "--setup",
        "--deploy",
        "--installation-info",
    }.intersection(sys.argv[1:])
    if len(modes) > 1:
        print("Choose exactly one Controller mode", file=sys.stderr)
        raise SystemExit(2)
    if "--install-skill" in sys.argv or "--mcp-config" in sys.argv:
        mode = "--install-skill" if "--install-skill" in sys.argv else "--mcp-config"
        sys.argv.remove(mode)
        from desktop.stdio import restored_stdio

        if mode == "--install-skill":
            from scripts.install_skill import main as integration_main
        else:
            from scripts.mcp_config import main as integration_main
        with restored_stdio():
            integration_main()
        return
    if "--mcp" in sys.argv:
        sys.argv.remove("--mcp")
        from desktop.mcp_entry import main as mcp_main
        from desktop.stdio import restored_stdio

        with restored_stdio():
            mcp_main()
        return
    if "--deploy" in sys.argv:
        sys.argv.remove("--deploy")
        from desktop.stdio import restored_stdio
        from scripts.deploy_remote import main as deploy_main

        with restored_stdio():
            deploy_main()
        return
    if "--setup" in sys.argv:
        sys.argv.remove("--setup")
        setup_main()
        return
    parser = argparse.ArgumentParser(description="Kintler command-first terminal controller")
    parser.add_argument("--env", type=Path)
    parser.add_argument(
        "--installation-info",
        action="store_true",
        help="Print the current local installation/file map; no network or device commands",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Read-only connection/device probe; prints JSON, never runs device commands",
    )
    parser.add_argument(
        "--mcp", action="store_true", help="Serve local stdio MCP; use this mode from your AI host"
    )
    parser.add_argument(
        "--mcp-config",
        action="store_true",
        help="Print host-specific stdio settings; see --mcp-config --help",
    )
    parser.add_argument(
        "--install-skill",
        action="store_true",
        help="Install generic skill files; see --install-skill --help",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Create a new owner/server kit; never use to probe an existing server",
    )
    parser.add_argument(
        "--deploy", action="store_true", help="Explicit server deployment mode; see --deploy --help"
    )
    parser.add_argument("--self-test", type=Path)
    parser.add_argument(
        "--check",
        type=Path,
        help="Development smoke test: executes remote sysinfo and a shell echo; use --probe for discovery",
    )
    parser.add_argument("--node")
    parser.add_argument("--create-shortcut", action="store_true")
    parser.add_argument("--shortcut-destination", type=Path)
    parser.add_argument("--activate-profile", type=Path)
    parser.add_argument("--window-title", default="Kintler · Controller")
    args = parser.parse_args()
    if args.installation_info:
        from desktop.installation import installation_information
        from desktop.stdio import restored_stdio

        with restored_stdio():
            json_stdout()
            print(json.dumps(installation_information(), ensure_ascii=False, indent=2))
        return
    if args.probe:
        from desktop.stdio import restored_stdio

        with restored_stdio():
            json_stdout()
            result = asyncio.run(probe(args.env or profile("controller"), args.node or ""))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["ok"] else 1)
    if args.activate_profile:
        activate_profile(args.activate_profile)
    elif args.create_shortcut:
        create_shortcut(Path(sys.executable), args.shortcut_destination)
    elif args.check:
        if not args.node:
            parser.error("--check requires --node")
        asyncio.run(check(args.env or profile("controller"), args.node, args.check))
    elif args.self_test:
        asyncio.run(self_test(args.self_test))
    else:
        from desktop.terminal_controller import TerminalController

        if getattr(sys, "frozen", False):
            from desktop.installation import register_installation

            with contextlib.suppress(OSError, ValueError):
                register_installation(Path(sys.executable))
        with terminal_console(args.window_title):
            TerminalController(args.env).run()


if __name__ == "__main__":
    main()
