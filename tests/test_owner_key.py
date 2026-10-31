import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer
from dotenv import dotenv_values

from client.kintler_sdk import ClientSettings, KintlerClient
from common.identity import new_private_key, public_key
from common.protocol import ProtocolError
from deploy.bootstrap_backend import initialize
from desktop.settings import controller_settings, load_profile
from scripts.prepare_owner import prepare
from server.app import create_app
from server.config import Credential, Settings


async def test_public_key_controller_and_unknown_controller_denied(tmp_path: Path):
    private = new_private_key()
    owner = Credential(
        "owner",
        "",
        public_key=public_key(private),
        nodes=frozenset({"*"}),
        tools=frozenset({"sysinfo"}),
        can_approve=True,
    )
    async with TestServer(
        create_app(Settings({"owner": owner}, tmp_path / "db", allow_local_http=True))
    ) as server:
        url = str(server.make_url("")).rstrip("/")
        async with KintlerClient(
            ClientSettings(url, "owner", "", True, private_key=private)
        ) as client:
            assert (await client.tools())["can_approve"]
        with pytest.raises(ProtocolError, match="failed"):
            async with KintlerClient(
                ClientSettings(url, "owner", "", True, private_key=new_private_key())
            ):
                pass
        with pytest.raises(ProtocolError, match="failed"):
            async with KintlerClient(
                ClientSettings(url, "unknown", "", True, private_key=new_private_key())
            ):
                pass


def test_owner_export_contains_only_public_key(tmp_path: Path):
    local = tmp_path / "private/.env"
    public = tmp_path / "owner-public.json"
    prepare("https://example.com:8443", public, local)
    values = load_profile(local, "controller")
    settings = controller_settings(values)
    exported = json.loads(public.read_text())
    assert exported["public_key"] == public_key(settings.private_key)
    assert "PRIVATE" not in public.read_text() and "secret" not in public.read_text()
    server = tmp_path / "server"
    initialize(server, public)
    credentials = json.loads(dotenv_values(server / ".env")["KINTLER_CREDENTIALS"])
    assert credentials[0]["can_approve"] and credentials[0]["public_key"] == exported["public_key"]
    assert "secret" not in credentials[0]
    with pytest.raises(FileExistsError):
        initialize(server, public)
