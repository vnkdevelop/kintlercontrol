import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

from common.identity import decode, verify_auth
from common.protocol import PROTOCOL_VERSION, ProtocolError, identifier, proof
from server.config import Credential, Settings


@dataclass
class Session:
    id: str
    credential: Credential
    expires: float
    target: str | None = None
    generation: int = 0
    token_hash: str = field(default="", repr=False)
    approved: bool = True


class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.credentials = dict(settings.credentials)
        self.challenges: dict[str, tuple[dict, str, float]] = {}
        self.failures: dict[str, tuple[int, float]] = {}
        self.sessions: dict[str, Session] = {}
        self.tokens: dict[str, str] = {}

    def prune(self) -> None:
        now = time.time()
        self.challenges = {k: v for k, v in self.challenges.items() if v[2] > now}
        self.failures = {k: v for k, v in self.failures.items() if v[1] > now}
        for session in list(self.sessions.values()):
            if session.expires <= now:
                self.revoke(session.id)

    def blocked(self, ip: str) -> bool:
        self.prune()
        count, until = self.failures.get(ip, (0, 0))
        return count >= 5 and until > time.time()

    def fail(self, ip: str) -> None:
        if ip not in self.failures and len(self.failures) >= 4096:
            raise ProtocolError("Authentication capacity reached")
        count, _ = self.failures.get(ip, (0, 0))
        self.failures[ip] = (count + 1, time.time() + 900)

    def challenge(self, ip: str) -> dict:
        if self.blocked(ip):
            raise ProtocolError("Authentication temporarily blocked")
        if len(self.challenges) >= 4096:
            raise ProtocolError("Authentication capacity reached")
        challenge = {
            "type": "challenge",
            "version": PROTOCOL_VERSION,
            "nonce": secrets.token_hex(32),
            "timestamp": int(time.time()),
        }
        self.challenges[str(challenge["nonce"])] = (challenge, ip, time.time() + 30)
        return challenge

    def authenticate(self, message: dict, ip: str, expected_role: str) -> Session:
        if self.blocked(ip):
            raise ProtocolError("Authentication temporarily blocked")
        nonce = message.get("nonce")
        entry = self.challenges.pop(nonce, None) if isinstance(nonce, str) else None
        try:
            principal = identifier(message.get("principal"))
            credential = self.credentials.get(principal)
            public = message.get("public_key", "")
            ed25519 = message.get("auth") == "ed25519"
            node_id = message.get("node_id", "")
            if ed25519:
                node_id = identifier(node_id, "node_id") if expected_role == "agent" else ""
                if not isinstance(public, str):
                    raise ValueError
                decode(public, 32)
                if credential is None:
                    if expected_role != "agent" or principal != node_id:
                        raise ValueError
                    credential = Credential(principal, "", "agent", node_id, public_key=public)
                elif credential.role != expected_role or credential.node_id != node_id:
                    raise ValueError
                elif credential.public_key != public:
                    if expected_role != "agent":
                        raise ValueError
                    credential = Credential(principal, "", "agent", node_id, public_key=public)
            if entry is None or credential is None:
                raise ValueError
            challenge, challenge_ip, expires = entry
            if (
                challenge_ip != ip
                or expires < time.time()
                or message.get("version") != PROTOCOL_VERSION
                or message.get("timestamp") != challenge["timestamp"]
                or abs(time.time() - challenge["timestamp"]) > 30
                or message.get("role") != expected_role
                or credential.role != expected_role
                or node_id != credential.node_id
            ):
                raise ValueError
            signature = message.get("proof")
            if not isinstance(signature, str):
                raise ValueError
            if ed25519:
                verify_auth(public, signature, challenge, principal, node_id, expected_role)
            elif credential.public_key or not hmac.compare_digest(
                signature, proof(credential.secret, challenge, expected_role, principal, node_id)
            ):
                raise ValueError
        except (ValueError, ProtocolError, TypeError):
            self.fail(ip)
            raise ProtocolError("Authentication failed") from None
        if len(self.sessions) >= self.settings.max_connections * 4:
            raise ProtocolError("Session capacity reached")
        session = Session(
            "sess_" + secrets.token_hex(16),
            credential,
            time.time() + self.settings.session_ttl,
            approved=credential.role != "agent",
        )
        self.sessions[session.id] = session
        return session

    def issue_token(self, session: Session) -> str:
        token = secrets.token_urlsafe(48)
        session.token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.tokens[session.token_hash] = session.id
        return token

    def bearer(self, header: str) -> Session:
        if not header.startswith("Bearer ") or len(header) > 256:
            raise ProtocolError("Authentication required")
        key = hashlib.sha256(header[7:].encode()).hexdigest()
        session = self.sessions.get(self.tokens.get(key, ""))
        if session is None or session.expires <= time.time():
            raise ProtocolError("Session expired or invalid")
        return session

    def revoke(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            self.tokens.pop(session.token_hash, None)
