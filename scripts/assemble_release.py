import argparse
import hashlib
import zipfile
from pathlib import Path


def assemble(directory: Path) -> None:
    windows = directory / "windows"
    clients = list(windows.rglob("*.exe"))
    expected = {"KintlerAgent.exe", "KintlerController.exe", "KintlerMCP.exe", "KintlerSetup.exe"}
    if {path.name for path in clients} != expected:
        raise ValueError("Release must contain exactly the four generic client executables")
    image = directory / "backend/kintlercontrol-image.tar"
    if not image.is_file():
        raise ValueError("Tested portable backend image is missing")
    root = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(
        directory / "KintlerControl-Windows.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for file in clients:
            archive.write(file, file.name)
        archive.write(image, "kintlercontrol-image.tar")
        archive.write(root / "docs/WINDOWS_EXE.md", "START_HERE.md")
    with zipfile.ZipFile(
        directory / "KintlerControl-Backend.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.write(image, image.name)
        for name in (
            "docker-compose.yml",
            "docker-compose.caddy.yml",
            "docker-compose.tls.yml",
            ".env.example",
        ):
            archive.write(root / name, name)
        archive.write(root / "deploy/Caddyfile", "Caddyfile")
        archive.write(root / "deploy/bootstrap_backend.py", "bootstrap_backend.py")
        archive.write(root / "docs/DEPLOYMENT.md", "DEPLOYMENT.md")
    lines = []
    for file in sorted(directory.glob("*.zip")):
        digest = hashlib.sha256()
        with file.open("rb") as handle:
            while data := handle.read(1024 * 1024):
                digest.update(data)
        lines.append(f"{digest.hexdigest()}  {file.name}")
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    print(
        "Generic release assembled. No owner registration, deployment address or private profiles included."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    assemble(args.directory)


if __name__ == "__main__":
    main()
