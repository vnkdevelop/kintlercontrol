import base64
import ctypes
import hashlib
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from common.protocol import PROTOCOL_VERSION, ProtocolError, canonical


def decode(value: str, length: int) -> bytes:
    try:
        result = base64.b64decode(value, validate=True)
        if len(result) != length:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ProtocolError("Invalid key or signature encoding") from None


def new_private_key() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )


def public_key(private: bytes) -> str:
    return base64.b64encode(
        Ed25519PrivateKey.from_private_bytes(private)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()


def fingerprint(public: str) -> str:
    digest = hashlib.sha256(decode(public, 32)).hexdigest()
    return " ".join(digest[i : i + 4] for i in range(0, 32, 4))


def signed_body(
    challenge: dict, principal: str, node: str, public: str, role: str = "agent"
) -> bytes:
    return canonical(
        {
            "v": PROTOCOL_VERSION,
            "nonce": challenge["nonce"],
            "timestamp": challenge["timestamp"],
            "role": role,
            "principal": principal,
            "node_id": node,
            "auth": "ed25519",
            "public_key": public,
        }
    )


def signed_auth(
    private: bytes, challenge: dict, principal: str, node: str, role: str = "agent"
) -> dict:
    public = public_key(private)
    signature = Ed25519PrivateKey.from_private_bytes(private).sign(
        signed_body(challenge, principal, node, public, role)
    )
    return {
        "type": "authenticate",
        "version": PROTOCOL_VERSION,
        "role": role,
        "principal": principal,
        "node_id": node,
        "nonce": challenge["nonce"],
        "timestamp": challenge["timestamp"],
        "auth": "ed25519",
        "public_key": public,
        "proof": base64.b64encode(signature).decode(),
    }


def verify_auth(
    public: str, signature: str, challenge: dict, principal: str, node: str, role: str = "agent"
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(decode(public, 32)).verify(
            decode(signature, 64), signed_body(challenge, principal, node, public, role)
        )
    except (ValueError, InvalidSignature):
        raise ProtocolError("Invalid device signature") from None


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data: bytes, role: str, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise ValueError("DPAPI profile can only be opened by its original Windows user")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming = Blob(len(data), buffer)
    entropy_bytes = ("Kintler:" + role).encode()
    entropy_buffer = (ctypes.c_ubyte * len(entropy_bytes)).from_buffer_copy(entropy_bytes)
    entropy = Blob(len(entropy_bytes), entropy_buffer)
    output = Blob()
    function = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    function.restype = ctypes.c_int
    if not function(
        ctypes.byref(incoming), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)
    ):
        raise ValueError("Windows could not protect/unlock this profile for the current user")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(output.data, ctypes.c_void_p))


def protect(data: bytes, role: str) -> str:
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(dpapi(data, role, False)).decode()
    return "base64:" + base64.b64encode(data).decode()


def unlock(value: str, role: str) -> bytes:
    try:
        if value.startswith("dpapi:"):
            return dpapi(base64.b64decode(value[6:], validate=True), role, True)
        if value.startswith("base64:"):
            return base64.b64decode(value[7:], validate=True)
        return value.encode()
    except (ValueError, TypeError):
        raise ValueError(
            "Cannot unlock the local profile; use the original Windows account"
        ) from None
