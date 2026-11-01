import hashlib
import json
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    names = [
        "server",
        "agent",
        "agent-rust",
        "client",
        "common",
        "scripts",
        "desktop",
        "deploy",
        "docs",
        "tests",
        ".github",
    ]
    files = [
        root / name
        for name in [
            "README.md",
            "README.ru.md",
            "LICENSE",
            "SECURITY.md",
            "pyproject.toml",
            "uv.lock",
            "requirements.lock",
            "requirements-test.lock",
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.tls.yml",
            "docker-compose.caddy.yml",
            "docker-compose.dev.yml",
            ".gitignore",
            ".gitattributes",
            ".dockerignore",
            ".graphifyignore",
            ".env.example",
        ]
    ]
    for name in names:
        files.extend(
            p
            for p in (root / name).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and "target" not in p.parts
            and not p.is_symlink()
        )
    files = sorted(set(files))
    for path in files:
        if path.name != ".env.example" and (
            path.name.startswith(".env") or path.suffix in {".key", ".pem", ".pyc"}
        ):
            raise RuntimeError("Secret/temporary file found in release input")
    destination = root / "dist" / "kintlercontrol-source-1.3.1.zip"
    destination.parent.mkdir(exist_ok=True)
    manifest = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "kintlercontrol/" + path.relative_to(root).as_posix())
        archive.writestr(
            "kintlercontrol/MANIFEST.sha256.json", json.dumps(manifest, indent=2, sort_keys=True)
        )
    print(
        f"Release: {destination}; {len(files)} files; SHA-256 {hashlib.sha256(destination.read_bytes()).hexdigest()}"
    )


if __name__ == "__main__":
    main()
