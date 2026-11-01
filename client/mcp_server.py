import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP

from client.kintler_sdk import ClientSettings, KintlerClient
from common.protocol import ProtocolError
from desktop.presets import message_command


@dataclass
class Runtime:
    client: KintlerClient


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[Runtime]:
    async with KintlerClient(replace(ClientSettings.from_env(), origin="mcp")) as client:
        yield Runtime(client)


mcp = FastMCP(
    "Kintler Remote",
    lifespan=lifespan,
    instructions="Manage only explicitly enrolled devices. Call nodes and capabilities first. Destructive tools require user authorization. All operations are audited and restricted by operator ACL and local device policy. File paths on devices are relative to their configured workspace. Shell access is not a sandbox.",
)


def sdk(ctx: Context) -> KintlerClient:
    return ctx.request_context.lifespan_context.client


@mcp.tool()
async def nodes(ctx: Context) -> list[dict]:
    """List permitted devices, NodeIDs, names, online status and telemetry."""
    return await sdk(ctx).nodes()


@mcp.tool()
async def capabilities(ctx: Context) -> dict:
    """Get the current operator's tools, allowed NodeIDs and session expiry."""
    return await sdk(ctx).tools()


@mcp.tool()
async def shell(
    node: str,
    command: str,
    ctx: Context,
    interpreter: str = "cmd",
    cwd: str = ".",
    timeout: float = 120,
) -> dict:
    """Run an explicitly authorized cmd, powershell or bash command with bounded output. This grants OS-level access; not sandboxed."""
    return await sdk(ctx).call(
        node, "shell", {"command": command, "shell": interpreter, "cwd": cwd}, timeout
    )


@mcp.tool()
async def sysinfo(node: str, ctx: Context) -> dict:
    """Read CPU, RAM, disks, network adapters, uptime and privilege level."""
    return await sdk(ctx).call(node, "sysinfo")


@mcp.tool()
async def message(node: str, text: str, ctx: Context) -> dict:
    """Show explicitly requested literal text on an interactive Windows desktop. Uses shell permission; waits for OK/cancel or 120 seconds."""
    client = sdk(ctx)
    selected = await client.resolve(node)
    devices = await client.nodes()
    if not any(
        device["id"] == selected and device["platform"].lower().startswith("windows")
        for device in devices
    ):
        raise ProtocolError("Message windows require an interactive Windows device")
    args = message_command(text)
    timeout = args.pop("timeout")
    return await client.call(selected, "shell", args, timeout)


@mcp.tool()
async def ps(node: str, ctx: Context, sort: str = "ram", limit: int = 100) -> dict:
    """List processes sorted by ram, cpu or pid. Keep created timestamps for safe termination."""
    return await sdk(ctx).call(node, "ps", {"sort": sort, "limit": limit})


@mcp.tool()
async def kill(node: str, pid: int, created: float, ctx: Context, force: bool = False) -> dict:
    """Terminate an authorized process; created must match its timestamp from ps. force permits killing after graceful timeout."""
    return await sdk(ctx).call(node, "kill", {"pid": pid, "created": created, "force": force})


@mcp.tool()
async def upload(
    node: str, local_path: str, remote_path: str, ctx: Context, overwrite: bool = False
) -> dict:
    """Upload a local operator file to the device workspace in chunks with SHA-256 verification."""
    return await sdk(ctx).upload(node, local_path, remote_path, overwrite)


@mcp.tool()
async def download(
    node: str, remote_path: str, local_path: str, ctx: Context, overwrite: bool = False
) -> dict:
    """Download a workspace file to the operator device with SHA-256 verification and atomic replacement."""
    return await sdk(ctx).download(node, remote_path, local_path, overwrite)


@mcp.tool()
async def screenshot(node: str, local_path: str, ctx: Context, monitor: int = 1) -> dict:
    """Capture a locally enabled graphical desktop monitor and download its PNG to the operator. The device retains the capture in its workspace."""
    result = await sdk(ctx).checked(node, "screenshot", {"monitor": monitor})
    return await sdk(ctx).download(node, result["path"], local_path)


@mcp.tool()
async def registry(
    node: str,
    key: str,
    ctx: Context,
    operation: str = "query",
    name: str = "",
    value: str = "",
    value_type: str = "REG_SZ",
) -> dict:
    """Query a local Windows registry value under OS ACL, or set it with Administrator privileges and explicit authorization."""
    return await sdk(ctx).call(
        node,
        "registry",
        {
            "operation": operation,
            "key": key,
            "name": name,
            "value": value,
            "value_type": value_type,
        },
    )


@mcp.tool()
async def services(node: str, ctx: Context, operation: str = "list", name: str = "") -> dict:
    """List/inspect OS services under OS ACL. Start/stop need Administrator/root and authorization; Linux supports restart."""
    return await sdk(ctx).call(node, "services", {"operation": operation, "name": name})


@mcp.tool()
async def tasks(
    node: str, ctx: Context, operation: str = "list", name: str = "", command: str = ""
) -> dict:
    """List Windows tasks under OS ACL, or explicitly create an ONLOGON task with Administrator privileges and user authorization."""
    return await sdk(ctx).call(
        node, "tasks", {"operation": operation, "name": name, "command": command}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kintler MCP stdio bridge for AI clients")
    parser.add_argument("--env", default=".env", help="Absolute path to operator .env recommended")
    args = parser.parse_args()
    load_dotenv(args.env)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
