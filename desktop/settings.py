import os
from pathlib import Path

from dotenv import dotenv_values

from agent.fingerprint import node_id
from agent.kintler_agent import AgentSettings
from client.kintler_sdk import ClientSettings
from common.identity import protect, unlock
from common.protocol import SAFE_TOOLS, TOOLS, client_tls, identifier
from scripts.configure import write_env


def profile(role: str) -> Path:
    if role not in {"agent", "controller"}:
        raise ValueError("Unknown application role")
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share")))
    return base / "KintlerControl" / role / ".env"


def load_profile(path: Path, role: str) -> dict[str, str]:
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    if values.get("KINTLER_CREDENTIALS"):
        raise ValueError("Это серверный .env. Нужен отдельный файл устройства или оператора.")
    other = "AGENT" if role == "controller" else "CONTROLLER"
    if values.get(f"KINTLER_{other}_SECRET"):
        raise ValueError("Этот .env предназначен для другой роли.")
    if role == "controller" and values.get("KINTLER_CONTROLLER_SECRET"):
        values["KINTLER_CONTROLLER_SECRET"] = unlock(
            values["KINTLER_CONTROLLER_SECRET"], "controller"
        ).decode()
    return values


def controller_settings(values: dict[str, str]) -> ClientSettings:
    url = values.get("KINTLER_URL", "").strip().rstrip("/")
    principal = identifier(values.get("KINTLER_CONTROLLER_ID"), "operator ID")
    secret = unlock(values.get("KINTLER_CONTROLLER_SECRET", "").strip(), "controller").decode()
    private = (
        unlock(values["KINTLER_CONTROLLER_PRIVATE_KEY"], "controller")
        if values.get("KINTLER_CONTROLLER_PRIVATE_KEY")
        else b""
    )
    if not private and len(secret) < 32:
        raise ValueError("Введите выданный сервером ключ оператора, минимум 32 символа.")
    local = values.get("KINTLER_ALLOW_LOCAL_HTTP") == "1"
    ca = values.get("KINTLER_CA_FILE") or None
    client_tls(url, local, ca)
    return ClientSettings(url, principal, secret, local, ca, private)


def agent_settings(values: dict[str, str], path: Path) -> AgentSettings:
    url = values.get("KINTLER_URL", "").strip().rstrip("/")
    principal = identifier(values.get("KINTLER_AGENT_ID"), "agent ID")
    secret = values.get("KINTLER_AGENT_SECRET", "").strip()
    if len(secret) < 32:
        raise ValueError("Введите отдельный ключ этого ПК, выданный сервером.")
    tools = frozenset(
        t.strip()
        for t in values.get("KINTLER_AGENT_TOOLS", ",".join(sorted(SAFE_TOOLS))).split(",")
        if t.strip()
    )
    if not tools or not tools <= TOOLS:
        raise ValueError("Выберите хотя бы один поддерживаемый инструмент.")
    workspace = Path(values.get("KINTLER_WORKSPACE") or str(path.parent / "workspace"))
    if not workspace.is_absolute():
        workspace = path.parent / workspace
    local = values.get("KINTLER_ALLOW_LOCAL_HTTP") == "1"
    ca = values.get("KINTLER_CA_FILE") or None
    client_tls(url, local, ca)
    return AgentSettings(
        url,
        principal,
        secret,
        node_id(),
        values.get("KINTLER_NODE_NAME") or "Мой ПК",
        workspace,
        tools,
        local,
        ca,
    )


def save_profile(path: Path, values: dict[str, str], role: str) -> None:
    values = dict(values)
    if role == "controller" and values.get("KINTLER_CONTROLLER_SECRET"):
        values["KINTLER_CONTROLLER_SECRET"] = protect(
            unlock(values["KINTLER_CONTROLLER_SECRET"], role), role
        )
    common = {"KINTLER_URL", "KINTLER_CA_FILE", "KINTLER_ALLOW_LOCAL_HTTP"}
    allowed = common | (
        {"KINTLER_CONTROLLER_ID", "KINTLER_CONTROLLER_SECRET", "KINTLER_CONTROLLER_PRIVATE_KEY"}
        if role == "controller"
        else {
            "KINTLER_AGENT_ID",
            "KINTLER_AGENT_SECRET",
            "KINTLER_AGENT_TOOLS",
            "KINTLER_NODE_NAME",
            "KINTLER_WORKSPACE",
        }
    )
    write_env(path, {key: value for key, value in values.items() if key in allowed})
