import secrets
import time
from pathlib import Path

import pytest

from common.protocol import ProtocolError, auth_message, client_tls
from server.auth import Auth
from server.config import Credential, Settings


@pytest.fixture
def auth(tmp_path: Path) -> Auth:
    credentials = [
        Credential(
            "op",
            secrets.token_urlsafe(48),
            nodes=frozenset({"node_a"}),
            tools=frozenset({"sysinfo"}),
        ),
        Credential("device", secrets.token_urlsafe(48), "agent", "node_a"),
    ]
    return Auth(Settings({c.principal: c for c in credentials}, tmp_path / "test.sqlite3"))


def test_auth_token_and_replay(auth: Auth):
    challenge = auth.challenge("127.0.0.1")
    value = auth_message(auth.settings.credentials["op"].secret, challenge, "controller", "op")
    session = auth.authenticate(value, "127.0.0.1", "controller")
    token = auth.issue_token(session)
    assert auth.bearer("Bearer " + token) is session
    assert token not in repr(session)
    with pytest.raises(ProtocolError, match="failed"):
        auth.authenticate(value, "127.0.0.1", "controller")
    auth.revoke(session.id)
    with pytest.raises(ProtocolError):
        auth.bearer("Bearer " + token)


@pytest.mark.parametrize(
    "mutation", ["role", "node_id", "timestamp", "version", "proof", "principal"]
)
def test_binding(auth: Auth, mutation: str):
    challenge = auth.challenge("127.0.0.1")
    value = auth_message(
        auth.settings.credentials["device"].secret, challenge, "agent", "device", "node_a"
    )
    value[mutation] = "wrong"
    with pytest.raises(ProtocolError):
        auth.authenticate(value, "127.0.0.1", "agent")


def test_ban_and_challenge_ip(auth: Auth):
    challenge = auth.challenge("10.0.0.1")
    value = auth_message(auth.settings.credentials["op"].secret, challenge, "controller", "op")
    with pytest.raises(ProtocolError):
        auth.authenticate(value, "10.0.0.2", "controller")
    for _ in range(4):
        with pytest.raises(ProtocolError):
            auth.authenticate({}, "10.0.0.2", "controller")
    assert auth.blocked("10.0.0.2")
    with pytest.raises(ProtocolError, match="blocked"):
        auth.challenge("10.0.0.2")
    auth.failures["10.0.0.2"] = (5, time.time() - 1)
    assert not auth.blocked("10.0.0.2")


def test_expired_nonce(auth: Auth):
    challenge = auth.challenge("127.0.0.1")
    auth.challenges[challenge["nonce"]] = (challenge, "127.0.0.1", time.time() - 1)
    value = auth_message(auth.settings.credentials["op"].secret, challenge, "controller", "op")
    with pytest.raises(ProtocolError):
        auth.authenticate(value, "127.0.0.1", "controller")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://192.168.1.3",
        "https://user:pass@example.com",
        "https://example.com?token=x",
        "ftp://localhost",
    ],
)
def test_cleartext_rejected(url: str):
    with pytest.raises(ValueError):
        client_tls(url, allow_local_http=True)


def test_tls_policy():
    import ssl

    context = client_tls("https://example.com")
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert client_tls("http://127.0.0.1", True) is True
    with pytest.raises(ValueError):
        client_tls("http://127.0.0.1")
