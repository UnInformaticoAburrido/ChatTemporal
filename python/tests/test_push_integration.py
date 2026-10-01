"""Cola Push, privacidad y transporte HTTPS cifrado con receptor local de prueba."""

import asyncio
import contextlib
import hmac
import json
import os
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID
from test_conversations_integration import client, contact
from test_conversations_integration import n4 as n4
from test_identity_integration import bootstrap
from test_websocket_integration import control, open_socket, received, sending, server

from chat.config import read_secret
from chat.protocol import decode_binary, encode_binary
from chat.push import push_once
from chat.web_push import send_push

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


def subscription(endpoint="https://push.example.test/device") -> dict:
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": endpoint, "p256dh": encode_binary(key), "auth_secret": encode_binary(b"a" * 16)}


def test_push_subscription_upsert_ownership_revocation_and_validation(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as owner, client(settings, tokens[1]) as outsider:
            data = subscription()
            first = await owner.post("/api/v1/push/subscriptions", json=data)
            assert first.status_code == 201, first.text
            identifier = first.json()["id"]
            data["auth_secret"] = encode_binary(b"b" * 16)
            assert (await owner.post("/api/v1/push/subscriptions", json=data)).json() == first.json()
            assert (await outsider.post("/api/v1/push/subscriptions", json=data)).status_code == 409
            path = f"/api/v1/push/subscriptions/{identifier}"
            assert (await outsider.delete(path)).status_code == 404
            assert (await owner.delete(path)).status_code == 204
            assert (await owner.delete(path)).status_code == 204
            assert (await owner.post("/api/v1/push/subscriptions", json=data)).json() == first.json()
            for field, value in (("endpoint", "https://127.0.0.1/secrets"), ("p256dh", encode_binary(b"x" * 65)),
                                 ("auth_secret", "bad"), ("secret", "extra")):
                assert (await owner.post("/api/v1/push/subscriptions", json={**data, field: value})).status_code == 422
    asyncio.run(run())


def test_stored_push_after_acceptance_retry_receipts_and_generic_payload(n4: tuple, monkeypatch, capsys) -> None:
    settings, _, _, tokens = n4
    calls = []

    def capture(settings, endpoint, public, auth, payload, ttl):
        calls.append((endpoint, json.loads(payload), ttl))
        return 503 if endpoint.endswith("retry") and len(calls) <= 2 else 201

    monkeypatch.setattr("chat.push.send_push", capture)

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            for suffix in ("success", "retry"):
                assert (await host.post("/api/v1/push/subscriptions", json=subscription("https://push.example.test/" + suffix))).status_code == 201
            conversation = (await contact(host, guest, "stored"))["id"]
            ws = await open_socket(stack, url, guest)
            message = sending(conversation)
            await ws.send(json.dumps(message))
            identifier = message["payload"]["message_id"]
            # REST observa commit sin depender de un receptor conectado.
            async with asyncio.timeout(3):
                while (await guest.get(f"/api/v1/messages/{identifier}/status")).status_code != 200:
                    await asyncio.sleep(0.01)
            assert await push_once(settings) == 1
            assert len(calls) == 2
            for _, payload, ttl in calls:
                assert payload == {"event_type": "message.new", "conversation_id": conversation, "message_id": identifier}
                assert 0 < ttl <= 86400
            await ws.send(json.dumps(message))
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT count(*) FROM push_jobs WHERE message_id=%s", (identifier,)).fetchone() == (1,)
                db.execute("UPDATE push_jobs SET next_attempt_at=clock_timestamp() WHERE message_id=%s", (identifier,))
            await push_once(settings)
            assert len(calls) == 3 and calls[-1][0].endswith("retry")
            assert await push_once(settings) == 0
            logs = capsys.readouterr().out
            assert message["payload"]["ciphertext"] not in logs and "push.example.test" not in logs
    asyncio.run(run())


@pytest.mark.parametrize("status", [404, 410])
def test_expired_push_subscription_revoked(n4: tuple, monkeypatch, status: int) -> None:
    settings, _, _, tokens = n4
    monkeypatch.setattr("chat.push.send_push", lambda *args: status)

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            identifier = (await host.post("/api/v1/push/subscriptions", json=subscription())).json()["id"]
            conversation = (await contact(host, guest, "stored"))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            await a.send(json.dumps(sending(conversation)))
            await received(b, "message.new")
            await push_once(settings)
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT revoked_at IS NOT NULL FROM web_push_subscriptions WHERE id=%s", (identifier,)).fetchone() == (True,)
    asyncio.run(run())


def test_ephemeral_push_only_offline_offers_and_no_ciphertext(n4: tuple, monkeypatch) -> None:
    settings, _, _, tokens = n4
    calls = []

    def capture(settings, endpoint, public, auth, payload, ttl):
        calls.append(json.loads(payload))
        assert 0 < ttl <= 60
        return 201

    monkeypatch.setattr("chat.push.send_push", capture)

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            await host.post("/api/v1/push/subscriptions", json=subscription())
            conversation = (await contact(host, guest))["id"]
            a = await open_socket(stack, url, guest)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await control(a, "message.offer", conversation, identifier)
            async with asyncio.timeout(3):
                while True:
                    with psycopg.connect(read_secret("DATABASE_URL")) as db:
                        if db.execute("SELECT count(*) FROM push_jobs WHERE message_id=%s", (identifier,)).fetchone() == (1,):
                            break
                    await asyncio.sleep(0.01)
            await push_once(settings)
            assert calls == [{"event_type": "message.offer", "conversation_id": conversation, "message_id": identifier}]
            b = await open_socket(stack, url, host)
            await received(b, "message.offer")
            another = sending(conversation)["payload"]["message_id"]
            await control(a, "message.offer", conversation, another)
            await received(b, "message.offer")
            assert await push_once(settings) == 0
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT count(*) FROM message_events WHERE conversation_id=%s", (conversation,)).fetchone() == (0,)
    asyncio.run(run())


def test_push_does_not_reach_previous_session_after_exchange(n4: tuple, monkeypatch) -> None:
    settings, users, _, tokens = n4
    calls = []
    monkeypatch.setattr("chat.push.send_push", lambda *args: calls.append(args) or 201)

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            await host.post("/api/v1/push/subscriptions", json=subscription())
            conversation = (await contact(host, guest, "stored"))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            await a.send(json.dumps(sending(conversation)))
            await received(b, "message.new")
            assert (await host.post("/api/v1/auth/exchange", json={"bootstrap_token": bootstrap(users[0])})).status_code == 200
            await push_once(settings)
            assert calls == []
    asyncio.run(run())


def test_real_https_vapid_and_push_decryption(n4: tuple, tmp_path, monkeypatch) -> None:
    settings, _, _, _ = n4
    tls_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "push.example.test")])
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(tls_key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
                   .not_valid_after(datetime.now(UTC) + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("push.example.test")]), critical=False)
                   .sign(tls_key, hashes.SHA256()))
    cert, key = tmp_path / "push.crt", tmp_path / "push.key"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(tls_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    http.socket = server_context.wrap_socket(http.socket, server_side=True)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(cert))
    connect = socket.create_connection
    # Solo el test enruta la IP pública ya comprobada a un puerto TLS local.
    monkeypatch.setattr("chat.web_push.public_address", lambda host: "8.8.8.8")
    monkeypatch.setattr("chat.web_push.ssl.create_default_context", lambda: context)
    monkeypatch.setattr("chat.web_push.socket.create_connection", lambda destination, **kwargs:
                        connect(http.server_address if destination == ("8.8.8.8", 443) else destination, **kwargs))
    receiver = ec.generate_private_key(ec.SECP256R1())
    public = receiver.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"test-auth-secret"
    payload = b'{"event_type":"message.new"}'
    try:
        assert send_push(settings, "https://push.example.test/device", encode_binary(public), encode_binary(auth), payload, 60) == 201
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
    headers, body = requests[0]
    assert headers["Content-Encoding"] == "aes128gcm" and headers["TTL"] == "60"
    authorization = headers["Authorization"]
    token, vapid_public = authorization.removeprefix("vapid t=").split(", k=")
    claims = jwt.decode(token, ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),
                        decode_binary(vapid_public, minimum=65, maximum=65)), algorithms=["ES256"], audience="https://push.example.test")
    assert claims["sub"] == settings.vapid_subject
    sender_public = body[21:86]
    shared = receiver.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public))
    # Receptor independiente: HKDF expandido con HMAC según RFC 8291.
    prk = hmac.digest(auth, shared, sha256)
    ikm = hmac.digest(prk, b"WebPush: info\0" + public + sender_public + b"\x01", sha256)
    prk = hmac.digest(body[:16], ikm, sha256)
    cek = hmac.digest(prk, b"Content-Encoding: aes128gcm\0\x01", sha256)[:16]
    nonce = hmac.digest(prk, b"Content-Encoding: nonce\0\x01", sha256)[:12]
    assert AESGCM(cek).decrypt(nonce, body[86:], None) == payload + b"\x02"
    assert payload not in body and int.from_bytes(body[16:20], "big") == 4096
