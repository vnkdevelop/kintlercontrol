import asyncio
import json
import secrets
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp.test_utils import TestServer

from server.app import SERVICE, create_app
from server.auth import Session
from server.config import Credential, Settings
from server.registry import Registry
from server.storage import Storage


async def rows(storage: Storage) -> list[tuple]:
    return await storage.run(
        lambda: storage.connection.execute("SELECT seq,action FROM audit ORDER BY seq").fetchall()
    )


async def test_audit_row_limit_is_enforced_on_every_write(tmp_path: Path):
    storage = Storage(tmp_path / "audit.sqlite3", audit_max_rows=3)
    try:
        for index in range(8):
            await storage.audit(f"event.{index}")
            assert len(await rows(storage)) <= 3
        assert [action for _, action in await rows(storage)] == ["event.5", "event.6", "event.7"]
        assert not storage.connection.in_transaction
    finally:
        await storage.close()


async def test_expiry_preserves_recent_audit_and_device_registrations(tmp_path: Path, monkeypatch):
    storage = Storage(tmp_path / "audit.sqlite3", audit_retention=60)
    try:
        await storage.save_node("node_a", {"name": "retained"}, 10)
        await storage.save_agent_key("agent_a", "node_a", "fixture")
        monkeypatch.setattr("server.storage.time.time", lambda: 100)
        await storage.audit("old")
        monkeypatch.setattr("server.storage.time.time", lambda: 160)
        await storage.audit("boundary")
        monkeypatch.setattr("server.storage.time.time", lambda: 220)
        assert await storage.prune_audit() == 1
        assert [action for _, action in await rows(storage)] == ["boundary"]
        assert len(await storage.load_nodes()) == len(await storage.load_agent_keys()) == 1
        assert await storage.prune_audit() == 0
    finally:
        await storage.close()


async def test_pruning_is_batched_and_enforces_limits_for_existing_database(tmp_path: Path):
    storage = Storage(tmp_path / "audit.sqlite3", audit_max_rows=2)
    try:

        def seed():
            storage.connection.executemany(
                "INSERT INTO audit(at,action,principal,session_id,node_id,command_id,detail) VALUES(?,'legacy','','','','','{}')",
                [(time.time(),)] * 1502,
            )
            storage.connection.commit()

        await storage.run(seed)
        assert await storage.prune_audit() == 1000
        assert await storage.prune_audit() == 500
        assert len(await rows(storage)) == 2
        assert await storage.prune_audit() == 0
    finally:
        await storage.close()


async def test_audit_pages_are_reused_after_repeated_pruning(tmp_path: Path):
    storage = Storage(tmp_path / "audit.sqlite3", audit_max_rows=20)
    try:
        for _ in range(50):
            await storage.audit("bounded", detail={"fixture": "x" * 1024})
        first = await storage.run(
            lambda: storage.connection.execute("PRAGMA page_count").fetchone()[0]
        )
        for _ in range(100):
            await storage.audit("bounded", detail={"fixture": "x" * 1024})
        last = await storage.run(
            lambda: storage.connection.execute("PRAGMA page_count").fetchone()[0]
        )
        assert last <= first + 2
        assert len(await rows(storage)) == 20
    finally:
        await storage.close()


async def test_server_cleans_expired_audit_on_start_and_during_maintenance(tmp_path: Path):
    database = tmp_path / "audit.sqlite3"
    storage = Storage(database)
    await storage.audit("expired")
    await storage.run(lambda: storage.connection.execute("UPDATE audit SET at=0"))
    await storage.run(storage.connection.commit)
    await storage.close()
    app = create_app(Settings({}, database, audit_retention=60, audit_max_rows=5))
    async with TestServer(app):
        service = app[SERVICE]
        assert await rows(service.storage) == []
        await service.storage.audit("expire.again")
        await service.storage.run(
            lambda: service.storage.connection.execute("UPDATE audit SET at=0")
        )
        await service.storage.run(service.storage.connection.commit)
        service.audit_cleanup_due = 0
        async with asyncio.timeout(6):
            while await rows(service.storage):
                await asyncio.sleep(0.05)
        assert service.maintenance_healthy
        assert service.audit_cleanup_due > time.monotonic()


@pytest.mark.parametrize(
    "name,value",
    [
        ("KINTLER_AUDIT_RETENTION_DAYS", "0"),
        ("KINTLER_AUDIT_RETENTION_DAYS", "-1"),
        ("KINTLER_AUDIT_RETENTION_DAYS", "3651"),
        ("KINTLER_AUDIT_RETENTION_DAYS", "invalid"),
        ("KINTLER_AUDIT_MAX_ROWS", "0"),
        ("KINTLER_AUDIT_MAX_ROWS", "1000001"),
        ("KINTLER_AUDIT_MAX_ROWS", "invalid"),
    ],
)
def test_invalid_audit_limits_fail_closed(monkeypatch, name: str, value: str):
    monkeypatch.setenv(
        "KINTLER_CREDENTIALS",
        json.dumps(
            [
                {
                    "id": "agent_a",
                    "role": "agent",
                    "node_id": "node_a",
                    "secret": secrets.token_urlsafe(48),
                }
            ]
        ),
    )
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="KINTLER_AUDIT"):
        Settings.from_env()


def test_audit_limits_are_configurable(monkeypatch):
    monkeypatch.setenv(
        "KINTLER_CREDENTIALS",
        json.dumps(
            [
                {
                    "id": "agent_a",
                    "role": "agent",
                    "node_id": "node_a",
                    "secret": secrets.token_urlsafe(48),
                }
            ]
        ),
    )
    monkeypatch.setenv("KINTLER_AUDIT_RETENTION_DAYS", "7")
    monkeypatch.setenv("KINTLER_AUDIT_MAX_ROWS", "50")
    settings = Settings.from_env()
    assert settings.audit_retention == 7 * 86400
    assert settings.audit_max_rows == 50


async def test_heartbeat_liveness_is_fresh_without_writing_every_pulse(tmp_path: Path, monkeypatch):
    storage = Storage(tmp_path / "audit.sqlite3")
    registry = Registry(storage)
    clock = [100.0]
    monkeypatch.setattr("server.registry.time.monotonic", lambda: clock[0])
    session = Session("session_a", Credential("agent_a", "", "agent", "node_a"), 1000)
    try:
        node = await registry.connect(session, Mock(), {"name": "first"})
        persist = AsyncMock(wraps=storage.save_node)
        monkeypatch.setattr(storage, "save_node", persist)
        for second in (110, 120, 150):
            clock[0] = second
            await registry.heartbeat(node, {"name": "updated"})
        assert node.metadata["name"] == "updated"
        assert node.last_seen > 0
        persist.assert_not_awaited()
        clock[0] = 160
        await registry.heartbeat(node, {"name": "saved"})
        assert persist.await_count == 1
        clock[0] = 170
        await registry.heartbeat(node, {"name": "final"})
        await registry.disconnect(node)
        assert persist.await_count == 2
        assert (await storage.load_nodes())[0][1]["name"] == "final"
    finally:
        await storage.close()
