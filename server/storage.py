import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


class Storage:
    def __init__(self, path: Path, audit_retention: int = 30 * 86400, audit_max_rows: int = 100000):
        if audit_retention <= 0 or audit_max_rows <= 0:
            raise ValueError("Audit retention and row limit must be positive")
        self.audit_retention = audit_retention
        self.audit_max_rows = audit_max_rows
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        if self.connection.execute("PRAGMA page_count").fetchone()[0] == 0:
            self.connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.lock = asyncio.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kintler-sqlite")
        self.connection.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA journal_size_limit=4194304;"
            "CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, metadata TEXT NOT NULL, last_seen REAL NOT NULL);"
            "CREATE TABLE IF NOT EXISTS agent_keys (principal TEXT PRIMARY KEY, node_id TEXT UNIQUE NOT NULL, public_key TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, action TEXT NOT NULL, principal TEXT NOT NULL, session_id TEXT NOT NULL, node_id TEXT NOT NULL, command_id TEXT NOT NULL, detail TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS audit_at ON audit(at);"
        )
        self.connection.commit()

    async def run(self, operation: Callable[..., T], *args: Any) -> T:
        async with self.lock:
            work = asyncio.get_running_loop().run_in_executor(
                self.executor, partial(operation, *args)
            )
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise

    async def audit(
        self,
        action: str,
        principal: str = "",
        session: str = "",
        node: str = "",
        command: str = "",
        detail: dict | None = None,
    ) -> None:
        await self.run(self._audit, action, principal, session, node, command, detail or {})

    def _audit(
        self, action: str, principal: str, session: str, node: str, command: str, detail: dict
    ) -> None:
        cursor = self.connection.execute(
            "INSERT INTO audit(at,action,principal,session_id,node_id,command_id,detail) VALUES(?,?,?,?,?,?,?)",
            (time.time(), action, principal, session, node, command, json.dumps(detail)),
        )
        if cursor.lastrowid is not None:
            self.connection.execute(
                "DELETE FROM audit WHERE seq<=?", (cursor.lastrowid - self.audit_max_rows,)
            )
        self.connection.commit()

    async def prune_audit(self) -> int:
        return await self.run(self._prune_audit)

    def _prune_audit(self) -> int:
        latest = self.connection.execute("SELECT COALESCE(MAX(seq),0) FROM audit").fetchone()[0]
        cursor = self.connection.execute(
            "DELETE FROM audit WHERE seq IN (SELECT seq FROM audit WHERE at<? OR seq<=? LIMIT 1000)",
            (time.time() - self.audit_retention, latest - self.audit_max_rows),
        )
        removed = cursor.rowcount
        self.connection.commit()
        if removed:
            self.connection.execute("PRAGMA incremental_vacuum(128)")
            self.connection.commit()
            self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return removed

    async def save_node(self, node_id: str, metadata: dict, last_seen: float) -> None:
        await self.run(self._save_node, node_id, metadata, last_seen)

    def _save_node(self, node_id: str, metadata: dict, last_seen: float) -> None:
        self.connection.execute(
            "INSERT INTO nodes VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata,last_seen=excluded.last_seen",
            (node_id, json.dumps(metadata), last_seen),
        )
        self.connection.commit()

    async def load_nodes(self) -> list[tuple[str, dict, float]]:
        rows = await self.run(
            lambda: self.connection.execute("SELECT id,metadata,last_seen FROM nodes").fetchall()
        )
        return [(row[0], json.loads(row[1]), row[2]) for row in rows]

    async def close(self) -> None:
        await self.run(self.connection.close)
        self.executor.shutdown(wait=True)

    async def health(self) -> None:
        def check() -> None:
            self.connection.execute("SAVEPOINT kintler_health")
            try:
                self.connection.execute("SELECT id FROM nodes LIMIT 1").fetchone()
                self.connection.execute("SELECT principal FROM agent_keys LIMIT 1").fetchone()
                self.connection.execute(
                    "INSERT INTO audit(at,action,principal,session_id,node_id,command_id,detail) VALUES(0,'health','','','','','{}')"
                )
            finally:
                self.connection.execute("ROLLBACK TO SAVEPOINT kintler_health")
                self.connection.execute("RELEASE SAVEPOINT kintler_health")

        await self.run(check)

    async def load_agent_keys(self) -> list[tuple[str, str, str]]:
        return await self.run(
            lambda: self.connection.execute(
                "SELECT principal,node_id,public_key FROM agent_keys"
            ).fetchall()
        )

    async def save_agent_key(self, principal: str, node: str, public: str) -> None:
        def save() -> None:
            self.connection.execute(
                "INSERT INTO agent_keys VALUES(?,?,?) ON CONFLICT(principal) DO UPDATE SET node_id=excluded.node_id,public_key=excluded.public_key",
                (principal, node, public),
            )
            self.connection.commit()

        await self.run(save)
