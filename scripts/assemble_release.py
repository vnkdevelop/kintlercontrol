import argparse
import hashlib
import json
import shutil
import tomllib
import zipfile
from pathlib import Path

from scripts.release_layout import BUNDLE_NAME, SKILL_ARCHIVE_NAME, SKILL_FILES, bundle_sources


def assemble(directory: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    files = bundle_sources(root, directory)
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    manifest = {"schema": 1, "product": "KintlerControl", "version": version, "files": {}}
    for name, path in files.items():
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        manifest["files"][name] = {"sha256": digest, "size": path.stat().st_size}
    with zipfile.ZipFile(directory / BUNDLE_NAME, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
        archive.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(
        directory / SKILL_ARCHIVE_NAME, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for name in SKILL_FILES:
            archive.write(files["skills/kintler-remote/" + name], "kintler-remote/" + name)
    shutil.copyfile(
        files["clients/windows/KintlerController.exe"], directory / "KintlerController.exe"
    )
    lines = []
    for filename in (BUNDLE_NAME, SKILL_ARCHIVE_NAME, "KintlerController.exe"):
        file = directory / filename
        checksum = hashlib.sha256()
        with file.open("rb") as handle:
            while data := handle.read(1024 * 1024):
                checksum.update(data)
        lines.append(f"{checksum.hexdigest()}  {file.name}")
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    print(
        "Generic all-in-one bundle, portable skill and small Controller bootstrap assembled; no personal configuration included."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    assemble(args.directory)


if __name__ == "__main__":
    main()
