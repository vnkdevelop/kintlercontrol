import os
import tomllib
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if os.environ.get("RELEASE_TAG") != "v" + version:
        raise ValueError(
            "Release tag must match the package version; refusing to overwrite another version"
        )
    print("Release tag matches package version.")


if __name__ == "__main__":
    main()
