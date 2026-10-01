"""RFC 8291/8292 mediante primitivas cryptography y TLS con dirección fijada."""

import http.client
import ipaddress
import os
import secrets
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from chat.config import Settings
from chat.protocol import decode_binary, encode_binary


def endpoint_url(endpoint: str) -> SplitResult:
    url = urlsplit(endpoint)
    if (len(endpoint) > 2048 or not endpoint.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in endpoint)
            or url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment
            or url.port not in (None, 443) or "\\" in endpoint):
        raise ValueError("HTTPS push endpoint required")
    try:
        address = ipaddress.ip_address(url.hostname)
    except ValueError:
        if "." not in url.hostname or url.hostname.endswith((".localhost", ".local", ".internal")):
            raise ValueError("Public endpoint required") from None
    else:
        if not address.is_global:
            raise ValueError("Public endpoint required")
    return url


def public_address(host: str) -> str:
    addresses = [row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Nonpublic push destination")
    return str(addresses[0])


def encrypt_push(public: bytes, auth: bytes, payload: bytes, *,
                 private: ec.EllipticCurvePrivateKey | None = None, salt: bytes | None = None) -> bytes:
    if len(auth) != 16 or len(public) != 65 or len(payload) > 3993:
        raise ValueError("Invalid push material")
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    sender = private or ec.generate_private_key(ec.SECP256R1())
    sender_public = sender.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    salt = salt if salt is not None else secrets.token_bytes(16)
    if len(salt) != 16:
        raise ValueError("Invalid salt")
    shared = sender.exchange(ec.ECDH(), receiver)
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\0" + public + sender_public).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\0").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\0").derive(ikm)
    return salt + (4096).to_bytes(4, "big") + bytes([65]) + sender_public + AESGCM(key).encrypt(nonce, payload + b"\x02", None)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        self.tls_context = ssl.create_default_context()
        super().__init__(host, timeout=5, context=self.tls_context)
        self.address = address

    def connect(self) -> None:
        # Sin segunda resolución DNS, proxy de entorno ni redirecciones.
        raw = socket.create_connection((self.address, 443), timeout=5)
        try:
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def send_push(settings: Settings, endpoint: str, p256dh: str, auth_secret: str, payload: bytes, ttl: int) -> int:
    url = endpoint_url(endpoint)
    assert url.hostname
    address = public_address(url.hostname)
    private = serialization.load_pem_private_key(Path(os.environ["VAPID_PRIVATE_KEY_FILE"]).read_bytes(), password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
        raise ValueError("Invalid VAPID key")
    public = encode_binary(private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    origin_host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    token = jwt.encode({"aud": f"https://{origin_host}", "exp": int(time.time()) + 3600,
                        "sub": settings.vapid_subject}, private, algorithm="ES256")
    body = encrypt_push(decode_binary(p256dh, minimum=65, maximum=65),
                        decode_binary(auth_secret, minimum=16, maximum=16), payload)
    connection = PinnedHTTPS(url.hostname, address)
    try:
        connection.request("POST", (url.path or "/") + ("?" + url.query if url.query else ""), body=body,
                           headers={"Authorization": f"vapid t={token}, k={public}", "TTL": str(max(0, ttl)),
                                    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream"})
        response = connection.getresponse()
        return response.status  # No leer ni registrar cuerpos controlados por el proveedor.
    finally:
        connection.close()
