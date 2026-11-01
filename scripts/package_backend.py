import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package the built image and public owner registration; never include private .env files"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.root
    output = root / "dist/backend"
    output.mkdir(parents=True, exist_ok=True)
    image = root / "dist/kintlercontrol-1.3.1-image.tar"
    if not image.is_file() or not (output / "owner-public.json").is_file():
        raise ValueError(
            "Build/save image 1.1.0 and prepare the local owner public registration first"
        )
    files = {
        "kintlercontrol-1.3.1-image.tar": image,
        "owner-public.json": output / "owner-public.json",
        "docker-compose.yml": root / "docker-compose.yml",
        "docker-compose.tls.yml": root / "docker-compose.tls.yml",
        "bootstrap_backend.py": root / "deploy/bootstrap_backend.py",
        "README_DEPLOY.md": root / "docs/DEPLOYMENT.md",
        "deploy/nginx.conf": root / "deploy/nginx.conf",
        "deploy/existing-nginx.conf": root / "deploy/existing-nginx.conf",
    }
    registration = json.loads((output / "owner-public.json").read_text())
    if set(registration) != {"id", "public_key", "url"}:
        raise ValueError("Owner registration must contain only public information")
    manifest = {}
    for name, source in files.items():
        if source.name == ".env" or source.suffix in {".pem", ".key"}:
            raise ValueError("Private file cannot be packaged")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        manifest[name] = digest.hexdigest()
    destination = root / "dist/kintlercontrol-backend-1.3.1.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in files.items():
            archive.write(source, name)
        archive.writestr("SHA256.json", json.dumps(manifest, indent=2))
    shutil.copyfile(root / "docs/DEPLOYMENT.md", root / "dist/DEPLOYMENT.md")
    print(f"Backend ready: {destination}. Only the owner's public key is included.")


if __name__ == "__main__":
    main()
