import asyncio

import aiohttp

from client.kintler_sdk import KintlerClient
from common.protocol import ProtocolError


async def connection_snapshot(
    client: KintlerClient, node: str = "", required_tools: list[str] | None = None
) -> dict:
    scope = {
        "commands": "explicit_remote_node",
        "transfer_local_path": "controller_host",
        "transfer_remote_path": "selected_node_workspace",
        "approvals": "human_controller_only",
    }
    try:
        async with asyncio.timeout(10):
            inventory, capabilities, health = await asyncio.gather(
                client.nodes(),
                client.tools(),
                client.request("GET", "/healthz", auth=False),
                return_exceptions=True,
            )
        if any(isinstance(value, BaseException) for value in (inventory, capabilities, health)):
            raise ProtocolError("Connection check failed")
        if (
            not isinstance(inventory, list)
            or not isinstance(capabilities, dict)
            or not isinstance(health, dict)
            or not isinstance(capabilities.get("tools"), list)
        ):
            raise ProtocolError("Invalid connection response")
        allowed = set(capabilities["tools"])
        devices = []
        for entry in inventory:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("id"), str)
                or not isinstance(entry.get("name"), str)
                or not isinstance(entry.get("online"), bool)
                or not isinstance(entry.get("tools"), list)
            ):
                raise ProtocolError("Invalid device inventory")
            platform = entry.get("platform", "")
            devices.append(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "online": entry["online"],
                    "platform": platform,
                    "admin": entry.get("admin") is True,
                    "tools": sorted(allowed & set(entry["tools"])),
                    "interpreter": "cmd"
                    if str(platform).lower().startswith("windows")
                    else "bash"
                    if str(platform).lower().startswith("linux")
                    else None,
                }
            )
        connected = health.get("status") == "ok"
        matches = (
            [
                entry
                for entry in devices
                if entry["id"] == node or entry["name"].casefold() == node.casefold()
            ]
            if node
            else []
        )
        target = matches[0] if len(matches) == 1 else None
        selection = (
            "not_requested"
            if not node
            else "not_found"
            if not matches
            else "ambiguous"
            if len(matches) > 1
            else "ready"
            if target and target["online"]
            else "offline"
        )
        required = list(dict.fromkeys(required_tools or []))
        missing = [
            tool
            for tool in required
            if ("shell" if tool == "message" else tool) not in (target["tools"] if target else [])
        ]
        if selection == "ready" and missing:
            selection = "tool_unavailable"
        if not connected:
            selection = "backend_unhealthy"
        return {
            "ok": connected and (not node or selection == "ready"),
            "connected": connected,
            "available": sum(entry["online"] for entry in devices) if connected else 0,
            "devices": devices,
            "target": target,
            "selection": selection,
            "missing_tools": missing,
            "can_approve": capabilities.get("can_approve") is True,
            "scope": scope,
        }
    except (aiohttp.ClientError, ProtocolError, TimeoutError, ValueError, KeyError, TypeError):
        return {
            "ok": False,
            "connected": False,
            "available": 0,
            "devices": [],
            "target": None,
            "selection": "connection_failed",
            "missing_tools": [],
            "can_approve": False,
            "scope": scope,
        }
