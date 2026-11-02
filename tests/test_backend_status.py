import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from textual.widgets import Static

from client.kintler_sdk import KintlerClient
from common.protocol import ProtocolError
from desktop.terminal_commands import parse_command
from desktop.terminal_controller import TerminalController
from tests.conftest import Environment


def connected_client(app: TerminalController):
    client = AsyncMock()
    client.request.return_value = {"status": "ok"}
    client.tools.return_value = {"tools": ["shell"], "can_approve": True}
    client.nodes.return_value = [
        {"id": "node_online", "name": "laptop", "online": True, "tools": ["shell"]},
        {"id": "node_offline", "name": "offline", "online": False, "tools": ["shell"]},
    ]
    client.approvals.return_value = [{"request_id": "req_pending", "name": "pending"}]
    app.client = client
    return client


async def test_status_contains_only_backend_connection_and_online_count(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        status = app.query_one("#status", Static)
        assert str(status.render()) == "Kintler не подключён · доступно 0"
        connected_client(app)
        await app.refresh_remote()
        assert str(status.render()) == "Kintler подключён · доступно 1"
        app.target = "node_online"
        app.update_status()
        assert str(status.render()) == "Kintler подключён · доступно 1"
        app.nodes["node_online"]["online"] = False
        app.update_status()
        assert str(status.render()) == "Kintler подключён · доступно 0"
        await app.disconnect()
        app.update_status()
        assert str(status.render()) == "Kintler не подключён · доступно 0"


@pytest.mark.parametrize(
    "failure", ["health", "health_payload", "tools", "nodes", "approvals", "timeout"]
)
async def test_backend_failure_stays_disconnected_until_full_check_recovers(
    tmp_path: Path, failure
):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        client = connected_client(app)
        await app.refresh_remote()
        app.target = "node_online"
        if failure == "health":
            client.request.return_value = {"status": "unhealthy"}
        elif failure == "health_payload":
            client.request.return_value = []
        else:
            getattr(client, "request" if failure == "timeout" else failure).side_effect = (
                TimeoutError() if failure == "timeout" else ProtocolError("Backend unavailable")
            )
        await app.refresh_remote()
        app.update_status()
        assert not app.backend_healthy
        assert str(app.query_one("#status", Static).render()) == "Kintler не подключён · доступно 0"
        client.request.return_value = {"status": "ok"}
        for method in (client.request, client.tools, client.nodes, client.approvals):
            method.side_effect = None
        await app.refresh_remote()
        assert app.backend_healthy
        assert str(app.query_one("#status", Static).render()) == "Kintler подключён · доступно 1"


async def test_missing_target_points_directly_to_use_by_name(tmp_path: Path):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        client = connected_client(app)
        await app.refresh_remote()
        with pytest.raises(ValueError) as error:
            app.require_target("shell", {})
        assert str(error.value) == "Используйте команду /use имя ПК"
        await app.execute(parse_command("/use laptop"))
        assert app.require_target("shell", {})[:2] == (client, "node_online")


async def test_health_checks_storage_without_leaving_probe_rows(environment: Environment):
    storage = environment.service.storage
    before = await storage.run(
        lambda: storage.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE action='health'"
        ).fetchone()
    )
    async with aiohttp.ClientSession() as http:
        async with http.get(environment.url + "/healthz") as response:
            assert response.status == 200
            assert (await response.json())["status"] == "ok"
    after = await storage.run(
        lambda: storage.connection.execute(
            "SELECT COUNT(*) FROM audit WHERE action='health'"
        ).fetchone()
    )
    assert before == after == (0,)
    assert not storage.connection.in_transaction


@pytest.mark.parametrize(
    "failure", ["database_readonly", "maintenance", "maintenance_stalled", "maintenance_stopped"]
)
async def test_health_reports_backend_component_failure(
    environment: Environment, monkeypatch, failure
):
    service = environment.service
    if failure == "database_readonly":
        await service.storage.run(
            lambda: service.storage.connection.execute("PRAGMA query_only=ON")
        )
    elif failure == "maintenance":
        monkeypatch.setattr(service, "maintenance_healthy", False)
    elif failure == "maintenance_stalled":
        monkeypatch.setattr(service, "maintenance_last_tick", time.monotonic() - 11)
    else:
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        monkeypatch.setattr(service, "maintenance_task", task)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(environment.url + "/healthz") as response:
                assert response.status == 503
                assert (await response.json())["status"] == "unhealthy"
    finally:
        if failure == "database_readonly":
            await service.storage.run(
                lambda: service.storage.connection.execute("PRAGMA query_only=OFF")
            )


async def test_server_api_failure_keeps_health_red_until_route_recovers(
    environment: Environment, monkeypatch
):
    service = environment.service
    inventory = service.registry.inventory
    async with KintlerClient(environment.operator) as client, aiohttp.ClientSession() as http:
        monkeypatch.setattr(
            service.registry, "inventory", Mock(side_effect=RuntimeError("Test API failure"))
        )
        with pytest.raises(ProtocolError, match="Internal server error"):
            await client.nodes()
        async with http.get(environment.url + "/healthz") as response:
            assert response.status == 503
        monkeypatch.setattr(service.registry, "inventory", inventory)
        await client.nodes()
        async with http.get(environment.url + "/healthz") as response:
            assert response.status == 200


async def test_controller_reflects_real_backend_database_failure_and_recovery(
    environment: Environment, tmp_path: Path
):
    app = TerminalController(tmp_path / ".env", autoconnect=False)
    async with app.run_test(size=(80, 24)):
        app.client = await KintlerClient(environment.operator).__aenter__()
        await app.refresh_remote()
        assert str(app.query_one("#status", Static).render()) == "Kintler подключён · доступно 2"
        storage = environment.service.storage
        await storage.run(lambda: storage.connection.execute("PRAGMA query_only=ON"))
        try:
            await app.refresh_remote()
            assert (
                str(app.query_one("#status", Static).render())
                == "Kintler не подключён · доступно 0"
            )
        finally:
            await storage.run(lambda: storage.connection.execute("PRAGMA query_only=OFF"))
        await app.refresh_remote()
        assert str(app.query_one("#status", Static).render()) == "Kintler подключён · доступно 2"
