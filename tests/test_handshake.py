import asyncio
import secrets
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from agent.kintler_agent import Agent, AgentSettings
from client.kintler_sdk import ClientSettings, KintlerClient
from common.protocol import TOOLS, ProtocolError
from server.app import SERVICE, create_app
from server.config import Credential, Settings


async def test_agent_not_routable_until_authentication_ack(tmp_path: Path, monkeypatch):
    operator = Credential(
        "op", secrets.token_urlsafe(48), nodes=frozenset({"node_a"}), tools=TOOLS, can_approve=True
    )
    device = Credential("agent", secrets.token_urlsafe(48), "agent", "node_a")
    app = create_app(
        Settings({"op": operator, "agent": device}, tmp_path / "db", allow_local_http=True)
    )
    service = app[SERVICE]
    published = asyncio.Event()
    release = asyncio.Event()
    audit = service.storage.audit

    async def delayed_audit(action, *args, **kwargs):
        if action == "ws.connect":
            published.set()
            await release.wait()
        return await audit(action, *args, **kwargs)

    monkeypatch.setattr(service.storage, "audit", delayed_audit)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        url = str(server.make_url("")).rstrip("/")
        agent = Agent(
            AgentSettings(
                url, "agent", device.secret, "node_a", "device", tmp_path / "workspace", TOOLS, True
            )
        )
        task = asyncio.create_task(agent.connect(http))
        try:
            async with KintlerClient(ClientSettings(url, "op", operator.secret, True)) as client:
                async with asyncio.timeout(5):
                    while not await client.approvals():
                        await asyncio.sleep(0.01)
                pending = (await client.approvals())[0]
                await client.approve(
                    pending["request_id"],
                    True,
                    service.approvals.requests[pending["request_id"]].code,
                )
                await asyncio.wait_for(published.wait(), 5)
                assert (await client.nodes())[0]["online"] is False
                with pytest.raises(ProtocolError, match="offline"):
                    await client.call("node_a", "sysinfo")
                release.set()
                async with asyncio.timeout(5):
                    while not service.registry.nodes["node_a"].ready:
                        await asyncio.sleep(0.01)
                assert (await client.call("node_a", "sysinfo"))["ok"]
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
