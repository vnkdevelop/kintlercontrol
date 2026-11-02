import asyncio
import base64
import contextlib
import hashlib
import os
import secrets
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from agent.modules.fs_transfer import sha256_file
from common.identity import signed_auth, unlock
from common.protocol import CHUNK_SIZE, MAX_MESSAGE, ProtocolError, auth_message, client_tls, ws_url


@dataclass(frozen=True)
class ClientSettings:
    url: str
    principal: str
    secret: str = field(repr=False)
    allow_local_http: bool = False
    ca_file: str | None = None
    private_key: bytes = field(default=b"", repr=False)
    origin: str = "controller"

    @classmethod
    def from_env(cls) -> "ClientSettings":
        secret = unlock(os.environ.get("KINTLER_CONTROLLER_SECRET", ""), "controller").decode()
        private = (
            unlock(os.environ["KINTLER_CONTROLLER_PRIVATE_KEY"], "controller")
            if os.environ.get("KINTLER_CONTROLLER_PRIVATE_KEY")
            else b""
        )
        if not private and len(secret) < 32:
            raise ValueError(
                "Run kintler login or set KINTLER_CONTROLLER_SECRET in your local .env"
            )
        return cls(
            os.environ["KINTLER_URL"].rstrip("/"),
            os.environ["KINTLER_CONTROLLER_ID"],
            secret,
            os.environ.get("KINTLER_ALLOW_LOCAL_HTTP") == "1",
            os.environ.get("KINTLER_CA_FILE") or None,
            private,
        )


class KintlerClient:
    def __init__(self, settings: ClientSettings):
        self.settings = settings
        self.tls = client_tls(settings.url, settings.allow_local_http, settings.ca_file)
        self.http: aiohttp.ClientSession | None = None
        self.token = ""
        self.session_id = ""
        self.expires = 0.0
        self.login_lock = asyncio.Lock()
        self._known_node_ids: set[str] = set()
        self._nodes_checked_at = 0.0
        self._inventory_session = ""

    async def __aenter__(self) -> "KintlerClient":
        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=40), raise_for_status=False
        )
        try:
            await self.login()
        except BaseException:
            await self.http.close()
            self.http = None
            raise
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self.http:
            if self.token:
                with contextlib.suppress(aiohttp.ClientError, ProtocolError, TimeoutError):
                    await self.request("POST", "/api/auth/logout", auth=True, renew=False)
            await self.http.close()
            self.http = None
            self.token = ""
        self._known_node_ids.clear()
        self._nodes_checked_at = 0.0
        self._inventory_session = ""

    async def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        auth: bool = True,
        renew: bool = True,
    ) -> dict:
        if self.http is None:
            raise RuntimeError("Use async with KintlerClient(...)")
        if auth and renew and time.time() >= self.expires - 5:
            async with self.login_lock:
                if time.time() >= self.expires - 5:
                    await self.login()
        headers = {"Authorization": "Bearer " + self.token} if auth else {}
        async with self.http.request(
            method, self.settings.url + path, json=payload, headers=headers, ssl=self.tls
        ) as response:
            try:
                value = await response.json()
            except (ValueError, aiohttp.ContentTypeError):
                raise ProtocolError(f"Relay returned HTTP {response.status} without JSON") from None
            if response.status >= 400:
                raise ProtocolError(value.get("error", f"HTTP {response.status}"))
            return value

    async def login(self) -> None:
        self._known_node_ids.clear()
        self._nodes_checked_at = 0.0
        self._inventory_session = ""
        challenge = await self.request("POST", "/api/auth/challenge", auth=False)
        reply = await self.request(
            "POST",
            "/api/auth/login",
            signed_auth(
                self.settings.private_key, challenge, self.settings.principal, "", "controller"
            )
            if self.settings.private_key
            else auth_message(
                self.settings.secret, challenge, "controller", self.settings.principal
            ),
            auth=False,
        )
        self.token, self.session_id, self.expires = (
            reply["token"],
            reply["session_id"],
            reply["expires"],
        )

    async def nodes(self) -> list[dict]:
        nodes = (await self.request("GET", "/api/nodes"))["nodes"]
        ids = Counter(
            entry["id"]
            for entry in nodes
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]
        )
        names: dict[str, set[str]] = {}
        for entry in nodes:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and isinstance(entry.get("name"), str)
            ):
                names.setdefault(entry["name"].casefold(), set()).add(entry["id"])
        self._known_node_ids = {
            node_id
            for node_id, count in ids.items()
            if count == 1 and names.get(node_id.casefold(), set()) <= {node_id}
        }
        self._nodes_checked_at = time.monotonic()
        self._inventory_session = self.session_id
        return nodes

    async def tools(self) -> dict:
        return await self.request("GET", "/api/tools")

    async def approvals(self) -> list[dict]:
        return (await self.request("GET", "/api/approvals"))["requests"]

    async def approve(self, request_id: str, allow: bool, code: str = "") -> dict:
        return await self.request(
            "POST", f"/api/approvals/{request_id}", {"approve": allow, "code": code}
        )

    async def resolve(self, node: str) -> str:
        if (
            node in self._known_node_ids
            and self._inventory_session == self.session_id
            and time.monotonic() - self._nodes_checked_at < 3
        ):
            return node
        matches = [
            n
            for n in await self.nodes()
            if n["id"] == node or n.get("name", "").casefold() == node.casefold()
        ]
        if len(matches) != 1:
            raise ProtocolError("Node not found or name ambiguous")
        return matches[0]["id"]

    async def submit(
        self, node: str, tool: str, args: dict | None = None, timeout: float = 120
    ) -> str:
        node_id = await self.resolve(node)
        reply = await self.request(
            "POST",
            f"/api/nodes/{node_id}/cmd",
            {"tool": tool, "args": args or {}, "timeout": timeout, "origin": self.settings.origin},
        )
        return reply["command_id"]

    async def cancel(self, command_id: str) -> dict:
        return await self.request("POST", f"/api/commands/{command_id}/cancel", renew=False)

    async def events(self, command_id: str) -> AsyncIterator[dict]:
        cursor = 0
        while True:
            reply = await self.request(
                "GET", f"/api/commands/{command_id}?after={cursor}&wait=20", renew=False
            )
            if reply["first_seq"] > cursor + 1:
                raise ProtocolError("Output cursor expired; consumer was too slow")
            for event in reply["events"]:
                cursor = event["seq"]
                yield event
            if reply["finished"]:
                return

    async def call(
        self,
        node: str,
        tool: str,
        args: dict | None = None,
        timeout: float = 120,
        on_output: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> dict:
        command_id = await self.submit(node, tool, args, timeout)
        output: dict[str, list[str]] = {"stdout": [], "stderr": []}
        try:
            async with asyncio.timeout(timeout + 30):
                async for event in self.events(command_id):
                    if event["kind"] == "output":
                        output[event["stream"]].append(event["data"])
                        if on_output:
                            await on_output(event["stream"], event["data"])
                    elif event["kind"] == "result":
                        return {
                            **event["result"],
                            "command_id": command_id,
                            "stdout": "".join(output["stdout"]),
                            "stderr": "".join(output["stderr"]),
                        }
        finally:
            with contextlib.suppress(ProtocolError, aiohttp.ClientError, TimeoutError):
                await self.cancel(command_id)
        raise ProtocolError("Command completed without result")

    async def checked(self, node: str, tool: str, args: dict) -> dict:
        result = await self.call(node, tool, args)
        if not result["ok"]:
            raise ProtocolError(result.get("error", "Remote operation failed"))
        return result

    async def upload(
        self, node: str, source: str | Path, destination: str, overwrite: bool = False
    ) -> dict:
        path = Path(source)
        digest = await asyncio.to_thread(sha256_file, path)
        started = await self.checked(
            node,
            "upload",
            {
                "operation": "start",
                "path": destination,
                "size": path.stat().st_size,
                "sha256": digest,
            },
        )
        upload_id = started["upload_id"]
        try:
            offset = 0
            with path.open("rb") as handle:
                while data := handle.read(CHUNK_SIZE):
                    reply = await self.checked(
                        node,
                        "upload",
                        {
                            "operation": "chunk",
                            "upload_id": upload_id,
                            "offset": offset,
                            "data": base64.b64encode(data).decode(),
                        },
                    )
                    offset = reply["offset"]
            return await self.checked(
                node,
                "upload",
                {"operation": "finish", "upload_id": upload_id, "overwrite": overwrite},
            )
        except BaseException:
            with contextlib.suppress(ProtocolError, aiohttp.ClientError, TimeoutError):
                await self.checked(node, "upload", {"operation": "abort", "upload_id": upload_id})
            raise

    async def download(
        self, node: str, source: str, destination: str | Path, overwrite: bool = False
    ) -> dict:
        target = Path(destination).resolve()
        if target.exists() and not overwrite:
            raise ProtocolError("Local destination exists; explicit overwrite is required")
        target.parent.mkdir(parents=True, exist_ok=True)
        info = await self.checked(node, "download", {"operation": "stat", "path": source})
        temporary = target.with_name(".kintler-" + secrets.token_hex(16) + ".part")
        digest = hashlib.sha256()
        offset = 0
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                while offset < info["size"]:
                    reply = await self.checked(
                        node, "download", {"operation": "chunk", "path": source, "offset": offset}
                    )
                    data = base64.b64decode(reply["data"], validate=True)
                    if (
                        not data
                        or reply["offset"] != offset
                        or len(data) > CHUNK_SIZE
                        or offset + len(data) > info["size"]
                    ):
                        raise ProtocolError("Invalid download chunk")
                    digest.update(data)
                    handle.write(data)
                    offset += len(data)
                handle.flush()
                os.fsync(handle.fileno())
            if digest.hexdigest() != info["sha256"]:
                raise ProtocolError("Downloaded SHA-256 mismatch; source may have changed")
            if target.exists() and not overwrite:
                raise ProtocolError("Local destination was created during download")
            if overwrite:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
            return {"ok": True, "path": str(target), "size": offset, "sha256": digest.hexdigest()}
        finally:
            temporary.unlink(missing_ok=True)


class LiveController:
    def __init__(self, settings: ClientSettings, on_event: Callable[[dict], Awaitable[None]]):
        self.settings = settings
        self.on_event = on_event
        self.http: aiohttp.ClientSession | None = None
        self.socket: aiohttp.ClientWebSocketResponse | None = None
        self.reader: asyncio.Task | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.session_id = ""

    async def __aenter__(self) -> "LiveController":
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        try:
            self.socket = await self.http.ws_connect(
                ws_url(self.settings.url, "/ws/controller"),
                ssl=client_tls(
                    self.settings.url, self.settings.allow_local_http, self.settings.ca_file
                ),
                heartbeat=15,
                max_msg_size=MAX_MESSAGE,
            )
            challenge = await self.socket.receive_json(timeout=30)
            await self.socket.send_json(
                signed_auth(
                    self.settings.private_key, challenge, self.settings.principal, "", "controller"
                )
                if self.settings.private_key
                else auth_message(
                    self.settings.secret, challenge, "controller", self.settings.principal
                )
            )
            reply = await self.socket.receive_json(timeout=30)
            if reply.get("type") != "authenticated":
                raise ProtocolError("Controller authentication rejected")
            self.session_id = reply["session_id"]
            self.reader = asyncio.create_task(self.receive())
        except BaseException:
            await self.http.close()
            raise
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self.socket:
            await self.socket.close()
        if self.reader:
            await self.reader
        if self.http:
            await self.http.close()

    async def receive(self) -> None:
        assert self.socket is not None
        try:
            async for message in self.socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                value = message.json()
                if value.get("type") == "reply":
                    future = self.pending.get(value.get("request_id"))
                    if future and not future.done():
                        if "error" in value:
                            future.set_exception(ProtocolError(value["error"]))
                        else:
                            future.set_result(value)
                elif value.get("type") == "event":
                    await self.on_event(value)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ProtocolError("Controller connection closed"))

    async def request(self, kind: str, **payload: Any) -> dict:
        if self.socket is None or self.socket.closed:
            raise ProtocolError("Controller is not connected")
        request_id = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.socket.send_json({"type": kind, "request_id": request_id, **payload})
            return await asyncio.wait_for(future, 15)
        finally:
            self.pending.pop(request_id, None)
