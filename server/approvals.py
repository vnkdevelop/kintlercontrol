import asyncio
import hmac
import secrets
import time
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web

from common.identity import fingerprint
from common.protocol import ProtocolError
from server.auth import Auth, Session
from server.config import Settings
from server.registry import Registry
from server.storage import Storage


@dataclass
class Request:
    id: str
    session: Session
    metadata: dict
    peer: str
    socket: web.WebSocketResponse
    expires: float
    code: str = field(repr=False)
    known: bool = False
    accepted: bool = False
    decided: bool = False
    failures: int = 0
    event: asyncio.Event = field(default_factory=asyncio.Event)
    deciding: asyncio.Lock = field(default_factory=asyncio.Lock)

    def view(self) -> dict:
        return {
            "request_id": self.id,
            "session_id": self.session.id,
            "node_id": self.session.credential.node_id,
            "name": self.metadata["name"],
            "platform": self.metadata["platform"],
            "admin": self.metadata["admin"],
            "tools": self.metadata["tools"],
            "peer": self.peer,
            "expires": self.expires,
            "known": self.known,
            "code_required": True,
            "fingerprint": fingerprint(self.session.credential.public_key)
            if self.session.credential.public_key
            else "Legacy HMAC identity",
        }


class Approvals:
    def __init__(self, settings: Settings, auth: Auth, registry: Registry, storage: Storage):
        self.settings, self.auth, self.registry, self.storage = settings, auth, registry, storage
        self.requests: dict[str, Request] = {}

    def permitted(self, controller: Session, node: str) -> bool:
        return (
            controller.credential.role == "controller"
            and controller.credential.can_approve
            and controller.credential.permits(node)
            and controller.expires > time.time()
        )

    def inventory(self, controller: Session) -> list[dict]:
        return [
            request.view()
            for request in self.requests.values()
            if not request.decided
            and request.expires > time.time()
            and not request.socket.closed
            and self.permitted(controller, request.session.credential.node_id)
        ]

    async def create(
        self, session: Session, metadata: object, peer: str, socket: web.WebSocketResponse
    ) -> Request:
        node = session.credential.node_id
        if (
            len(self.requests) >= self.settings.max_pending
            or sum(r.peer == peer for r in self.requests.values()) >= 4
            or any(r.session.credential.node_id == node for r in self.requests.values())
        ):
            raise ProtocolError(
                "Connection approval capacity reached or this device is already waiting"
            )
        existing = self.auth.credentials.get(session.credential.principal)
        known = (
            existing is not None
            and existing.public_key == session.credential.public_key
            and existing.node_id == node
        )
        request = Request(
            "req_" + secrets.token_hex(16),
            session,
            self.registry.metadata(metadata),
            peer,
            socket,
            time.time() + self.settings.approval_timeout,
            f"{secrets.randbelow(100000000):08d}",
            known,
        )
        self.requests[request.id] = request
        try:
            await self.storage.audit(
                "connection.request",
                session.credential.principal,
                session.id,
                node,
                detail={"request_id": request.id, "known": known, "peer": peer},
            )
        except BaseException:
            self.requests.pop(request.id, None)
            raise
        return request

    async def decide(
        self, controller: Session, request_id: str, approve: object, code: object = ""
    ) -> None:
        request = self.requests.get(request_id)
        if request is None or not self.permitted(controller, request.session.credential.node_id):
            raise ProtocolError("Approval request not found or access denied")
        if not isinstance(approve, bool):
            raise ProtocolError("approve must be a boolean")
        async with request.deciding:
            if request.decided or request.socket.closed or request.expires <= time.time():
                raise ProtocolError("This connection approval is no longer valid")
            if approve and not (
                isinstance(code, str)
                and len(code) == 8
                and code.isascii()
                and code.isdigit()
                and hmac.compare_digest(code, request.code)
            ):
                request.failures += 1
                if request.failures >= 5:
                    request.decided = True
                    request.event.set()
                raise ProtocolError("Pairing code does not match the code displayed by the agent")
            await self.storage.audit(
                "connection.approve" if approve else "connection.deny",
                controller.credential.principal,
                request.session.id,
                request.session.credential.node_id,
                detail={"request_id": request.id, "controller_session": controller.id},
            )
            if (
                request.socket.closed
                or request.expires <= time.time()
                or controller.expires <= time.time()
            ):
                raise ProtocolError("Connection or approver expired during approval")
            request.accepted = approve
            request.decided = True
            request.event.set()

    async def wait(self, request: Request) -> bool:
        receive = asyncio.create_task(request.socket.receive())
        decision = asyncio.create_task(request.event.wait())
        try:
            done, _ = await asyncio.wait(
                {receive, decision},
                timeout=max(0, request.expires - time.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive in done:
                frame = receive.result()
                if frame.type == WSMsgType.TEXT:
                    raise ProtocolError("Device must wait for connection approval")
                return False
            return (
                decision in done
                and request.accepted
                and not request.socket.closed
                and request.expires > time.time()
            )
        finally:
            for task in (receive, decision):
                task.cancel()
            await asyncio.gather(receive, decision, return_exceptions=True)

    def remove(self, request: Request) -> None:
        self.requests.pop(request.id, None)
