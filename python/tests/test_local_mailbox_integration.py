"""Registro y verificación con SMTP real, solo en el proyecto Docker de pruebas."""

import asyncio
import os
import re
from uuid import uuid4

import httpx
import psycopg
import pytest

from chat.app import create_app
from chat.config import Settings, read_secret

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test"
    or os.environ.get("CHAT_TEST_MAILPIT_HTTP") != "http://mailpit:8025",
    reason="Requiere PostgreSQL/Redis/Mailpit aislados del runner Docker",
)]


def test_registration_and_verification_through_real_local_smtp(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, redis = os.environ["DATABASE_URL_FILE"], os.environ["REDIS_URL_FILE"]
    settings: Settings = request.getfixturevalue("local_settings")
    monkeypatch.setenv("DATABASE_URL_FILE", database)
    monkeypatch.setenv("REDIS_URL_FILE", redis)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CHAT_LOCAL_MAILBOX", "1")
    prefix = "mail_" + uuid4().hex[:12]
    email = prefix + "@example.com"

    async def run() -> None:
        peer = f"198.18.{uuid4().int % 250}.{uuid4().int % 250}"
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(
                app=create_app(settings), client=(peer, 1)), base_url="http://test") as client,
            httpx.AsyncClient(base_url="http://mailpit:8025", timeout=5) as mailbox,
        ):
            response = await client.post("/api/v1/users/register", json={"nick": prefix, "email": email})
            # No incluir en los errores cuerpo, mnemonic, tokens ni contenido del correo.
            assert response.status_code == 201
            registered = response.json()
            assert len(registered["recovery_mnemonic"].split()) == 24
            assert not registered["user"]["email_verified"]
            client.headers["Authorization"] = "Bearer " + registered["tokens"]["access_token"]
            assert (await client.post("/api/v1/auth/ws-ticket")).status_code == 403

            message_id = None
            for _ in range(30):
                listing = await mailbox.get("/api/v1/messages")
                assert listing.status_code == 200
                matches = [message for message in listing.json()["messages"]
                           if any(recipient["Address"] == email for recipient in message["To"])]
                if matches:
                    assert len(matches) == 1
                    message_id = matches[0]["ID"]
                    break
                await asyncio.sleep(0.1)
            assert message_id is not None
            message = await mailbox.get("/api/v1/message/" + message_id)
            assert message.status_code == 200
            matches = re.findall(r"\b[A-Za-z0-9_-]{43}\b", message.json()["Text"])
            assert len(matches) == 1
            code = matches[0]
            assert code not in response.text
            verified = await client.post("/api/v1/users/verify-email", json={"token": code})
            assert verified.status_code == 204
            assert (await client.get("/api/v1/users/me")).json()["email_verified"]
            assert (await client.post("/api/v1/auth/ws-ticket")).status_code == 201
            assert (await client.post("/api/v1/users/verify-email", json={"token": code})).status_code == 401
            assert (await mailbox.request("DELETE", "/api/v1/messages", json={"IDs": [message_id]})).status_code == 200

    try:
        asyncio.run(run())
    finally:
        with psycopg.connect(read_secret("DATABASE_URL")) as connection:
            connection.execute("DELETE FROM users WHERE email = %s", (email,))
