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
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = asyncio.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kintler-sqlite")
        self.connection.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;"
            "CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, metadata TEXT NOT NULL, last_seen REAL NOT NULL);"
            "CREATE TABLE IF NOT EXISTS agent_keys (principal TEXT PRIMARY KEY, node_id TEXT UNIQUE NOT NULL, public_key TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, action TEXT NOT NULL, principal TEXT NOT NULL, session_id TEXT NOT NULL, node_id TEXT NOT NULL, command_id TEXT NOT NULL, detail TEXT NOT NULL);"
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
        self.connection.execute(
            "INSERT INTO audit(at,action,principal,session_id,node_id,command_id,detail) VALUES(?,?,?,?,?,?,?)",
            (time.time(), action, principal, session, node, command, json.dumps(detail)),
        )
        self.connection.commit()

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
