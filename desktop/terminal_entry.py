import argparse
import asyncio
import json
import sys
from pathlib import Path

from client.kintler_sdk import KintlerClient
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
    from desktop.terminal_controller import TerminalController

    app = TerminalController(autoconnect=False)
    async with app.run_test(size=(110, 36)) as pilot:
        await pilot.pause()
    path.write_text(json.dumps({"ok": True, "role": role, "terminal": True}), encoding="utf-8")


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
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--check", type=Path)
    parser.add_argument("--node")
    parser.add_argument("--create-shortcut", action="store_true")
    parser.add_argument("--shortcut-destination", type=Path)
    parser.add_argument("--activate-profile", type=Path)
    parser.add_argument("--window-title", default="Kintler · Controller")
    args = parser.parse_args()
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

        with terminal_console(args.window_title):
            TerminalController(args.env).run()


if __name__ == "__main__":
    main()
