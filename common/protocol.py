import hashlib
import hmac
import json
import re
import ssl
import time
from urllib.parse import urlsplit

PROTOCOL_VERSION = 1
CHUNK_SIZE = 48 * 1024
MAX_MESSAGE = 256 * 1024
TOOLS = frozenset(
    {
        "shell",
        "sysinfo",
        "ps",
        "kill",
        "screenshot",
        "upload",
        "download",
        "registry",
        "services",
        "tasks",
    }
)
SAFE_TOOLS = frozenset({"sysinfo", "ps", "upload", "download"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class ProtocolError(Exception):
    pass


class ApprovalDenied(ProtocolError):
    pass


def identifier(value: object, field: str = "id") -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProtocolError(f"Invalid {field}")
    return value


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def proof(secret: str, challenge: dict, role: str, principal: str, node_id: str = "") -> str:
    body = {
        "v": PROTOCOL_VERSION,
        "nonce": challenge["nonce"],
        "timestamp": challenge["timestamp"],
        "role": role,
        "principal": principal,
        "node_id": node_id,
    }
    return hmac.new(secret.encode(), canonical(body), hashlib.sha256).hexdigest()


def auth_message(
    secret: str, challenge: dict, role: str, principal: str, node_id: str = ""
) -> dict:
    return {
        "type": "authenticate",
        "version": PROTOCOL_VERSION,
        "role": role,
        "principal": principal,
        "node_id": node_id,
        "nonce": challenge["nonce"],
        "timestamp": challenge["timestamp"],
        "proof": proof(secret, challenge, role, principal, node_id),
    }


def client_tls(
    url: str, allow_local_http: bool = False, ca_file: str | None = None
) -> ssl.SSLContext | bool:
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("Relay URL must be a server origin without a path prefix")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Relay URL must not contain credentials, query or fragment")
    if parsed.scheme in {"https", "wss"}:
        context = ssl.create_default_context(cafile=ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        return context
    if (
        parsed.scheme in {"http", "ws"}
        and allow_local_http
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        return True
    raise ValueError("TLS is mandatory; cleartext is allowed only explicitly on loopback")


def ws_url(url: str, path: str) -> str:
    return url.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1) + path


def now() -> float:
    return time.time()
