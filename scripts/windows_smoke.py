import asyncio
import ctypes
import json
import os
import secrets
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil
from aiohttp.test_utils import TestServer
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from agent.fingerprint import node_id
from agent.modules.executor import terminate
from client.kintler_sdk import ClientSettings, KintlerClient
from common.identity import new_private_key, protect, public_key
from common.protocol import TOOLS
from scripts.configure import write_env
from scripts.container_smoke import certificate
from server.app import SERVICE, create_app
from server.config import Credential, Settings


async def start(executable: Path, *args: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        subprocess.list2cmdline([str(executable), *args]),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


def close_own_window(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    process = psutil.Process(pid)
    pids = {pid, *(child.pid for child in process.children(recursive=True))}
    user32 = ctypes.windll.user32
    matches: list[int] = []

    def collect(handle: int, context: object) -> bool:
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value in pids:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, 256)
            if title.value.startswith("Kintler"):
                matches.append(handle)
        return True

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    callback = callback_type(collect)
    user32.EnumWindows(callback, 0)
    for handle in matches:
        user32.PostMessageW(handle, 0x0010, 0, 0)
    return bool(matches)


def console_for_process(pid: int, close: bool = False) -> str:
    if sys.platform != "win32":
        return ""
    kernel = ctypes.windll.kernel32
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handles: list[int] = []
    original = kernel.GetConsoleWindow()
    pids = [pid, *(child.pid for child in psutil.Process(pid).children(recursive=True))]
    try:
        kernel.FreeConsole()
        for candidate in reversed(pids):
            if not kernel.AttachConsole(candidate):
                continue
            title = ctypes.create_unicode_buffer(256)
            kernel.GetConsoleTitleW(title, len(title))
            if not title.value.startswith("Kintler"):
                kernel.FreeConsole()
                continue

            class Coord(ctypes.Structure):
                _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]

            class Rect(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_short),
                    ("top", ctypes.c_short),
                    ("right", ctypes.c_short),
                    ("bottom", ctypes.c_short),
                ]

            class Info(ctypes.Structure):
                _fields_ = [
                    ("size", Coord),
                    ("cursor", Coord),
                    ("attributes", ctypes.c_ushort),
                    ("window", Rect),
                    ("maximum", Coord),
                ]

            handle = kernel.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
            if handle in {None, ctypes.c_void_p(-1).value}:
                kernel.FreeConsole()
                continue
            handles.append(handle)
            info = Info()
            kernel.GetConsoleScreenBufferInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Info)]
            if not kernel.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
                kernel.FreeConsole()
                continue
            count = min(info.size.x * info.size.y, 1000000)
            text = ctypes.create_unicode_buffer(count + 1)
            copied = ctypes.c_ulong()
            kernel.ReadConsoleOutputCharacterW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_ulong,
                Coord,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            kernel.ReadConsoleOutputCharacterW(
                handle, text, count, Coord(0, 0), ctypes.byref(copied)
            )
            result = text.value
            if "KINTLER" not in result and "/help" not in result:
                return ""
            if close:

                class Key(ctypes.Structure):
                    _fields_ = [
                        ("down", ctypes.c_int),
                        ("repeat", ctypes.c_ushort),
                        ("virtual", ctypes.c_ushort),
                        ("scan", ctypes.c_ushort),
                        ("character", ctypes.c_wchar),
                        ("control", ctypes.c_ulong),
                    ]

                class Record(ctypes.Structure):
                    _fields_ = [("kind", ctypes.c_ushort), ("key", Key)]

                key = Record(
                    1,
                    Key(
                        1,
                        1,
                        0x51,
                        0,
                        "\x11" if "Controller" in title.value else "q",
                        8 if "Controller" in title.value else 0,
                    ),
                )
                written = ctypes.c_ulong()
                kernel.WriteConsoleInputW.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(Record),
                    ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_ulong),
                ]
                incoming = kernel.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
                if incoming not in {None, ctypes.c_void_p(-1).value}:
                    handles.append(incoming)
                    accepted = kernel.WriteConsoleInputW(
                        incoming, ctypes.byref(key), 1, ctypes.byref(written)
                    )
                    if not accepted or written.value != 1:
                        raise RuntimeError("Could not send the test terminal's close key")
            return result
        return ""
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)
        kernel.FreeConsole()
        if original:
            kernel.AttachConsole(0xFFFFFFFF)


async def run(root: Path, temporary: Path) -> None:
    output = root / "dist/windows"
    executables = {
        role: output / name
        for role, name in {
            "agent": "KintlerAgent.exe",
            "controller": "KintlerController.exe",
            "mcp": "KintlerController.exe",
            "setup": "KintlerController.exe",
        }.items()
    }
    for mode, arguments in (
        ("config", ["--mcp-config", "--host", "opencode"]),
        ("skill", ["--install-skill", "--host", "shared", "--dry-run"]),
    ):
        process = await asyncio.create_subprocess_exec(
            "cmd.exe",
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline([str(executables["controller"]), *arguments]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
            assert process.returncode == 0, f"Frozen integration mode {mode} failed"
            value = json.loads(stdout)
            if mode == "config":
                assert value["type"] == "local" and value["command"][1:] == ["--mcp"]
                assert "enabled" not in value
            else:
                assert value["dry_run"] and value["skill"] == "kintler-remote"
        finally:
            await terminate(process)
    for role, executable in executables.items():
        result = temporary / (role + "-startup.json")
        prefix = ["--mcp"] if role == "mcp" else ["--setup"] if role == "setup" else []
        process = await start(executable, *prefix, "--self-test", str(result))
        try:
            await asyncio.wait_for(process.wait(), 60)
            assert process.returncode == 0 and json.loads(result.read_text())["ok"], (
                f"Frozen {role} startup failed"
            )
        finally:
            await terminate(process)
        if role in {"agent", "controller"}:
            if role == "agent":
                assert json.loads(result.read_text())["native"]
                write_env(temporary / role / "unused/.env", {"KINTLER_URL": "https://127.0.0.1:1"})
            window = await start(executable, "--env", str(temporary / role / "unused/.env"))
            try:
                for _ in range(200):
                    if window.returncode is not None:
                        raise RuntimeError(f"Frozen {role} GUI exited early")
                    text = await asyncio.to_thread(console_for_process, window.pid, True)
                    if text:
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError(f"Frozen {role} GUI did not open")
                await asyncio.wait_for(window.wait(), 30)
                assert window.returncode == 0, f"Frozen {role} GUI did not close cleanly"
            finally:
                await terminate(window)
    node = node_id()
    relocated = temporary / "relocated"
    relocated.mkdir()
    import shutil

    shutil.copyfile(executables["controller"], relocated / "KintlerController.exe")
    shortcut = temporary / "Controller.lnk"
    link_process = await start(
        relocated / "KintlerController.exe",
        "--create-shortcut",
        "--shortcut-destination",
        str(shortcut),
    )
    await asyncio.wait_for(link_process.wait(), 60)
    assert link_process.returncode == 0 and shortcut.is_file()
    setup_output = temporary / "setup-output"
    setup_process = await start(
        relocated / "KintlerController.exe",
        "--setup",
        "--url",
        "https://relay.example.com",
        "--output",
        str(setup_output),
        "--owner-profile",
        str(temporary / "setup-owner/.env"),
    )
    await asyncio.wait_for(setup_process.wait(), 60)
    assert setup_process.returncode == 0 and (setup_output / "Agent/KintlerAgent.exe").is_file()
    assert not (setup_output / "Agent/kintler-agent.json").exists()
    private = new_private_key()
    operator = Credential(
        "frozen_operator",
        "",
        nodes=frozenset({node}),
        tools=TOOLS,
        public_key=public_key(private),
        can_approve=True,
    )
    app = create_app(
        Settings(
            {operator.principal: operator},
            temporary / "db.sqlite3",
            allow_local_http=False,
        )
    )
    certificate(temporary)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(temporary / "cert.pem", temporary / "key.pem")
    server = TestServer(app)
    await server.start_server(ssl=tls)
    async with server:
        url = str(server.make_url("")).rstrip("/")
        agent_env, controller_env = temporary / "agent/.env", temporary / "controller/.env"
        write_env(
            agent_env,
            {
                "KINTLER_URL": url,
                "KINTLER_AGENT_TOOLS": ",".join(sorted(TOOLS)),
                "KINTLER_WORKSPACE": str(temporary / "workspace"),
                "KINTLER_CA_FILE": str(temporary / "cert.pem"),
            },
        )
        write_env(
            controller_env,
            {
                "KINTLER_URL": url,
                "KINTLER_CONTROLLER_ID": operator.principal,
                "KINTLER_CONTROLLER_PRIVATE_KEY": protect(private, "controller"),
                "KINTLER_CA_FILE": str(temporary / "cert.pem"),
            },
        )
        process = await start(executables["agent"], "--headless", "--env", str(agent_env))
        try:
            async with KintlerClient(
                ClientSettings(
                    url,
                    operator.principal,
                    "",
                    ca_file=str(temporary / "cert.pem"),
                    private_key=private,
                )
            ) as client:
                async with asyncio.timeout(60):
                    for _ in range(200):
                        for request in await client.approvals():
                            assert not any(item["online"] for item in await client.nodes())
                            await client.approve(
                                request["request_id"],
                                True,
                                app[SERVICE].approvals.requests[request["request_id"]].code,
                            )
                        if any(item["online"] for item in await client.nodes()):
                            break
                        if process.returncode is not None:
                            raise RuntimeError("Frozen agent exited before connection")
                        await asyncio.sleep(0.2)
                    else:
                        raise TimeoutError("Frozen agent connection timed out")
                source = temporary / "source.bin"
                source.write_bytes(secrets.token_bytes(120000))
                await client.upload(node, source, "roundtrip.bin")
                await client.download(node, "roundtrip.bin", temporary / "download.bin")
                assert source.read_bytes() == (temporary / "download.bin").read_bytes()
                check = temporary / "controller-check.json"
                controller = await start(
                    executables["controller"],
                    "--env",
                    str(controller_env),
                    "--node",
                    node,
                    "--check",
                    str(check),
                )
                try:
                    await asyncio.wait_for(controller.wait(), 60)
                    assert controller.returncode == 0 and json.loads(check.read_text())["ok"]
                finally:
                    await terminate(controller)
                params = StdioServerParameters(
                    command=str(executables["mcp"]), args=["--mcp", "--env", str(controller_env)]
                )
                async with (
                    stdio_client(params) as (reader, writer),
                    ClientSession(reader, writer) as session,
                ):
                    await asyncio.wait_for(session.initialize(), 60)
                    assert len((await session.list_tools()).tools) == 13
                    mcp_result = await session.call_tool("sysinfo", {"node": node})
                    content = mcp_result.content[0]
                    assert isinstance(content, TextContent)
                    assert not mcp_result.isError and json.loads(content.text)["ok"]
                assert app[SERVICE].registry.nodes[node].ready
                await terminate(process)
                repeated = await start(executables["agent"], "--headless", "--env", str(agent_env))
                try:
                    async with asyncio.timeout(30):
                        while True:
                            requests = await client.approvals()
                            if requests:
                                break
                            await asyncio.sleep(0.1)
                    request = requests[0]
                    assert (
                        request["known"]
                        and request["code_required"]
                        and not app[SERVICE].registry.nodes[node].view()["online"]
                    )
                    await client.approve(request["request_id"], False)
                    await asyncio.wait_for(repeated.wait(), 30)
                    assert repeated.returncode == 0
                finally:
                    await terminate(repeated)
        finally:
            await terminate(process)
    print(
        "PASS: Rust native agent + standalone controller/MCP; TLS 1.3, Ed25519, owner approval on every reconnect, shell/sysinfo/files, rejection without cached access."
    )


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Windows executables must be tested on Windows")
    root = Path(__file__).resolve().parent.parent
    base = Path(os.environ["LOCALAPPDATA"]) / "Temp/opencode"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kintler-frozen-", dir=base) as temporary:
        asyncio.run(run(root, Path(temporary)))


if __name__ == "__main__":
    main()
