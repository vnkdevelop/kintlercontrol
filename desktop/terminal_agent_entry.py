import argparse
import asyncio
from pathlib import Path

from desktop.console import terminal_console


def main() -> None:
    from desktop.terminal_agent import TerminalAgent, self_test

    parser = argparse.ArgumentParser(description="Compatible Python agent terminal frontend")
    parser.add_argument("--env", type=Path)
    parser.add_argument("--self-test", type=Path)
    args = parser.parse_args()
    if args.self_test:
        asyncio.run(self_test(args.self_test))
    else:
        with terminal_console("Kintler · Agent"):
            TerminalAgent(args.env).run()


if __name__ == "__main__":
    main()
