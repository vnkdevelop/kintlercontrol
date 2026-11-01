import argparse
import json
import sys
from pathlib import Path

HOSTS = (
    "generic",
    "opencode",
    "codex",
    "claude",
    "cursor",
    "antigravity",
    "gemini",
    "copilot",
    "vscode",
)


def connection(host: str, executable: Path, profile: Path | None = None) -> dict | str:
    if host not in HOSTS:
        raise ValueError("Unsupported MCP host")
    args = ["--mcp"] + (["--env", str(profile.resolve())] if profile else [])
    command = str(executable.resolve())
    if host == "opencode":
        return {"type": "local", "command": [command, *args]}
    if host == "codex":
        return (
            "[mcp_servers.kintler]\ncommand = "
            + json.dumps(command)
            + "\nargs = "
            + json.dumps(args)
            + "\nstartup_timeout_sec = 45\ntool_timeout_sec = 180\n"
        )
    result = {"command": command, "args": args}
    if host in {"cursor", "vscode"}:
        result["type"] = "stdio"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print only the local Kintler stdio entry for the selected AI host; never writes host config or credentials"
    )
    parser.add_argument("--host", choices=HOSTS, default="generic")
    parser.add_argument("--env", type=Path)
    args = parser.parse_args()
    if not getattr(sys, "frozen", False):
        parser.error("Use the packaged KintlerController.exe --mcp-config mode")
    result = connection(args.host, Path(sys.executable), args.env)
    print(result if isinstance(result, str) else json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
