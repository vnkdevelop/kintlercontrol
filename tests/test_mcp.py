import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from client.mcp_server import mcp, message
from scripts.configure import write_env
from tests.conftest import Environment


async def test_all_tools_registered_with_schemas():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "nodes",
        "capabilities",
        "session_status",
        "shell",
        "message",
        "sysinfo",
        "ps",
        "kill",
        "upload",
        "download",
        "screenshot",
        "registry",
        "services",
        "tasks",
    }
    for tool in tools:
        assert "ctx" not in tool.inputSchema.get("properties", {})
        assert tool.description
    shell = next(tool for tool in tools if tool.name == "shell")
    assert set(shell.inputSchema["required"]) == {"node", "command"}
    notice = next(tool for tool in tools if tool.name == "message")
    assert set(notice.inputSchema["required"]) == {"node", "text"}


async def test_real_mcp_stdio_login_and_tool_call(environment: Environment, tmp_path: Path):
    path = tmp_path / "mcp-operator" / ".env"
    write_env(
        path,
        {
            "KINTLER_URL": environment.url,
            "KINTLER_CONTROLLER_ID": environment.operator.principal,
            "KINTLER_CONTROLLER_SECRET": environment.operator.secret,
            "KINTLER_ALLOW_LOCAL_HTTP": "1",
        },
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "client.mcp_server", "--env", str(path)]
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert len(tools.tools) == 14
        status = await session.call_tool(
            "session_status", {"node": "node_a", "required_tools": ["shell"]}
        )
        assert not status.isError
        assert json.loads(status.content[0].text)["target"]["id"] == "node_a"
        result = await session.call_tool("sysinfo", {"node": "node_a"})
        assert not result.isError
        assert json.loads(result.content[0].text)["ok"]
        result = await session.call_tool(
            "shell",
            {
                "node": "node_b",
                "command": "echo MCP_STDIO_OK",
                "interpreter": "cmd" if os.name == "nt" else "bash",
            },
        )
        assert not result.isError
        assert "MCP_STDIO_OK" in json.loads(result.content[0].text)["stdout"]


async def test_message_mcp_uses_shell_without_new_backend_permission():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.resolve.return_value = "node_fixture"
    client.nodes.return_value = [{"id": "node_fixture", "platform": "Windows"}]
    client.call.return_value = {"ok": True}
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(client=client))
    )
    assert (await message("fixture", "Привет", ctx))["ok"]
    assert client.call.call_args.args[:2] == ("node_fixture", "shell")
    assert client.call.call_args.args[2]["shell"] == "powershell"
