import argparse
import asyncio
import datetime
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import tempfile
from pathlib import Path

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent.kintler_agent import Agent, AgentSettings
from client.kintler_sdk import ClientSettings, KintlerClient
from common.protocol import TOOLS


def docker(*args: str, check: bool = True) -> str:
    argv = ["docker", *args]
    command = (
        ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)] if os.name == "nt" else argv
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if check and result.returncode:
        raise RuntimeError("Docker smoke operation failed: " + result.stderr.strip())
    return result.stdout.strip()


def certificate(directory: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (directory / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


async def exercise(
    url: str,
    directory: Path,
    principal: str,
    secret: str,
    agent_secret: str,
    agent_principal: str = "smoke_agent",
    node: str = "node_smoke",
) -> None:
    import ssl

    ca = str(directory / "cert.pem")
    tls = ssl.create_default_context(cafile=ca)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    async with aiohttp.ClientSession() as readiness:
        for _ in range(100):
            try:
                async with readiness.get(url + "/healthz", ssl=tls) as response:
                    if response.status == 200:
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.2)
        else:
            raise TimeoutError("TLS container did not become ready")
    pairing_codes: dict[str, str] = {}

    def observe(event: str, detail: dict) -> None:
        if event == "waiting":
            pairing_codes[detail["request_id"]] = detail["code"]

    device = Agent(
        AgentSettings(
            url,
            agent_principal,
            agent_secret,
            node,
            "smoke-device",
            directory / "workspace",
            TOOLS,
            ca_file=ca,
        ),
        observer=observe,
    )
    async with aiohttp.ClientSession() as http:
        task = asyncio.create_task(device.connect(http))
        try:
            async with KintlerClient(ClientSettings(url, principal, secret, ca_file=ca)) as client:
                async with asyncio.timeout(20):
                    for _ in range(200):
                        for request in await client.approvals():
                            code = pairing_codes.get(request["request_id"])
                            if code:
                                await client.approve(request["request_id"], True, code)
                        if any(n["id"] == node and n["online"] for n in await client.nodes()):
                            break
                        if task.done():
                            task.result()
                        await asyncio.sleep(0.1)
                    else:
                        raise TimeoutError("Agent did not connect")
                info = await client.call(node, "sysinfo")
                assert info["ok"] and info["ram_total"] > 0, info.get(
                    "error", "Telemetry unavailable"
                )
                shell = "cmd" if os.name == "nt" else "bash"
                result = await client.call(
                    node, "shell", {"shell": shell, "command": "echo TLS_CONTAINER_OK"}
                )
                assert result["ok"] and "TLS_CONTAINER_OK" in result["stdout"]
                source = directory / "source.bin"
                source.write_bytes(secrets.token_bytes(140000))
                await client.upload(node, source, "file.bin")
                await client.download(node, "file.bin", directory / "download.bin")
                assert source.read_bytes() == (directory / "download.bin").read_bytes()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Isolated TLS container smoke test; removes only its own containers, network and temporary files"
    )
    parser.add_argument("--image", default="kintlercontrol:1.5.1")
    args = parser.parse_args()
    suffix = secrets.token_hex(4)
    network, relay, proxy = ("kintler-smoke-" + suffix + tag for tag in ("-net", "-relay", "-tls"))
    base = (
        Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "opencode"
        if os.name == "nt"
        else Path(tempfile.gettempdir())
    )
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kintler-smoke-", dir=base) as temporary:
        directory = Path(temporary)
        certificate(directory)
        operator_secret, agent_secret = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        credentials = [
            {
                "id": "smoke_operator",
                "secret": operator_secret,
                "role": "controller",
                "nodes": ["node_smoke"],
                "tools": sorted(TOOLS),
                "can_approve": True,
            },
            {"id": "smoke_agent", "secret": agent_secret, "role": "agent", "node_id": "node_smoke"},
        ]
        (directory / ".env").write_text(
            "KINTLER_CREDENTIALS="
            + json.dumps(credentials, separators=(",", ":"))
            + "\nKINTLER_TRUSTED_PROXIES=172.31.242.10/32\n",
            encoding="utf-8",
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        created: list[str] = []
        try:
            docker("network", "create", "--subnet", "172.31.242.0/24", network)
            created.append(network)
            docker(
                "run",
                "-d",
                "--name",
                relay,
                "--network",
                network,
                "--network-alias",
                "relay",
                "--ip",
                "172.31.242.20",
                "--env-file",
                str(directory / ".env"),
                "-v",
                "/app/data",
                "--read-only",
                "--tmpfs",
                "/tmp:size=16m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                args.image,
            )
            created.append(relay)
            docker(
                "run",
                "-d",
                "--name",
                proxy,
                "--network",
                network,
                "--ip",
                "172.31.242.10",
                "-p",
                f"127.0.0.1:{port}:8443",
                "-v",
                f"{Path('deploy/nginx.conf').resolve()}:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{directory / 'cert.pem'}:/etc/nginx/tls/fullchain.pem:ro",
                "-v",
                f"{directory / 'key.pem'}:/etc/nginx/tls/privkey.pem:ro",
                "nginx:1.28-alpine",
            )
            created.append(proxy)
            asyncio.run(
                exercise(
                    f"https://localhost:{port}",
                    directory,
                    "smoke_operator",
                    operator_secret,
                    agent_secret,
                )
            )
            assert docker("exec", relay, "id", "-u") == "10001"
            print(
                f"PASS: Linux relay + TLS 1.3 + HMAC login + {'Windows' if os.name == 'nt' else 'Linux'} agent + telemetry + shell + chunked SHA-256 transfer; relay UID 10001."
            )
        except Exception:
            if relay in created:
                print(docker("logs", relay, check=False))
            raise
        finally:
            for container in (proxy, relay):
                if container in created:
                    docker("rm", "-f", "-v", container, check=False)
            if network in created:
                docker("network", "rm", network, check=False)


if __name__ == "__main__":
    main()
