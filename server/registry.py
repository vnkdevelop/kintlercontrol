import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from common.protocol import TOOLS, ProtocolError
from server.auth import Session
from server.storage import Storage


@dataclass
class Node:
    id: str
    metadata: dict
    last_seen: float
    socket: web.WebSocketResponse | None = None
    session_id: str = ""
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: bool = False
    persisted_at: float = 0.0

    def view(self) -> dict:
        return {
            **self.metadata,
            "id": self.id,
            "online": self.ready and self.socket is not None and not self.socket.closed,
            "last_seen": self.last_seen,
        }


class Registry:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.nodes: dict[str, Node] = {}

    async def load(self) -> None:
        for node_id, metadata, last_seen in await self.storage.load_nodes():
            self.nodes[node_id] = Node(node_id, metadata, last_seen)

    def metadata(self, value: object) -> dict:
        if not isinstance(value, dict):
            raise ProtocolError("Metadata must be an object")
        result: dict[str, Any] = {}
        for key in ("name", "platform", "user", "version"):
            entry = value.get(key, "")
            if not isinstance(entry, str) or len(entry) > 200 or any(ord(c) < 32 for c in entry):
                raise ProtocolError("Invalid node metadata")
            result[key] = entry
        result["admin"] = value.get("admin") is True
        tools = value.get("tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, str) or tool not in TOOLS for tool in tools
        ):
            raise ProtocolError("Invalid node tools")
        result["tools"] = tools
        result["telemetry"] = value.get("telemetry", {})
        if not isinstance(result["telemetry"], dict) or len(str(result["telemetry"])) > 8192:
            raise ProtocolError("Invalid telemetry")
        return result

    async def connect(
        self, session: Session, socket: web.WebSocketResponse, metadata: object
    ) -> Node:
        if not session.approved:
            raise ProtocolError("This agent connection has not been approved")
        node_id = session.credential.node_id
        previous = self.nodes.get(node_id)
        if previous and previous.socket and not previous.socket.closed:
            raise ProtocolError("Node already connected")
        node = Node(node_id, self.metadata(metadata), time.time(), socket, session.id)
        self.nodes[node_id] = node
        await self.storage.save_node(node_id, node.metadata, node.last_seen)
        node.persisted_at = time.monotonic()
        return node

    async def heartbeat(self, node: Node, metadata: object) -> None:
        node.metadata = self.metadata(metadata)
        node.last_seen = time.time()
        if time.monotonic() - node.persisted_at >= 60:
            await self.storage.save_node(node.id, node.metadata, node.last_seen)
            node.persisted_at = time.monotonic()

    async def disconnect(self, node: Node) -> None:
        if self.nodes.get(node.id) is node:
            node.ready = False
            node.socket = None
            await self.storage.save_node(node.id, node.metadata, node.last_seen)

    def select(self, session: Session, value: str) -> Node:
        matches = [
            node
            for node in self.nodes.values()
            if session.credential.permits(node.id)
            and (node.id == value or node.metadata.get("name", "").casefold() == value.casefold())
        ]
        if len(matches) != 1:
            raise ProtocolError("Node not found or name ambiguous")
        return matches[0]

    def inventory(self, session: Session) -> list[dict]:
        return [node.view() for node in self.nodes.values() if session.credential.permits(node.id)]
