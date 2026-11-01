import argparse
import asyncio
import contextlib
import json
import logging
import os
import random
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import psutil
from dotenv import load_dotenv

from agent.fingerprint import node_id
from agent.modules import admin_tools, executor, monitor, processes
from agent.modules.fs_transfer import Transfers, sha256_file
from common.identity import signed_auth, unlock
from common.protocol import (
    MAX_MESSAGE,
    SAFE_TOOLS,
    TOOLS,
    ApprovalDenied,
    ProtocolError,
    auth_message,
    client_tls,
    identifier,
    ws_url,
)

logger = logging.getLogger("kintler.agent")


@dataclass(frozen=True)
class AgentSettings:
    url: str
    principal: str
    secret: str = field(repr=False)
    id: str = ""
    name: str = ""
    root: Path = Path("workspace")
    tools: frozenset[str] = SAFE_TOOLS
    allow_local_http: bool = False
    ca_file: str | None = None
    private_key: bytes = field(default=b"", repr=False)

    @classmethod
    def from_env(cls, directory: Path | None = None) -> "AgentSettings":
        secret = os.environ.get("KINTLER_AGENT_SECRET", "")
        private = (
            unlock(os.environ["KINTLER_AGENT_PRIVATE_KEY"], "agent")
            if os.environ.get("KINTLER_AGENT_PRIVATE_KEY")
            else b""
        )
        if not private and len(secret) < 32:
            raise ValueError("KINTLER_AGENT_SECRET must be at least 32 characters")
        tools = frozenset(
            t.strip()
            for t in os.environ.get("KINTLER_AGENT_TOOLS", ",".join(sorted(SAFE_TOOLS))).split(",")
            if t.strip()
        )
        if not tools <= TOOLS:
            raise ValueError("Unknown agent tool")
        root = Path(os.environ.get("KINTLER_WORKSPACE", "workspace"))
        if not root.is_absolute():
            root = (directory or Path.cwd()) / root
        return cls(
            os.environ["KINTLER_URL"],
            identifier(os.environ["KINTLER_AGENT_ID"]),
            secret,
            node_id(),
            os.environ.get("KINTLER_NODE_NAME", socket.gethostname()),
            root,
            tools,
            os.environ.get("KINTLER_ALLOW_LOCAL_HTTP") == "1",
            os.environ.get("KINTLER_CA_FILE") or None,
            private,
        )


class Agent:
    def __init__(
        self, settings: AgentSettings, observer: Callable[[str, dict], None] | None = None
    ):
        self.settings = settings
        self.observer = observer
        self.transfers = Transfers(settings.root)
        self.jobs: dict[str, tuple[str, asyncio.Task]] = {}
        self.seen: dict[str, float] = {}
        self.send_lock = asyncio.Lock()
        self.file_lock = asyncio.Lock()
        self.heartbeat_sent = 0.0
        self.ping_ms: float | None = None

    def notify(self, event: str, detail: dict) -> None:
        if self.observer:
            self.observer(event, detail)

    async def send(self, socket: aiohttp.ClientWebSocketResponse, message: dict) -> None:
        async with self.send_lock:
            await asyncio.wait_for(socket.send_json(message), 5)

    async def invoke(
        self, tool: str, args: dict, session_id: str, emit: executor.Emit, timeout: float
    ) -> dict:
        if tool not in self.settings.tools:
            raise ProtocolError("Tool is disabled by the local device owner")
        root = self.transfers.root
        if tool == "shell":
            return await executor.execute(args, root, emit, timeout)
        if tool == "sysinfo":
            return await asyncio.to_thread(monitor.sysinfo)
        if tool == "ps":
            return await asyncio.to_thread(processes.processes, args)
        if tool == "kill":
            return await asyncio.to_thread(processes.kill, args)
        if tool in {"upload", "download"}:
            async with self.file_lock:
                work = asyncio.create_task(
                    asyncio.to_thread(self.transfers.upload, args, session_id)
                    if tool == "upload"
                    else asyncio.to_thread(self.transfers.download, args)
                )
                try:
                    return await asyncio.shield(work)
                except asyncio.CancelledError:
                    await work
                    raise
        if tool == "screenshot":
            image = await asyncio.to_thread(monitor.screenshot, args)
            if len(image) > self.transfers.max_size:
                raise ProtocolError("Screenshot size limit exceeded")
            filename = "screenshot-" + str(time.time_ns()) + ".png"
            destination = root / filename
            await asyncio.to_thread(destination.write_bytes, image)
            return {
                "ok": True,
                "path": filename,
                "size": len(image),
                "sha256": await asyncio.to_thread(sha256_file, destination),
            }
        if tool in {"registry", "services", "tasks"}:
            return await admin_tools.admin(tool, args, root, emit, timeout)
        raise ProtocolError("Unknown tool")

    async def command(self, socket: aiohttp.ClientWebSocketResponse, message: dict) -> None:
        envelope = {key: message[key] for key in ("session_id", "target_node_id", "command_id")}

        async def emit(stream: str, data: str) -> None:
            await self.send(socket, {"type": "output", **envelope, "stream": stream, "data": data})

        logger.info(
            "Command %s tool=%s session=%s",
            message["command_id"],
            message["tool"],
            message["session_id"],
        )
        self.notify("command", {"command_id": message["command_id"], "tool": message["tool"]})
        try:
            timeout = message["timeout"]
            async with asyncio.timeout(timeout):
                result = await self.invoke(
                    message["tool"], message["args"], message["session_id"], emit, timeout
                )
        except asyncio.CancelledError:
            return
        except TimeoutError:
            result = {"ok": False, "error": "Agent command timeout"}
        except (ProtocolError, OSError, ValueError, KeyError, psutil.Error) as exc:
            result = {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("Tool failed")
            result = {"ok": False, "error": "Agent tool failed"}
        finally:
            self.jobs.pop(message["command_id"], None)
        with contextlib.suppress(ConnectionError, TimeoutError):
            await self.send(socket, {"type": "result", **envelope, "result": result})

    async def handle(self, socket: aiohttp.ClientWebSocketResponse, message: dict) -> None:
        if message.get("type") == "heartbeat":
            self.ping_ms = round(max(0, time.monotonic() - self.heartbeat_sent) * 1000, 2)
            return
        if message.get("type") == "authenticated":
            return
        if message.get("target_node_id") != self.settings.id:
            raise ProtocolError("Incorrect target node")
        command_id = identifier(message.get("command_id"), "command_id")
        session_id = identifier(message.get("session_id"), "session_id")
        if message.get("type") == "cancel":
            job = self.jobs.get(command_id)
            if job and job[0] == session_id:
                job[1].cancel()
            return
        if message.get("type") != "command":
            raise ProtocolError("Unexpected server message")
        self.seen = {key: until for key, until in self.seen.items() if until > time.monotonic()}
        if command_id in self.seen:
            raise ProtocolError("Duplicate command")
        if len(self.jobs) >= 8 or len(self.seen) >= 10000:
            raise ProtocolError("Agent capacity reached")
        if message.get("tool") not in TOOLS or not isinstance(message.get("args"), dict):
            raise ProtocolError("Invalid tool arguments")
        timeout = message.get("timeout")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not 1 <= timeout <= 120
        ):
            raise ProtocolError("Invalid timeout")
        self.seen[command_id] = time.monotonic() + 600
        self.jobs[command_id] = (session_id, asyncio.create_task(self.command(socket, message)))

    async def heartbeat(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            self.heartbeat_sent = time.monotonic()
            await self.send(socket, {"type": "heartbeat", "metadata": await self.node_metadata()})
            await asyncio.sleep(10)

    async def node_metadata(self) -> dict:
        value = await asyncio.to_thread(monitor.metadata, self.settings.name)
        value["tools"] = sorted(self.settings.tools)
        value["telemetry"]["ping_ms"] = self.ping_ms
        return value

    async def connect(self, http: aiohttp.ClientSession) -> None:
        tls = client_tls(self.settings.url, self.settings.allow_local_http, self.settings.ca_file)
        async with http.ws_connect(
            ws_url(self.settings.url, "/ws/agent"),
            ssl=tls,
            heartbeat=15,
            max_msg_size=MAX_MESSAGE,
            compress=0,
        ) as connection:
            challenge = await connection.receive_json(timeout=30)
            message = (
                signed_auth(
                    self.settings.private_key, challenge, self.settings.principal, self.settings.id
                )
                if self.settings.private_key
                else auth_message(
                    self.settings.secret,
                    challenge,
                    "agent",
                    self.settings.principal,
                    self.settings.id,
                )
            )
            message["metadata"] = await self.node_metadata()
            await connection.send_json(message)
            response = await connection.receive_json(timeout=30)
            if response.get("type") == "approval_required":
                self.notify("waiting", response)
                response = await connection.receive_json(timeout=630)
            if response.get("type") == "approval_denied":
                raise ApprovalDenied(response.get("error", "Connection rejected"))
            if response.get("type") != "authenticated":
                raise ProtocolError("Agent authentication rejected")
            logger.info(
                "Connected node=%s name=%s tools=%s",
                self.settings.id,
                self.settings.name,
                ",".join(sorted(self.settings.tools)),
            )
            self.notify("connected", {"node_id": self.settings.id})
            pulse = asyncio.create_task(self.heartbeat(connection))
            try:
                async for frame in connection:
                    if frame.type == aiohttp.WSMsgType.TEXT:
                        value = json.loads(frame.data)
                        if not isinstance(value, dict):
                            raise ProtocolError("Invalid server message")
                        await self.handle(connection, value)
                    elif frame.type == aiohttp.WSMsgType.ERROR:
                        raise ProtocolError("WebSocket connection failed")
            finally:
                self.notify("disconnected", {})
                pulse.cancel()
                tasks = [job[1] for job in self.jobs.values()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(pulse, *tasks, return_exceptions=True)
                self.jobs.clear()
                self.transfers.close()
            if connection.close_code == 4001:
                raise ApprovalDenied("Connection replaced by another explicitly approved instance")

    async def run(self) -> None:
        delay = 1.0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as http:
            while True:
                started = time.monotonic()
                try:
                    await self.connect(http)
                except ApprovalDenied as exc:
                    self.notify("denied", {"error": str(exc)})
                    return
                except (
                    aiohttp.ClientError,
                    ProtocolError,
                    OSError,
                    ValueError,
                    TimeoutError,
                ) as exc:
                    logger.warning("Relay connection unavailable; retrying")
                    self.notify("retry", {"error": str(exc)})
                if time.monotonic() - started > 60:
                    delay = 1
                await asyncio.sleep(delay + random.uniform(0, delay / 2))
                delay = min(60, delay * 2)


def display_approval(event: str, detail: dict) -> None:
    if event == "waiting":
        if sys.stdout.isatty():
            print(
                f"Enter this connection's eight-digit code in Controller: {detail['code']}",
                flush=True,
            )
        else:
            logger.warning("Manual approval required; use an interactive agent to read its code")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kintler agent for explicitly enrolled devices")
    parser.add_argument("--env", default=".env", help="Local environment configuration")
    parser.add_argument("--print-id", action="store_true", help="Print NodeID without connecting")
    args = parser.parse_args()
    if args.print_id:
        print(node_id())
        return
    load_dotenv(args.env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(
            Agent(
                AgentSettings.from_env(Path(args.env).resolve().parent),
                observer=display_approval,
            ).run()
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
