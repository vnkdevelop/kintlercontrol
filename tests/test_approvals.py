import asyncio
import secrets
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from agent.kintler_agent import Agent, AgentSettings
from client.kintler_sdk import ClientSettings, KintlerClient
from common.identity import new_private_key, signed_auth
from common.protocol import TOOLS, ApprovalDenied, ProtocolError
from server.app import SERVICE, create_app
from server.config import Credential, Settings


async def pending(client: KintlerClient) -> dict:
    async with asyncio.timeout(5):
        while True:
            values = await client.approvals()
            if values:
                return values[0]
            await asyncio.sleep(0.01)


async def ready(service, node: str, online: bool) -> None:
    async with asyncio.timeout(5):
        while (
            bool(service.registry.nodes.get(node) and service.registry.nodes[node].view()["online"])
            != online
        ):
            await asyncio.sleep(0.01)


async def test_every_reconnection_requires_fresh_owner_decision(tmp_path: Path):
    owner = Credential(
        "owner", secrets.token_urlsafe(48), nodes=frozenset({"*"}), tools=TOOLS, can_approve=True
    )
    limited = Credential("limited", secrets.token_urlsafe(48), nodes=frozenset({"*"}), tools=TOOLS)
    app = create_app(
        Settings({"owner": owner, "limited": limited}, tmp_path / "db", allow_local_http=True)
    )
    key = new_private_key()
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        url = str(server.make_url("")).rstrip("/")
        service = app[SERVICE]
        settings = AgentSettings(
            url,
            "node_new",
            "",
            "node_new",
            "device",
            tmp_path / "workspace",
            TOOLS,
            True,
            private_key=key,
        )
        tasks = []
        try:
            async with (
                KintlerClient(ClientSettings(url, "owner", owner.secret, True)) as client,
                KintlerClient(ClientSettings(url, "limited", limited.secret, True)) as outsider,
            ):
                first = asyncio.create_task(Agent(settings).connect(http))
                tasks.append(first)
                request = await pending(client)
                assert request["code_required"] and not request["known"]
                assert await client.nodes() == []
                assert await outsider.approvals() == []
                with pytest.raises(ProtocolError, match="denied"):
                    await outsider.approve(request["request_id"], True)
                with pytest.raises(ProtocolError, match="code"):
                    await client.approve(request["request_id"], True, "incorrect")
                with pytest.raises(ProtocolError):
                    await client.request(
                        "POST", "/api/nodes/node_new/cmd", {"tool": "sysinfo", "args": {}}
                    )
                await client.approve(
                    request["request_id"],
                    True,
                    service.approvals.requests[request["request_id"]].code,
                )
                await ready(service, "node_new", True)
                assert (await client.call("node_new", "sysinfo"))["ok"]
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
                await ready(service, "node_new", False)
                second = asyncio.create_task(Agent(settings).connect(http))
                tasks.append(second)
                repeated = await pending(client)
                assert repeated["request_id"] != request["request_id"]
                assert repeated["known"] and not repeated["code_required"]
                assert not (await client.nodes())[0]["online"]
                with pytest.raises(ProtocolError, match="not found"):
                    await client.approve(request["request_id"], True)
                await client.approve(repeated["request_id"], False)
                with pytest.raises(ApprovalDenied):
                    await asyncio.wait_for(second, 5)
                assert not service.registry.nodes["node_new"].view()["online"]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_disconnect_and_expiry_do_not_cache_approval(tmp_path: Path):
    owner = Credential(
        "owner", secrets.token_urlsafe(48), nodes=frozenset({"*"}), tools=TOOLS, can_approve=True
    )
    app = create_app(
        Settings({"owner": owner}, tmp_path / "db", allow_local_http=True, approval_timeout=1)
    )
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        url = str(server.make_url("")).rstrip("/")
        service = app[SERVICE]
        async with KintlerClient(ClientSettings(url, "owner", owner.secret, True)) as client:
            task = asyncio.create_task(
                Agent(
                    AgentSettings(
                        url,
                        "node_x",
                        "",
                        "node_x",
                        "device",
                        tmp_path / "workspace",
                        TOOLS,
                        True,
                        private_key=new_private_key(),
                    )
                ).connect(http)
            )
            await pending(client)
            with pytest.raises(ApprovalDenied):
                await asyncio.wait_for(task, 5)
            assert not service.approvals.requests
            assert not service.registry.nodes
            assert await service.storage.load_agent_keys() == []


async def test_agent_cannot_send_commands_while_waiting(tmp_path: Path):
    owner = Credential(
        "owner", secrets.token_urlsafe(48), nodes=frozenset({"*"}), tools=TOOLS, can_approve=True
    )
    app = create_app(Settings({"owner": owner}, tmp_path / "db", allow_local_http=True))
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        async with http.ws_connect(server.make_url("/ws/agent")) as socket:
            challenge = await socket.receive_json()
            auth = signed_auth(new_private_key(), challenge, "node_test", "node_test")
            auth["metadata"] = {"name": "device", "tools": ["sysinfo"]}
            await socket.send_json(auth)
            assert (await socket.receive_json())["type"] == "approval_required"
            await socket.send_json({"type": "result", "result": {"ok": True}})
            assert (await socket.receive_json())["type"] == "error"
        assert not app[SERVICE].registry.nodes
