import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from client.mcp_server import main as serve
from client.mcp_server import mcp
from desktop.settings import profile


def main() -> None:
    if "--self-test" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--self-test", type=Path, required=True)
        args = parser.parse_args()
        tools = asyncio.run(mcp.list_tools())
        args.self_test.write_text(
            json.dumps(
                {"ok": len(tools) == 12, "role": "mcp", "tools": [tool.name for tool in tools]}
            ),
            encoding="utf-8",
        )
        return
    if "--env" not in sys.argv:
        sys.argv.extend(["--env", str(profile("controller"))])
    original_in, original_out = sys.stdin, sys.stdout
    private_in = os.fdopen(os.dup(original_in.fileno()), "r", encoding="utf-8", errors="replace")
    try:
        private_out = os.fdopen(os.dup(original_out.fileno()), "w", encoding="utf-8")
    except BaseException:
        private_in.close()
        raise
    try:
        sys.stdin, sys.stdout = private_in, private_out
        serve()
    finally:
        sys.stdin, sys.stdout = original_in, original_out
        for stream in (private_in, private_out):
            with contextlib.suppress(ValueError, OSError):
                stream.close()


if __name__ == "__main__":
    main()
