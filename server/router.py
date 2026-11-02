import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from common.activity import describe_action
from common.protocol import ProtocolError, canonical
from server.auth import Auth, Session
from server.config import Settings
from server.registry import Node, Registry
from server.storage import Storage


@dataclass
class Command:
    id: str
    session_id: str
    node_id: str
    agent_session: str
    generation: int
    tool: str
    deadline: float
    created: float = field(default_factory=time.time)
    completed: float = 0
    events: list[dict] = field(default_factory=list)
    next_sequence: int = 1
    output_bytes: int = 0
    buffered_bytes: int = 0
    finished: bool = False
    finishing: bool = False
    result: dict | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def view(self, after: int = 0) -> dict:
        return {
            "command_id": self.id,
            "session_id": self.session_id,
            "target_node_id": self.node_id,
            "finished": self.finished,
            "result": self.result,
            "events": [e for e in self.events if e["seq"] > after],
            "first_seq": self.events[0]["seq"] if self.events else self.next_sequence,
            "next_seq": self.next_sequence,
        }


class Router:
    def __init__(self, settings: Settings, auth: Auth, registry: Registry, storage: Storage):
        self.settings = settings
        self.auth = auth
        self.registry = registry
        self.storage = storage
        self.commands: dict[str, Command] = {}
        self.controllers: dict[str, web.WebSocketResponse] = {}

    async def send_node(self, node: Node, message: dict) -> None:
        async with node.send_lock:
            if not node.ready or node.socket is None or node.socket.closed:
                raise ProtocolError("Node is offline")
            await asyncio.wait_for(node.socket.send_json(message), timeout=5)

    async def submit(
        self,
        session: Session,
        node_id: str,
        tool: Any,
        args: Any,
        timeout: Any = None,
        origin: Any = "controller",
    ) -> Command:
        if not isinstance(tool, str) or not session.credential.permits(node_id, tool):
            raise ProtocolError("Tool or node access denied")
        if session.expires <= time.time():
            raise ProtocolError("Session expired")
        if not isinstance(args, dict) or len(canonical(args)) > 192 * 1024:
            raise ProtocolError("Invalid command arguments")
        if not isinstance(origin, str) or origin not in {"controller", "mcp"}:
            raise ProtocolError("Invalid command origin")
        seconds = timeout if timeout is not None else self.settings.command_timeout
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int | float)
            or not 1 <= seconds <= self.settings.command_timeout
        ):
            raise ProtocolError("Invalid command timeout")
        self.prune()
        active = [c for c in self.commands.values() if not c.finished]
        if (
            len(self.commands) >= self.settings.max_commands
            or len(active) >= self.settings.max_running
            or sum(c.node_id == node_id for c in active) >= 8
            or sum(c.session_id == session.id for c in active) >= 16
        ):
            raise ProtocolError("Command capacity reached")
        node = self.registry.nodes.get(node_id)
        if node is None or not node.ready or node.socket is None or node.socket.closed:
            raise ProtocolError("Node is offline")
        if tool in {"open", "desktop"} and tool not in node.metadata.get("tools", []):
            raise ProtocolError("Tool is disabled or this Agent requires an update")
        command = Command(
            "cmd_" + secrets.token_hex(16),
            session.id,
            node_id,
            node.session_id,
            session.generation,
            tool,
            time.time() + seconds,
        )
        self.commands[command.id] = command
        await self.storage.audit(
            "command.submit",
            session.credential.principal,
            session.id,
            node_id,
            command.id,
            {
                "tool": tool,
                "args_sha256": hashlib.sha256(canonical(args)).hexdigest(),
                "timeout": seconds,
            },
        )
        try:
            await self.send_node(
                node,
                {
                    "type": "command",
                    "session_id": session.id,
                    "target_node_id": node_id,
                    "command_id": command.id,
                    "tool": tool,
                    "args": args,
                    "timeout": seconds,
                    "origin": origin,
                    "activity": describe_action(tool, args, origin),
                },
            )
        except (ProtocolError, TimeoutError, ConnectionError):
            await self.finish(command, {"ok": False, "error": "Node delivery failed"})
        return command

    async def event(self, command: Command, kind: str, payload: dict) -> None:
        event = {
            "type": "event",
            "session_id": command.session_id,
            "target_node_id": command.node_id,
            "command_id": command.id,
            "seq": command.next_sequence,
            "kind": kind,
            **payload,
        }
        command.next_sequence += 1
        command.events.append(event)
        command.buffered_bytes += len(canonical(event))
        if len(command.events) > self.settings.max_events:
            command.buffered_bytes -= len(canonical(command.events.pop(0)))
        command.changed.set()
        socket = self.controllers.get(command.session_id)
        session = self.auth.sessions.get(command.session_id)
        if (
            socket
            and session
            and session.target == command.node_id
            and session.generation == command.generation
            and not socket.closed
        ):
            try:
                await asyncio.wait_for(socket.send_json(event), timeout=5)
            except (TimeoutError, ConnectionError):
                await socket.close()

    async def finish(self, command: Command, result: dict) -> None:
        if command.finished or command.finishing:
            return
        command.finishing = True
        session = self.auth.sessions.get(command.session_id)
        try:
            await self.storage.audit(
                "command.finish",
                session.credential.principal if session else "",
                command.session_id,
                command.node_id,
                command.id,
                {
                    "ok": result.get("ok") is True,
                    "result_sha256": hashlib.sha256(canonical(result)).hexdigest(),
                    "output_bytes": command.output_bytes,
                },
            )
        except BaseException:
            command.finishing = False
            raise
        command.result = result
        command.finished = True
        command.completed = time.time()
        await self.event(command, "result", {"result": result})
        self.prune()

    async def accept(self, node: Node, message: dict) -> None:
        command_id = message.get("command_id")
        if not isinstance(command_id, str):
            raise ProtocolError("Invalid command_id")
        command = self.commands.get(command_id)
        if command is None or command.finished or command.finishing:
            return
        if (
            command.node_id != node.id
            or command.agent_session != node.session_id
            or message.get("target_node_id") != node.id
            or message.get("session_id") != command.session_id
        ):
            raise ProtocolError("Command routing mismatch")
        kind = message.get("type")
        if kind == "output":
            stream, data = message.get("stream"), message.get("data")
            if (
                stream not in {"stdout", "stderr"}
                or not isinstance(data, str)
                or len(data.encode()) > 16384
            ):
                raise ProtocolError("Invalid output event")
            command.output_bytes += len(data.encode())
            if command.output_bytes > self.settings.max_output:
                await self.cancel(command, "Output limit reached")
                return
            await self.event(command, "output", {"stream": stream, "data": data})
        elif kind == "result":
            result = message.get("result")
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("ok"), bool)
                or len(canonical(result)) > 192 * 1024
            ):
                raise ProtocolError("Invalid command result")
            await self.finish(command, result)
        else:
            raise ProtocolError("Unknown agent event")

    def owned(self, session: Session, command_id: str) -> Command:
        if not isinstance(command_id, str):
            raise ProtocolError("Invalid command_id")
        command = self.commands.get(command_id)
        if command is None or command.session_id != session.id:
            raise ProtocolError("Command not found")
        return command

    async def cancel(self, command: Command, reason: str = "Cancelled") -> None:
        if command.finished:
            return
        await self.finish(command, {"ok": False, "error": reason})
        node = self.registry.nodes.get(command.node_id)
        if node and node.session_id == command.agent_session:
            try:
                await self.send_node(
                    node,
                    {
                        "type": "cancel",
                        "session_id": command.session_id,
                        "target_node_id": command.node_id,
                        "command_id": command.id,
                    },
                )
            except (ProtocolError, TimeoutError, ConnectionError):
                pass

    async def switch(self, session: Session, value: str) -> dict:
        node = self.registry.select(session, value)
        session.generation += 1
        session.target = node.id
        for command in list(self.commands.values()):
            if command.session_id == session.id and not command.finished:
                await self.cancel(command, "Target switched")
        await self.storage.audit(
            "session.switch", session.credential.principal, session.id, node.id
        )
        return node.view()

    async def disconnect_controller(self, session: Session) -> None:
        self.controllers.pop(session.id, None)
        for command in list(self.commands.values()):
            if command.session_id == session.id:
                await self.cancel(command, "Controller disconnected")
        self.auth.revoke(session.id)

    async def disconnect_node(self, node: Node) -> None:
        for command in list(self.commands.values()):
            if command.agent_session == node.session_id:
                await self.finish(command, {"ok": False, "error": "Node disconnected"})

    def prune(self) -> None:
        cutoff = time.time() - self.settings.retention
        self.commands = {
            k: v for k, v in self.commands.items() if not v.finished or v.completed > cutoff
        }
        total = sum(command.buffered_bytes for command in self.commands.values())
        for command in sorted(
            (c for c in self.commands.values() if c.finished), key=lambda c: c.completed
        ):
            if total <= self.settings.max_buffered:
                break
            self.commands.pop(command.id, None)
            total -= command.buffered_bytes

    async def tick(self) -> None:
        for command in list(self.commands.values()):
            if not command.finished and (
                command.deadline <= time.time() or command.session_id not in self.auth.sessions
            ):
                await self.cancel(command, "Command deadline or session expired")
        self.prune()
