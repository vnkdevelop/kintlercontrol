import ipaddress
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from common.protocol import client_tls
from deploy.bootstrap_backend import initialize
from desktop.agent_bundle import personalize_agent
from desktop.settings import activate_profile, owner_profile_for_origin
from scripts.prepare_owner import prepare


def resources() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base if (base / "docker-compose.yml").is_file() else Path(__file__).parent / "resources"


def _create_deployment(
    url: str,
    output: Path,
    binaries: Path,
    owner_profile: Path | None = None,
    mode: str = "automatic",
) -> dict:
    url = url.strip().rstrip("/")
    client_tls(url)
    parsed = urlsplit(url)
    if any(character.isspace() or character in "'\\" for character in url):
        raise ValueError("Relay URL must not contain whitespace, quotes or backslashes")
    is_ip = False
    try:
        ipaddress.ip_address(parsed.hostname or "")
        is_ip = True
    except ValueError:
        pass
    if mode not in {"automatic", "existing"}:
        raise ValueError("Select automatic HTTPS or an existing HTTPS proxy")
    if mode == "automatic" and (
        parsed.port not in {None, 443}
        or is_ip
        or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", parsed.hostname or "")
        or "." not in (parsed.hostname or "")
    ):
        raise ValueError("Automatic HTTPS needs a real DNS hostname and HTTPS port 443")
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            "Choose a new or empty output directory; existing deployments are never overwritten"
        )
    agent = binaries / "KintlerAgent.exe"
    if not agent.is_file():
        raise ValueError(
            "KintlerAgent.exe must be next to KintlerSetup.exe (or build the Windows release first)"
        )
    output.mkdir(parents=True, exist_ok=True)
    server = output / "server"
    server.mkdir()
    local = prepare(
        url, server / "owner-public.json", owner_profile or owner_profile_for_origin(url)
    )
    initialize(server, server / "owner-public.json")
    root = resources()
    for name in ("docker-compose.yml", "docker-compose.tls.yml"):
        shutil.copyfile(root / name, server / name)
    (server / "deploy").mkdir()
    for name in ("nginx.conf", "existing-nginx.conf"):
        shutil.copyfile(root / "deploy" / name, server / "deploy" / name)
    if mode == "automatic":
        shutil.copyfile(root / "docker-compose.caddy.yml", server / "docker-compose.caddy.yml")
        shutil.copyfile(root / "deploy/Caddyfile", server / "Caddyfile")
        with (server / ".env").open("a", encoding="utf-8") as handle:
            handle.write(f"KINTLER_DOMAIN='{parsed.hostname}'\n")
        start = "docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d"
    else:
        start = "docker compose up -d"
    portable_image = binaries / "kintlercontrol-image.tar"
    if portable_image.is_file():
        shutil.copyfile(portable_image, server / "kintlercontrol-image.tar")
        from dotenv import set_key

        set_key(str(server / ".env"), "KINTLER_IMAGE", "kintlercontrol:1.3.1")
        start = "docker load --input kintlercontrol-image.tar && " + start
    instructions = f"""# Your KintlerControl server\n\nRelay: {url}\n\n1. Point your DNS hostname to your server. Install Docker Engine + Compose.\n2. Extract this folder on the server (not inside a public web directory).\n3. Run: `{start}`\n4. Open your controller and start the generated Agent folder on the target PC.\n5. Approve each new connection. For an unknown key, enter the code shown by the agent.\n\nAutomatic mode requires TCP 80/443. Existing-proxy mode requires your own TLS reverse proxy.\nDo not delete Docker volumes when updating. Private client keys are NOT in this server kit.\n\n# Запуск сервера\n\nНастройте DNS, установите Docker с Compose, распакуйте папку на сервере и выполните указанную команду.\nВ автоматическом режиме HTTPS выпускается Caddy; нужны свободные TCP 80/443 и доступный домен.\nВ режиме существующего прокси настройте свой HTTPS reverse proxy.\nПриватные ключи клиентов остаются на их ПК. Каждое новое соединение требует подтверждения.\n"""
    (server / "START_HERE.md").write_text(instructions, encoding="utf-8")
    target = output / "Agent"
    target.mkdir()
    personalize_agent(agent, target / agent.name, url)
    (target / "START_HERE.txt").write_text(
        "Copy only KintlerAgent.exe. Its public endpoint is included; no JSON or installation is required. Approve each connection in your controller.\nПереносите только Agent.exe; JSON и установка не нужны. Подтверждайте каждое соединение в контроллере.\n",
        encoding="utf-8",
    )
    archive = output / "server-kit.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for file in server.rglob("*"):
            if file.is_file():
                package.write(file, file.relative_to(server))
    return {
        "url": url,
        "owner_profile": str(local),
        "server_kit": str(archive),
        "agent_folder": str(target),
        "start_command": start,
    }


def create_deployment(
    url: str,
    output: Path,
    binaries: Path,
    owner_profile: Path | None = None,
    mode: str = "automatic",
) -> dict:
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(
            "Choose a new or empty output directory; existing deployments are never overwritten"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".kintler-setup-", dir=output.parent) as temporary:
        staging = Path(temporary) / "deployment"
        result = _create_deployment(url, staging, binaries, owner_profile, mode)
        if output.exists():
            output.rmdir()
        staging.rename(output)
    result["server_kit"] = str(output / "server-kit.zip")
    result["agent_folder"] = str(output / "Agent")
    if owner_profile is None:
        activate_profile(Path(result["owner_profile"]))
    return result
