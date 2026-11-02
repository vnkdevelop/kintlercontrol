from __future__ import annotations

import argparse
import errno
import hashlib
import json
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any


def run(arguments: list[str], timeout: int = 180) -> str:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Docker operation timed out; inspect this deployment before retrying"
        ) from None
    if result.returncode:
        raise RuntimeError(
            "Docker operation failed; check Docker access and this deployment's Compose logs"
        )
    return result.stdout.strip()


def portable_image(directory: Path, image: str) -> dict[str, Any] | None:
    archive = directory / "kintlercontrol-image.tar"
    if not archive.is_file():
        return None
    checksum = directory / "kintlercontrol-image.tar.sha256"
    if not checksum.is_file():
        raise RuntimeError("Image checksum is missing; use an intact generated server kit")
    expected = checksum.read_text(encoding="utf-8").split()
    if (
        len(expected) != 2
        or not re.fullmatch(r"[0-9a-f]{64}", expected[0])
        or expected[1] != archive.name
    ):
        raise RuntimeError("Image checksum file is invalid")
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected[0]:
        raise RuntimeError("Image checksum mismatch; container startup was refused")
    with tarfile.open(archive) as package:
        manifest_file = package.extractfile("manifest.json")
        if manifest_file is None:
            raise RuntimeError("Docker image manifest is missing")
        manifest = json.load(manifest_file)
        if len(manifest) != 1 or image not in manifest[0].get("RepoTags", []):
            raise RuntimeError("The bundled image does not match the configured image tag")
        config_file = package.extractfile(manifest[0]["Config"])
        if config_file is None:
            raise RuntimeError("Docker image configuration is missing")
        config_data = config_file.read()
        config = json.loads(config_data)
        architecture = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }.get(platform.machine().lower())
        if config.get("os") != "linux" or config.get("architecture") != architecture:
            raise RuntimeError("The bundled image architecture does not match this Linux server")
    if not isinstance(config.get("config"), dict) or not isinstance(
        config.get("rootfs", {}).get("diff_ids"), list
    ):
        raise RuntimeError("Docker image configuration or layer identity is invalid")
    return config


def without_null_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_null_fields(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_null_fields(item) for item in value]
    return value


def start(directory: Path, check: bool = False, timeout: int = 120) -> dict[str, Any]:
    if platform.system() != "Linux":
        raise RuntimeError("Run this script on the target Linux server, not on the Controller PC")
    directory = directory.resolve()
    if not (directory / ".env").is_file() or not (directory / "docker-compose.yml").is_file():
        raise RuntimeError(
            "Extract the generated server-kit.zip first; .env and docker-compose.yml are required"
        )
    if shutil.which("docker") is None:
        raise RuntimeError(
            "Install Docker Engine and its Docker Compose plugin, then rerun this script"
        )
    run(["docker", "info", "--format", "{{.ServerVersion}}"], 30)
    run(["docker", "compose", "version"], 30)
    automatic = (directory / "Caddyfile").is_file() and (
        directory / "docker-compose.caddy.yml"
    ).is_file()
    compose = [
        "docker",
        "compose",
        "--project-directory",
        str(directory),
        "-f",
        str(directory / "docker-compose.yml"),
    ]
    if automatic:
        compose.extend(["-f", str(directory / "docker-compose.caddy.yml")])
    configuration = json.loads(run([*compose, "config", "--format", "json"], 30))
    relay = configuration.get("services", {}).get("relay", {})
    credentials = json.loads(relay.get("environment", {}).get("KINTLER_CREDENTIALS", "[]"))
    if not isinstance(credentials, list) or not any(
        isinstance(value, dict) and value.get("role") == "controller" for value in credentials
    ):
        raise RuntimeError(
            "This is not a configured owner deployment; create it with Controller /setup"
        )
    image = relay.get("image")
    if not isinstance(image, str) or not image:
        raise RuntimeError("The relay image is missing from the Compose configuration")
    existing = run([*compose, "ps", "-aq"], 30).splitlines()
    own_caddy = False
    if existing:
        for container in json.loads(run(["docker", "inspect", *existing], 30)):
            labels = container.get("Config", {}).get("Labels", {})
            if labels.get("com.docker.compose.project.working_dir") != str(directory):
                raise RuntimeError(
                    "The Compose project belongs to another directory; refusing to replace it"
                )
            if labels.get("com.docker.compose.service") == "caddy" and container.get(
                "State", {}
            ).get("Running"):
                own_caddy = True
    if automatic and not own_caddy:
        for port in (80, 443):
            with socket.socket() as probe:
                try:
                    probe.bind(("0.0.0.0", port))
                except PermissionError:
                    pass
                except OSError as error:
                    if error.errno == errno.EADDRINUSE:
                        raise RuntimeError(
                            "TCP 80/443 are occupied; use the existing-proxy setup mode without stopping other services"
                        ) from None
                    raise
    expected_image = portable_image(directory, image)
    if check:
        return {
            "checked": True,
            "docker_ready": True,
            "compose_ready": True,
            "portable_image": expected_image is not None,
            "automatic_https": automatic,
            "existing_deployment": bool(existing),
        }
    if expected_image:
        run(["docker", "load", "--input", str(directory / "kintlercontrol-image.tar")])
        loaded = json.loads(run(["docker", "image", "inspect", image], 30))
        if len(loaded) != 1 or any(
            (
                loaded[0].get("Os") != expected_image["os"],
                loaded[0].get("Architecture") != expected_image["architecture"],
                without_null_fields(loaded[0].get("Config"))
                != without_null_fields(expected_image["config"]),
                loaded[0].get("RootFS", {}).get("Layers") != expected_image["rootfs"]["diff_ids"],
            )
        ):
            raise RuntimeError(
                "The loaded Docker image identity differs; container startup was refused"
            )
    else:
        run(["docker", "pull", image])
    run([*compose, "up", "-d", "--no-build", "--pull", "missing"])
    container_id = run([*compose, "ps", "-q", "relay"], 30)
    if not container_id or "\n" in container_id:
        raise RuntimeError("Expected exactly one relay container")
    deadline = time.monotonic() + timeout
    while True:
        containers = json.loads(run(["docker", "inspect", container_id], 30))
        state = containers[0].get("State", {})
        if state.get("Health", {}).get("Status") == "healthy":
            return {
                "started": True,
                "healthy": True,
                "portable_image": expected_image is not None,
                "automatic_https": automatic,
                "restart_count": containers[0].get("RestartCount", 0),
                "data_volumes_preserved": True,
            }
        if state.get("Status") in {"exited", "dead"} or time.monotonic() >= deadline:
            raise RuntimeError(
                "The relay did not become healthy; inspect this deployment's Compose logs. Data volumes were not deleted"
            )
        time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start only your extracted KintlerControl server kit; never installs Docker or deletes data volumes"
    )
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not 10 <= args.timeout <= 300:
        parser.error("--timeout must be between 10 and 300 seconds")
    try:
        print(json.dumps(start(args.directory, args.check, args.timeout), indent=2))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print("Startup refused: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
