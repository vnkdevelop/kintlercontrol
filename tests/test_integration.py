import asyncio
import os
import secrets
import sqlite3

import aiohttp
import pytest

from client.kintler_sdk import KintlerClient, LiveController
from common.protocol import ProtocolError, auth_message
from tests.conftest import Environment


async def test_inventory_tools_shell_and_telemetry(environment: Environment):
    async with KintlerClient(environment.operator) as client:
        assert len(await client.nodes()) == 2
        assert "shell" in (await client.tools())["tools"]
        result = await client.call("device-0", "sysinfo")
        assert result["ok"] and result["ram_total"] > 0
        result = await client.call(
            "node_b",
            "shell",
            {"shell": "cmd" if os.name == "nt" else "bash", "command": "echo NODE_B_ONLY"},
        )
        assert result["ok"] and "NODE_B_ONLY" in result["stdout"]
        result = await client.call("node_a", "ps", {"limit": 3})
        assert result["ok"] and len(result["processes"]) <= 3
    with sqlite3.connect(environment.root / "audit.sqlite3") as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM audit")]
        assert "command.submit" in actions and "command.finish" in actions


async def test_controller_acl_and_result_ownership(environment: Environment):
    async with (
        KintlerClient(environment.operator) as owner,
        KintlerClient(environment.restricted) as limited,
    ):
        assert [node["id"] for node in await limited.nodes()] == ["node_a"]
        with pytest.raises(ProtocolError):
            await limited.call("node_b", "sysinfo")
        with pytest.raises(ProtocolError, match="denied"):
            await limited.call("node_a", "shell", {"command": "echo NO"})
        command_id = await owner.submit("node_a", "sysinfo")
        with pytest.raises(ProtocolError, match="not found"):
            await limited.request("GET", f"/api/commands/{command_id}")
        assert (await limited.call("node_a", "sysinfo"))["ok"]


async def test_chunked_transfer_and_empty_file(environment: Environment):
    source = environment.root / "source.bin"
    source.write_bytes(secrets.token_bytes(160000))
    target = environment.root / "received.bin"
    async with KintlerClient(environment.operator) as client:
        uploaded = await client.upload("node_a", source, "remote.bin")
        downloaded = await client.download("node_a", "remote.bin", target)
        assert uploaded["sha256"] == downloaded["sha256"]
        assert source.read_bytes() == target.read_bytes()
        with pytest.raises(ProtocolError, match="exists"):
            await client.upload("node_a", source, "remote.bin")
        assert not environment.agents[0].transfers.uploads
        empty = environment.root / "empty"
        empty.write_bytes(b"")
        await client.upload("node_b", empty, "empty")
        await client.download("node_b", "empty", environment.root / "empty-out")
        assert (environment.root / "empty-out").read_bytes() == b""


async def test_live_switch_isolation_and_cancellation(environment: Environment):
    events = []

    async def emit(event: dict):
        events.append(event)

    async with LiveController(environment.operator, emit) as live:
        assert len((await live.request("nodes"))["nodes"]) == 2
        await live.request("use", node="node_a")
        command = (
            "ping -n 10 127.0.0.1 >nul & echo OLD_NODE"
            if os.name == "nt"
            else "sleep 10; echo OLD_NODE"
        )
        reply = await live.request(
            "command",
            tool="shell",
            args={"shell": "cmd" if os.name == "nt" else "bash", "command": command},
        )
        await live.request("use", node="node_b")
        switched_at = len(events)
        await live.request("command", tool="sysinfo", args={})
        async with asyncio.timeout(10):
            while not any(
                e["kind"] == "result" and e["target_node_id"] == "node_b" for e in events
            ):
                await asyncio.sleep(0.02)
        assert not any(e["target_node_id"] == "node_a" for e in events[switched_at:])
        previous = environment.service.router.commands[reply["command_id"]]
        assert previous.finished and previous.result["error"] == "Target switched"


async def test_forged_agent_routing_rejected(environment: Environment):
    async with KintlerClient(environment.operator) as client:
        node_a, node_b = (
            environment.service.registry.nodes["node_a"],
            environment.service.registry.nodes["node_b"],
        )
        session = environment.service.auth.sessions[client.session_id]
        command = await environment.service.router.submit(
            session,
            "node_a",
            "shell",
            {
                "shell": "cmd" if os.name == "nt" else "bash",
                "command": "ping -n 3 127.0.0.1 >nul" if os.name == "nt" else "sleep 3",
            },
        )
        with pytest.raises(ProtocolError, match="mismatch"):
            await environment.service.router.accept(
                node_b,
                {
                    "type": "result",
                    "command_id": command.id,
                    "session_id": command.session_id,
                    "target_node_id": "node_a",
                    "result": {"ok": True},
                },
            )
        assert node_a.id != node_b.id
        await client.cancel(command.id)


async def test_unauthenticated_http_and_invalid_json(environment: Environment):
    async with aiohttp.ClientSession() as http:
        async with http.get(environment.url + "/api/nodes") as response:
            assert response.status == 401
        async with http.post(environment.url + "/api/auth/login", json=[]) as response:
            assert response.status == 401
        async with http.post(environment.url + "/api/auth/login", data="not JSON") as response:
            assert response.status == 401


async def test_disconnect_marks_offline(environment: Environment):
    environment.tasks[0].cancel()
    await asyncio.gather(environment.tasks[0], return_exceptions=True)
    async with asyncio.timeout(5):
        while environment.service.registry.nodes["node_a"].view()["online"]:
            await asyncio.sleep(0.02)
    async with KintlerClient(environment.operator) as client:
        with pytest.raises(ProtocolError, match="offline"):
            await client.call("node_a", "sysinfo")


async def test_ws_auth_rejects_wrong_key(environment: Environment):
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(environment.url + "/ws/controller") as socket:
            challenge = await socket.receive_json()
            await socket.send_json(
                auth_message(
                    secrets.token_urlsafe(48),
                    challenge,
                    "controller",
                    environment.operator.principal,
                )
            )
            assert (await socket.receive_json())["type"] == "error"


async def test_local_agent_policy_overrides_operator_acl(environment: Environment):
    from dataclasses import replace

    environment.agents[0].settings = replace(
        environment.agents[0].settings, tools=frozenset({"sysinfo"})
    )
    async with KintlerClient(environment.operator) as client:
        result = await client.call("node_a", "shell", {"command": "echo BLOCKED"})
        assert not result["ok"] and "disabled" in result["error"]
        assert (await client.call("node_a", "sysinfo"))["ok"]
