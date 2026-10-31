import secrets
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestServer

from server.app import create_app
from server.config import Credential, Settings


async def test_untrusted_proxy_headers_cannot_enable_tls(tmp_path: Path):
    credential = Credential(
        "operator", secrets.token_urlsafe(48), nodes=frozenset({"*"}), tools=frozenset({"sysinfo"})
    )
    app = create_app(Settings({"operator": credential}, tmp_path / "db", allow_local_http=False))
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        async with http.post(
            server.make_url("/api/auth/challenge"),
            headers={"X-Forwarded-Proto": "https", "X-Real-IP": "10.0.0.1"},
        ) as response:
            assert response.status == 403
        async with http.get(server.make_url("/healthz")) as response:
            assert response.status == 200
