import asyncio
import secrets
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from agent.kintler_agent import Agent, AgentSettings
from client.kintler_sdk import ClientSettings, KintlerClient
from common.protocol import TOOLS
from server.app import SERVICE, Service, create_app
from server.config import Credential, Settings


@dataclass
class Environment:
    url: str
    service: Service
    operator: ClientSettings
    restricted: ClientSettings
    agents: list[Agent]
    tasks: list[asyncio.Task]
    root: Path


@pytest.fixture
async def environment(tmp_path: Path):
    controller = Credential(
        "operator", secrets.token_urlsafe(48), nodes=frozenset({"*"}), tools=TOOLS, can_approve=True
    )
    restricted = Credential(
        "limited",
        secrets.token_urlsafe(48),
        nodes=frozenset({"node_a"}),
        tools=frozenset({"sysinfo"}),
    )
    devices = [
        Credential("agent_a", secrets.token_urlsafe(48), "agent", "node_a"),
        Credential("agent_b", secrets.token_urlsafe(48), "agent", "node_b"),
    ]
    credentials = {c.principal: c for c in [controller, restricted, *devices]}
    app = create_app(Settings(credentials, tmp_path / "audit.sqlite3", allow_local_http=True))
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        async with aiohttp.ClientSession() as http:
            agents = [
                Agent(
                    AgentSettings(
                        url,
                        device.principal,
                        device.secret,
                        device.node_id,
                        f"device-{index}",
                        tmp_path / device.node_id,
                        TOOLS,
                        True,
                    )
                )
                for index, device in enumerate(devices)
            ]
            tasks = [asyncio.create_task(agent.connect(http)) for agent in agents]
            async with (
                KintlerClient(
                    ClientSettings(url, controller.principal, controller.secret, True)
                ) as approver,
                asyncio.timeout(15),
            ):
                while (
                    sum(node.view()["online"] for node in app[SERVICE].registry.nodes.values()) < 2
                ):
                    for task in tasks:
                        if task.done():
                            task.result()
                    for pending in await approver.approvals():
                        await approver.approve(
                            pending["request_id"],
                            True,
                            app[SERVICE].approvals.requests[pending["request_id"]].code,
                        )
                    await asyncio.sleep(0.02)
            try:
                yield Environment(
                    url,
                    app[SERVICE],
                    ClientSettings(url, controller.principal, controller.secret, True),
                    ClientSettings(url, restricted.principal, restricted.secret, True),
                    agents,
                    tasks,
                    tmp_path,
                )
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
