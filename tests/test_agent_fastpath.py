import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from client.diagnostics import connection_snapshot
from client.kintler_sdk import ClientSettings, KintlerClient
from client.mcp_server import session_status
from common.protocol import ProtocolError
from desktop import terminal_entry
from tests.conftest import Environment


def client_fixture():
    client = AsyncMock()
    client.nodes.return_value = [
        {
            "id": "node_fixture",
            "name": "workstation",
            "online": True,
            "platform": "Windows",
            "admin": False,
            "tools": ["shell", "sysinfo"],
            "user": "private-account",
            "telemetry": {"cpu": 50},
        },
        {
            "id": "node_other",
            "name": "other",
            "online": False,
            "platform": "Linux",
            "tools": ["shell"],
        },
    ]
    client.tools.return_value = {
        "tools": ["shell"],
        "can_approve": True,
        "session_id": "private-session",
    }
    client.request.return_value = {"status": "ok"}
    return client


async def test_session_status_is_compact_read_only_and_explicitly_scoped():
    client = client_fixture()
    context = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(client=client))
    )
    result = await session_status(context, "WORKSTATION", ["shell"])
    assert result["ok"] and result["connected"] and result["available"] == 1
    assert result["target"]["id"] == "node_fixture"
    assert result["target"]["tools"] == ["shell"]
    assert result["target"]["interpreter"] == "cmd"
    assert result["scope"]["transfer_local_path"] == "controller_host"
    assert result["scope"]["approvals"] == "human_controller_only"
    assert "private-account" not in json.dumps(result)
    assert "private-session" not in json.dumps(result)
    assert "telemetry" not in json.dumps(result)
    client.request.assert_awaited_once_with("GET", "/healthz", auth=False)
    for method in (client.call, client.approve, client.upload, client.download):
        method.assert_not_awaited()


async def test_connection_reads_are_parallel_not_sequential():
    client = client_fixture()
    entered = set()
    ready = asyncio.Event()

    async def response(kind, value):
        entered.add(kind)
        if len(entered) == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), 1)
        return value

    nodes = client.nodes.return_value
    tools = client.tools.return_value

    async def inventory_response():
        return await response("nodes", nodes)

    async def tools_response():
        return await response("tools", tools)

    async def health_response(*args, **kwargs):
        return await response("health", {"status": "ok"})

    client.nodes.side_effect = inventory_response
    client.tools.side_effect = tools_response
    client.request.side_effect = health_response
    result = await connection_snapshot(client, "workstation")
    assert result["ok"] and entered == {"nodes", "tools", "health"}


@pytest.mark.parametrize(
    "case,selection",
    [
        ("missing", "not_found"),
        ("duplicate", "ambiguous"),
        ("offline", "offline"),
        ("blocked_tool", "tool_unavailable"),
        ("health", "backend_unhealthy"),
        ("network", "connection_failed"),
    ],
)
async def test_unready_target_never_becomes_automatic_or_authorized(case, selection):
    client = client_fixture()
    name = "workstation"
    required = ["shell"]
    if case == "missing":
        name = "missing"
    elif case == "duplicate":
        client.nodes.return_value.append(dict(client.nodes.return_value[0], id="node_duplicate"))
    elif case == "offline":
        client.nodes.return_value[0]["online"] = False
    elif case == "blocked_tool":
        required = ["sysinfo"]
    elif case == "health":
        client.request.return_value = {"status": "unhealthy"}
    else:
        client.nodes.side_effect = ProtocolError("private failure details")
    result = await connection_snapshot(client, name, required)
    assert not result["ok"] and result["selection"] == selection
    assert "private failure details" not in json.dumps(result)
    client.call.assert_not_awaited()
    client.approve.assert_not_awaited()


async def test_no_node_only_reports_inventory_and_does_not_select_first():
    result = await connection_snapshot(client_fixture())
    assert result["ok"] and result["target"] is None and result["selection"] == "not_requested"


async def test_message_checks_existing_shell_permission():
    client = client_fixture()
    assert (await connection_snapshot(client, "workstation", ["message"]))["ok"]
    client.tools.return_value["tools"] = []
    result = await connection_snapshot(client, "workstation", ["message"])
    assert not result["ok"] and result["missing_tools"] == ["message"]


def sdk_fixture():
    client = KintlerClient(ClientSettings("http://127.0.0.1", "operator", "fixture", True))
    client.session_id = "session"
    client.request = AsyncMock(
        return_value={"nodes": [{"id": "node_fixture", "name": "workstation"}]}
    )
    return client


async def test_known_exact_id_reuses_inventory_but_names_stay_fresh():
    client = sdk_fixture()
    await client.nodes()
    assert await client.resolve("node_fixture") == "node_fixture"
    assert await client.resolve("node_fixture") == "node_fixture"
    assert client.request.await_count == 1
    assert await client.resolve("workstation") == "node_fixture"
    assert client.request.await_count == 2
    client._nodes_checked_at = time.monotonic() - 4
    await client.resolve("node_fixture")
    assert client.request.await_count == 3


async def test_id_name_collision_is_not_hidden_by_cache():
    client = sdk_fixture()
    client.request.return_value["nodes"].append({"id": "node_other", "name": "node_fixture"})
    await client.nodes()
    with pytest.raises(ProtocolError, match="ambiguous"):
        await client.resolve("node_fixture")
    assert client.request.await_count == 2


async def test_changed_session_and_logout_drop_inventory_cache():
    client = sdk_fixture()
    await client.nodes()
    client.session_id = "new-session"
    await client.resolve("node_fixture")
    assert client.request.await_count == 2
    await client.__aexit__()
    assert not client._known_node_ids


def test_help_advertises_modes_without_source_inspection(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["KintlerController.exe", "--help"])
    with pytest.raises(SystemExit) as error:
        terminal_entry.main()
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert all(
        flag in help_text
        for flag in ("--mcp", "--mcp-config", "--install-skill", "--probe", "--setup", "--deploy")
    )
    assert "executes remote sysinfo" in help_text


def test_probe_cannot_be_combined_with_mutating_modes(monkeypatch):
    monkeypatch.setattr("sys.argv", ["KintlerController.exe", "--probe", "--setup"])
    setup = Mock()
    monkeypatch.setattr(terminal_entry, "setup_main", setup)
    with pytest.raises(SystemExit) as error:
        terminal_entry.main()
    assert error.value.code == 2
    setup.assert_not_called()


async def test_probe_uses_only_reads_and_never_smoke_commands(tmp_path: Path, monkeypatch):
    client = client_fixture()
    client.__aenter__.return_value = client
    monkeypatch.setattr(terminal_entry, "load_profile", Mock(return_value={}))
    monkeypatch.setattr(terminal_entry, "controller_settings", Mock())
    monkeypatch.setattr(terminal_entry, "KintlerClient", Mock(return_value=client))
    assert (await terminal_entry.probe(tmp_path / ".env", "workstation"))["ok"]
    client.call.assert_not_awaited()
    client.approve.assert_not_awaited()
    assert not list(tmp_path.iterdir())


async def test_real_status_does_not_create_commands_and_acl_still_enforced(
    environment: Environment,
):
    commands = dict(environment.service.router.commands)
    async with KintlerClient(environment.operator) as client:
        snapshot = await connection_snapshot(client, "device-0", ["shell"])
        assert snapshot["ok"] and snapshot["target"]["id"] == "node_a"
        assert environment.service.router.commands == commands
    async with KintlerClient(environment.restricted) as client:
        await client.nodes()
        client._known_node_ids.add("node_b")
        with pytest.raises(ProtocolError):
            await client.call("node_b", "sysinfo")
