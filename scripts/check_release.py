import argparse
import hashlib
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path

from scripts.release_layout import BUNDLE_NAME, SKILL_ARCHIVE_NAME, SKILL_FILES, bundle_sources


def check(directory: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    files = bundle_sources(root, directory)
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    for filename, allowed in (
        (BUNDLE_NAME, set(files) | {"MANIFEST.json"}),
        (SKILL_ARCHIVE_NAME, {"kintler-remote/" + name for name in SKILL_FILES}),
    ):
        with zipfile.ZipFile(directory / filename) as archive:
            if (
                len(archive.namelist()) != len(allowed)
                or set(archive.namelist()) != allowed
                or archive.testzip() is not None
            ):
                raise ValueError("Public release includes unexpected files or is corrupt")
            if filename == BUNDLE_NAME:
                manifest = json.loads(archive.read("MANIFEST.json"))
                if manifest.get("version") != version or set(manifest.get("files", {})) != set(
                    files
                ):
                    raise ValueError("Bundle manifest version or payload differs")
                for name, record in manifest["files"].items():
                    with archive.open(name) as handle:
                        checksum = hashlib.sha256()
                        while data := handle.read(1024 * 1024):
                            checksum.update(data)
                        digest = checksum.hexdigest()
                    if (
                        digest != record["sha256"]
                        or archive.getinfo(name).file_size != record["size"]
                    ):
                        raise ValueError("Bundle payload checksum differs")
                if (
                    archive.read("clients/windows/KintlerController.exe")
                    != (directory / "KintlerController.exe").read_bytes()
                ):
                    raise ValueError("Controller bootstrap is not the same generic client")
                if archive.read("clients/windows/KintlerAgent.exe")[-8:] == b"KCAGENT1":
                    raise ValueError(
                        "A configured personal Agent was included in the public bundle"
                    )
                clients = json.loads(archive.read("clients/windows/SHA256.json"))
                if archive.read("server/kintlercontrol-image.tar.sha256").decode(
                    "utf-8"
                ).split() != [
                    manifest["files"]["server/kintlercontrol-image.tar"]["sha256"],
                    "kintlercontrol-image.tar",
                ]:
                    raise ValueError("Server image checksum does not match the bundle payload")
                if set(clients) != {"KintlerController.exe", "KintlerAgent.exe"} or any(
                    hashlib.sha256(archive.read("clients/windows/" + name)).hexdigest() != digest
                    for name, digest in clients.items()
                ):
                    raise ValueError("Client checksums do not match the generic bundle EXEs")
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
