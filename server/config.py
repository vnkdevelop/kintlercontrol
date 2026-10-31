import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from common.identity import decode
from common.protocol import TOOLS, identifier


@dataclass(frozen=True)
class Credential:
    principal: str
    secret: str = field(repr=False)
    role: str = "controller"
    node_id: str = ""
    nodes: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()
    public_key: str = ""
    can_approve: bool = False

    def permits(self, node: str, tool: str | None = None) -> bool:
        return ("*" in self.nodes or node in self.nodes) and (tool is None or tool in self.tools)


@dataclass(frozen=True)
class Settings:
    credentials: dict[str, Credential] = field(repr=False)
    database: Path = Path("data/kintler.sqlite3")
    host: str = "0.0.0.0"
    port: int = 7443
    session_ttl: int = 3600
    heartbeat_timeout: int = 45
    command_timeout: int = 120
    max_commands: int = 1000
    max_running: int = 64
    max_events: int = 512
    max_output: int = 2 * 1024 * 1024
    max_buffered: int = 32 * 1024 * 1024
    retention: int = 600
    max_connections: int = 256
    trusted_proxies: tuple = ()
    allow_local_http: bool = False
    approval_timeout: int = 600
    max_pending: int = 64

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.environ.get("KINTLER_CREDENTIALS", "")
        try:
            items = json.loads(raw)
            if not isinstance(items, list) or not items or len(items) > 1024:
                raise ValueError
            credentials: dict[str, Credential] = {}
            node_ids: set[str] = set()
            for item in items:
                principal = identifier(item["id"])
                secret = item.get("secret", "")
                role = item["role"]
                public = item.get("public_key", "")
                can_approve = item.get("can_approve", False)
                if (
                    principal in credentials
                    or not isinstance(secret, str)
                    or not isinstance(can_approve, bool)
                ):
                    raise ValueError
                if role not in {"agent", "controller"}:
                    raise ValueError
                if public:
                    decode(public, 32)
                    if secret:
                        raise ValueError
                elif len(secret) < 32:
                    raise ValueError
                node_id = identifier(item["node_id"]) if role == "agent" else ""
                if not isinstance(item.get("nodes", []), list) or not isinstance(
                    item.get("tools", []), list
                ):
                    raise ValueError
                nodes = frozenset(item.get("nodes", []))
                tools = frozenset(item.get("tools", []))
                if not tools <= TOOLS or any(n != "*" and not identifier(n) for n in nodes):
                    raise ValueError
                if role == "controller" and (not nodes or not tools):
                    raise ValueError
                if node_id and node_id in node_ids:
                    raise ValueError
                node_ids.add(node_id)
                credentials[principal] = Credential(
                    principal, secret, role, node_id, nodes, tools, public, can_approve
                )
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(
                "KINTLER_CREDENTIALS must contain unique, scoped credentials with 32+ character secrets"
            ) from exc
        secrets = [c.secret for c in credentials.values() if c.secret]
        if len(set(secrets)) != len(secrets):
            raise ValueError("Each principal requires a different secret")
        proxies = tuple(
            ipaddress.ip_network(p.strip())
            for p in os.environ.get("KINTLER_TRUSTED_PROXIES", "").split(",")
            if p.strip()
        )
        return cls(
            credentials=credentials,
            database=Path(os.environ.get("KINTLER_DATABASE", "data/kintler.sqlite3")),
            host=os.environ.get("KINTLER_HOST", "0.0.0.0"),
            port=int(os.environ.get("KINTLER_PORT", "7443")),
            trusted_proxies=proxies,
            allow_local_http=os.environ.get("KINTLER_ALLOW_LOCAL_HTTP") == "1",
        )
