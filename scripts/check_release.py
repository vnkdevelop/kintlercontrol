import argparse
import json
import tarfile
import zipfile
from pathlib import Path


def check(directory: Path) -> None:
    allowed_windows = {
        "KintlerAgent.exe",
        "KintlerController.exe",
        "KintlerMCP.exe",
        "KintlerSetup.exe",
        "kintlercontrol-image.tar",
        "START_HERE.md",
    }
    allowed_backend = {
        "kintlercontrol-image.tar",
        "docker-compose.yml",
        "docker-compose.caddy.yml",
        "docker-compose.tls.yml",
        ".env.example",
        "Caddyfile",
        "bootstrap_backend.py",
        "DEPLOYMENT.md",
    }
    for filename, allowed in (
        ("KintlerControl-Windows.zip", allowed_windows),
        ("KintlerControl-Backend.zip", allowed_backend),
    ):
        with zipfile.ZipFile(directory / filename) as archive:
            if set(archive.namelist()) != allowed or archive.testzip() is not None:
                raise ValueError("Public release includes unexpected files or is corrupt")
    image = directory / "backend/kintlercontrol-image.tar"
    with tarfile.open(image) as archive:
        stream = archive.extractfile("manifest.json")
        if stream is None:
            raise ValueError("Docker manifest missing")
        manifest = json.load(stream)
        for record in manifest:
            config_stream = archive.extractfile(record["Config"])
            if config_stream is None:
                raise ValueError("Docker image config missing")
            configuration = json.load(config_stream).get("config", {})
            environment = configuration.get("Env", [])
            if any(
                value.split("=", 1)[0]
                in {
                    "KINTLER_CREDENTIALS",
                    "KINTLER_URL",
                    "KINTLER_CONTROLLER_PRIVATE_KEY",
                    "KINTLER_AGENT_PRIVATE_KEY",
                }
                for value in environment
            ):
                raise ValueError("Deployment-specific configuration was baked into the image")
            for layer in record["Layers"]:
                layer_stream = archive.extractfile(layer)
                if layer_stream is None:
                    raise ValueError("Docker layer missing")
                with tarfile.open(fileobj=layer_stream, mode="r|*") as filesystem:
                    for item in filesystem:
                        if Path(item.name).name in {
                            ".env",
                            "owner-public.json",
                            "kintler-agent.json",
                            "privkey.pem",
                            "id_rsa",
                            "id_ed25519",
                            "kintler.sqlite3",
                        }:
                            raise ValueError(
                                "Private deployment data was baked into an image layer"
                            )
    print(
        "Public release verified: exact generic file set; no owner registration, profiles or deployment data in image layers."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    check(args.directory)


if __name__ == "__main__":
    main()
