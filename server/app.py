import asyncio
import contextlib
import ipaddress
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from aiohttp import WSMsgType, web
from dotenv import load_dotenv

from common.protocol import MAX_MESSAGE, TOOLS, ProtocolError
from server.approvals import Approvals
from server.approvals import Request as ApprovalRequest
from server.auth import Auth, Session
from server.config import Credential, Settings
from server.registry import Node, Registry
from server.router import Router
from server.storage import Storage

logger = logging.getLogger("kintler.server")


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.auth = Auth(settings)
        self.storage = Storage(settings.database)
        self.registry = Registry(self.storage)
        self.router = Router(settings, self.auth, self.registry, self.storage)
        self.approvals = Approvals(settings, self.auth, self.registry, self.storage)
        self.connections: set[web.WebSocketResponse] = set()
        self.pending_connections = 0
        self.maintenance_task: asyncio.Task | None = None
        self.maintenance_healthy = True
        self.maintenance_last_tick = time.monotonic()
        self.failed_routes: set[str] = set()

    def peer(self, request: web.Request) -> str:
        address = request.remote or "unknown"
        try:
            trusted = any(
                ipaddress.ip_address(address) in network
                for network in self.settings.trusted_proxies
            )
            if trusted and request.headers.get("X-Real-IP"):
                return str(ipaddress.ip_address(request.headers["X-Real-IP"]))
        except ValueError:
            pass
        return address

    def secure(self, request: web.Request) -> bool:
        if request.secure:
            return True
        try:
            peer = ipaddress.ip_address(request.remote or "")
        except ValueError:
            return False
        if (
            any(peer in network for network in self.settings.trusted_proxies)
            and request.headers.get("X-Forwarded-Proto") == "https"
        ):
            return True
        return self.settings.allow_local_http and peer.is_loopback

    def controller(self, request: web.Request) -> Session:
        session = self.auth.bearer(request.headers.get("Authorization", ""))
        if session.credential.role != "controller":
            raise ProtocolError("Controller access required")
        return session

    async def lifecycle(self, app: web.Application) -> AsyncIterator[None]:
        await self.registry.load()
        for principal, node, public in await self.storage.load_agent_keys():
            existing = self.auth.credentials.get(principal)
            if existing and (existing.role != "agent" or existing.node_id != node):
                raise ValueError("Persistent agent identity conflicts with configured credentials")
            self.auth.credentials[principal] = Credential(
                principal, "", "agent", node, public_key=public
            )
        task = asyncio.create_task(self.maintenance())
        self.maintenance_task = task
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for socket in list(self.connections):
            await socket.close(code=1001, message=b"Server shutdown")
        for command in list(self.router.commands.values()):
            await self.router.cancel(command, "Server shutdown")
        await self.storage.close()

    async def maintenance(self) -> None:
        while True:
            await asyncio.sleep(2)
            self.auth.prune()
            try:
                await self.router.tick()
            except Exception:
                self.maintenance_healthy = False
                logger.exception("Command maintenance failed; check audit storage")
            else:
                self.maintenance_healthy = True
            for node in list(self.registry.nodes.values()):
                if node.socket and (
                    time.time() - node.last_seen > self.settings.heartbeat_timeout
                    or node.session_id not in self.auth.sessions
                ):
                    await node.socket.close(code=1008, message=b"Heartbeat or session expired")
            for session_id, socket in list(self.router.controllers.items()):
                if session_id not in self.auth.sessions:
                    await socket.close(code=1008, message=b"Session expired")
            self.maintenance_last_tick = time.monotonic()

    async def health(self, request: web.Request) -> web.Response:
        healthy = (
            self.maintenance_task is not None
            and not self.maintenance_task.done()
            and self.maintenance_healthy
            and time.monotonic() - self.maintenance_last_tick <= 10
            and not self.failed_routes
        )
        try:
            async with asyncio.timeout(2):
                await self.storage.health()
        except Exception:
            healthy = False
        return web.json_response(
            {"status": "ok" if healthy else "unhealthy", "version": "1.5.2"},
            status=200 if healthy else 503,
        )

    async def challenge(self, request: web.Request) -> web.Response:
        return web.json_response(self.auth.challenge(self.peer(request)))

    async def login(self, request: web.Request) -> web.Response:
        session = self.auth.authenticate(await body(request), self.peer(request), "controller")
        await self.storage.audit("auth.login", session.credential.principal, session.id)
        return web.json_response(
            {
                "token": self.auth.issue_token(session),
                "session_id": session.id,
                "expires": session.expires,
                "can_approve": session.credential.can_approve,
            }
        )

    async def logout(self, request: web.Request) -> web.Response:
        session = self.controller(request)
        await self.router.disconnect_controller(session)
        return web.json_response({"ok": True})

    async def nodes(self, request: web.Request) -> web.Response:
        return web.json_response({"nodes": self.registry.inventory(self.controller(request))})

    async def tools(self, request: web.Request) -> web.Response:
        session = self.controller(request)
        return web.json_response(
            {
                "tools": sorted(session.credential.tools & TOOLS),
                "can_approve": session.credential.can_approve,
                "nodes": sorted(session.credential.nodes),
                "session_id": session.id,
                "expires": session.expires,
            }
        )

    async def submit(self, request: web.Request) -> web.Response:
        session = self.controller(request)
        value = await body(request)
        command = await self.router.submit(
            session,
            request.match_info["node_id"],
            value.get("tool"),
            value.get("args", {}),
            value.get("timeout"),
            value.get("origin", "controller"),
        )
        return web.json_response(command.view(), status=202)

    async def approval_list(self, request: web.Request) -> web.Response:
        return web.json_response({"requests": self.approvals.inventory(self.controller(request))})

    async def approval_decide(self, request: web.Request) -> web.Response:
        value = await body(request)
        await self.approvals.decide(
            self.controller(request),
            request.match_info["request_id"],
            value.get("approve"),
            value.get("code", ""),
        )
        return web.json_response({"ok": True})

    async def command(self, request: web.Request) -> web.Response:
        session = self.controller(request)
        command = self.router.owned(session, request.match_info["command_id"])
        try:
            after = int(request.query.get("after", "0"))
            wait = min(20, max(0, float(request.query.get("wait", "0"))))
            if after < 0:
                raise ValueError
        except ValueError:
            raise ProtocolError("Invalid event cursor") from None
        if wait and not command.finished and command.next_sequence - 1 <= after:
            command.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(command.changed.wait(), wait)
        return web.json_response(command.view(after))

    async def cancel(self, request: web.Request) -> web.Response:
        command = self.router.owned(self.controller(request), request.match_info["command_id"])
        await self.router.cancel(command)
        return web.json_response(command.view())

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        role = request.match_info["role"]
        if role not in {"controller", "agent"}:
            raise web.HTTPNotFound()
        if len(self.connections) + self.pending_connections >= self.settings.max_connections:
            raise web.HTTPServiceUnavailable()
        self.pending_connections += 1
        socket = web.WebSocketResponse(heartbeat=15, max_msg_size=MAX_MESSAGE, compress=False)
        pending = True
        session: Session | None = None
        node: Node | None = None
        approval: ApprovalRequest | None = None
        try:
            challenge = self.auth.challenge(self.peer(request))
            await socket.prepare(request)
            self.connections.add(socket)
            self.pending_connections -= 1
            pending = False
            await socket.send_json(challenge)
            value = await socket.receive_json(timeout=30)
            if not isinstance(value, dict):
                raise ProtocolError("Invalid authentication message")
            session = self.auth.authenticate(value, self.peer(request), role)
            if role == "agent":
                approval = await self.approvals.create(
                    session, value.get("metadata"), self.peer(request), socket
                )
                await socket.send_json(
                    {
                        "type": "approval_required",
                        "request_id": approval.id,
                        "node_id": session.credential.node_id,
                        "code": approval.code,
                        "expires": approval.expires,
                    }
                )
                if not await self.approvals.wait(approval):
                    if not socket.closed:
                        await socket.send_json(
                            {
                                "type": "approval_denied",
                                "request_id": approval.id,
                                "error": "Connection was rejected, cancelled or its approval expired",
                            }
                        )
                    return socket
                previous = self.registry.nodes.get(session.credential.node_id)
                if previous and previous.socket and not previous.socket.closed:
                    await previous.socket.close(
                        code=4001, message=b"Replaced by explicit owner approval"
                    )
                if session.credential.public_key:
                    await self.storage.save_agent_key(
                        session.credential.principal,
                        session.credential.node_id,
                        session.credential.public_key,
                    )
                    self.auth.credentials[session.credential.principal] = session.credential
                session.approved = True
                session.expires = time.time() + self.settings.session_ttl
                node = await self.registry.connect(session, socket, value.get("metadata"))
            else:
                self.router.controllers[session.id] = socket
            await self.storage.audit(
                "ws.connect", session.credential.principal, session.id, session.credential.node_id
            )
            await socket.send_json(
                {"type": "authenticated", "session_id": session.id, "expires": session.expires}
            )
            if node:
                node.ready = True
            if approval:
                self.approvals.remove(approval)
                approval = None
            async for frame in socket:
                if frame.type != WSMsgType.TEXT:
                    if frame.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                        break
                    continue
                value = json.loads(frame.data)
                if not isinstance(value, dict):
                    raise ProtocolError("Message must be an object")
                if session.id not in self.auth.sessions or session.expires <= time.time():
                    raise ProtocolError("Session expired")
                if node:
                    if value.get("type") == "heartbeat":
                        await self.registry.heartbeat(node, value.get("metadata"))
                        await socket.send_json({"type": "heartbeat", "at": time.time()})
                    else:
                        await self.router.accept(node, value)
                else:
                    await self.control(socket, session, value)
        except (ProtocolError, ValueError, TypeError, TimeoutError, ConnectionError) as exc:
            await self.storage.audit(
                "ws.reject",
                session.credential.principal if session else "",
                session.id if session else "",
                detail={"peer": self.peer(request)},
            )
            if socket.prepared and not socket.closed:
                with contextlib.suppress(ConnectionError):
                    await socket.send_json(
                        {
                            "type": "error",
                            "error": str(exc)
                            if isinstance(exc, ProtocolError)
                            else "Invalid message or connection timeout",
                        }
                    )
            if not socket.prepared:
                raise ProtocolError("WebSocket authentication unavailable") from None
        finally:
            if approval:
                self.approvals.remove(approval)
            if pending:
                self.pending_connections -= 1
            self.connections.discard(socket)
            if node:
                await self.router.disconnect_node(node)
                await self.registry.disconnect(node)
            if session:
                if role == "controller":
                    await self.router.disconnect_controller(session)
                else:
                    self.auth.revoke(session.id)
            if socket.prepared:
                await socket.close()
        return socket

    async def control(self, socket: web.WebSocketResponse, session: Session, value: dict) -> None:
        request_id = value.get("request_id")
        if not isinstance(request_id, str) or len(request_id) > 80:
            raise ProtocolError("Invalid request_id")
        try:
            kind = value.get("type")
            payload: dict[str, Any]
            if kind == "nodes":
                payload = {"nodes": self.registry.inventory(session)}
            elif kind == "use":
                name = value.get("node")
                if not isinstance(name, str):
                    raise ProtocolError("Invalid node")
                payload = {"node": await self.router.switch(session, name)}
            elif kind == "command":
                if session.target is None:
                    raise ProtocolError("Select a node first")
                command = await self.router.submit(
                    session,
                    session.target,
                    value.get("tool"),
                    value.get("args", {}),
                    value.get("timeout"),
                    value.get("origin", "controller"),
                )
                payload = {"command_id": command.id}
            elif kind == "cancel":
                command = self.router.owned(session, value.get("command_id", ""))
                await self.router.cancel(command)
                payload = {"ok": True}
            else:
                raise ProtocolError("Unknown controller request")
            await socket.send_json({"type": "reply", "request_id": request_id, **payload})
        except ProtocolError as exc:
            await socket.send_json({"type": "reply", "request_id": request_id, "error": str(exc)})


async def body(request: web.Request) -> dict:
    try:
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ProtocolError("JSON object required") from None


SERVICE = web.AppKey("service", Service)


@web.middleware
async def security(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    service = request.app[SERVICE]
    if request.path != "/healthz" and not service.secure(request):
        return web.json_response({"error": "TLS required"}, status=403)
    try:
        response = await handler(request)
    except ProtocolError as exc:
        await service.storage.audit(
            "request.reject", detail={"path": request.path, "peer": service.peer(request)}
        )
        status = (
            401
            if request.path.startswith("/api/auth")
            or "Authentication" in str(exc)
            or "Session expired" in str(exc)
            else 400
        )
        response = web.json_response({"error": str(exc)}, status=status)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Request failed: %s", request.path)
        response = web.json_response({"error": "Internal server error"}, status=500)
    if request.path != "/healthz":
        route = getattr(request.match_info.route.resource, "canonical", request.path)
        if response.status >= 500:
            service.failed_routes.add(route)
        elif response.status < 400:
            service.failed_routes.discard(route)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def create_app(settings: Settings | None = None) -> web.Application:
    service = Service(settings or Settings.from_env())
    app = web.Application(middlewares=[security], client_max_size=MAX_MESSAGE)
    app[SERVICE] = service
    app.cleanup_ctx.append(service.lifecycle)
    app.add_routes(
        [
            web.get("/healthz", service.health),
            web.post("/api/auth/challenge", service.challenge),
            web.post("/api/auth/login", service.login),
            web.post("/api/auth/logout", service.logout),
            web.get("/api/nodes", service.nodes),
            web.get("/api/tools", service.tools),
            web.get("/api/approvals", service.approval_list),
            web.post("/api/approvals/{request_id}", service.approval_decide),
            web.post("/api/nodes/{node_id}/cmd", service.submit),
            web.get("/api/commands/{command_id}", service.command),
            web.post("/api/commands/{command_id}/cancel", service.cancel),
            web.get("/ws/{role}", service.websocket),
        ]
    )
    return app


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    web.run_app(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=None,
        shutdown_timeout=15,
    )


if __name__ == "__main__":
    main()
