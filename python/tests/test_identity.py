import asyncio
import os
import smtplib
import time
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from argon2 import extract_parameters
from fastapi import Request
from fastapi.testclient import TestClient
from mnemonic import Mnemonic

from chat.app import create_app
from chat.config import Settings
from chat.errors import APIError
from chat.identity_crypto import Tokens, hash_phrase, new_phrase, opaque_token, token_hash, verify_phrase
from chat.identity_redis import client_ip
from chat.mailer import SMTPMailer


def signed(*, changes: dict[str, object] | None = None, headers: dict[str, object] | None = None,
           bootstrap: bool = False) -> str:
    now = int(time.time())
    claims = {"sub": str(uuid4()), "sid": str(uuid4()), "iat": now, "nbf": now,
              "exp": now + (300 if bootstrap else 86400), "jti": str(uuid4()),
              "iss": "main-app" if bootstrap else "chat-api", "aud": "chat-api" if bootstrap else "chat-client"}
    claims.update(changes or {})
    if bootstrap:
        claims.pop("sid")
    key = (Path(os.environ["BOOTSTRAP_PUBLIC_KEYS_DIR"]).parent / "bootstrap_local_private.pem"
           if bootstrap else Path(os.environ["CHAT_JWT_PRIVATE_KEY_FILE"]))
    return jwt.encode(claims, key.read_bytes(), algorithm="EdDSA",
                      headers={"kid": "main-local-1" if bootstrap else "chat-local-1", "typ": "JWT", **(headers or {})})


def test_bip39_and_exact_argon2_parameters_and_nfkd() -> None:
    phrase = new_phrase()
    assert len(phrase.split()) == 24
    assert Mnemonic("english").check(phrase)
    encoded = hash_phrase(phrase)
    assert encoded.startswith("$argon2id$")
    parameters = extract_parameters(encoded)
    assert (parameters.memory_cost, parameters.time_cost, parameters.parallelism,
            parameters.salt_len, parameters.hash_len) == (65536, 3, 1, 16, 32)
    fullwidth = "".join(chr(ord(char) + 0xFEE0) if "a" <= char <= "z" else char for char in phrase)
    assert verify_phrase(encoded, "\t" + fullwidth.replace(" ", " \n ") + " ")
    assert not verify_phrase(encoded, new_phrase())
    assert encoded != hash_phrase(phrase)


def test_token_hash_is_binary_and_strict() -> None:
    token = opaque_token()
    assert len(token) == 43 and len(token_hash(token)) == 32
    for invalid in (token + "=", "!" * 43, "A" * 42 + "B", "é" * 43, ""):
        with pytest.raises(APIError):
            token_hash(invalid)


def test_access_claims_and_immediate_key_removal(local_settings: Settings) -> None:
    tokens = Tokens(local_settings)
    user, sid = uuid4(), uuid4()
    access = tokens.access(user, sid)
    claims = tokens.verify(access)
    assert (claims.user_id, claims.session_id) == (user, sid)
    Path(os.environ["CHAT_JWT_PUBLIC_KEYS_DIR"], "chat-local-1.pem").unlink()
    with pytest.raises(APIError, match="TOKEN_INVALID"):
        tokens.verify(access)


@pytest.mark.parametrize("changes,headers", [
    ({"aud": "other"}, {}), ({"iss": "main-app"}, {}), ({"sub": "bad"}, {}),
    ({"sid": None}, {}), ({"jti": "invalid"}, {}), ({"iat": True}, {}),
    ({"aud": ["chat-client"]}, {}), ({}, {"kid": "unknown"}), ({}, {"kid": "../chat-local-1"}),
    ({}, {"typ": "JWE"}), ({}, {"crit": ["unknown"]}),
])
def test_invalid_jwt(local_settings: Settings, changes: dict[str, object], headers: dict[str, object]) -> None:
    with pytest.raises(APIError):
        Tokens(local_settings).verify(signed(changes=changes, headers=headers))


def test_time_algorithm_substitution_and_bootstrap_isolation(local_settings: Settings) -> None:
    tokens = Tokens(local_settings)
    now = int(time.time())
    for changes in ({"iat": now + 60, "nbf": now + 60, "exp": now + 86460},
                    {"nbf": now + 60}, {"iat": now - 86500, "exp": now - 100}):
        with pytest.raises(APIError):
            tokens.verify(signed(changes=changes))
    for algorithm in ("none", "HS256"):
        forged = jwt.encode({"sub": str(uuid4())}, "" if algorithm == "none" else "x" * 32,
                            algorithm=algorithm, headers={"kid": "chat-local-1"})
        with pytest.raises(APIError):
            tokens.verify(forged)
    assert tokens.verify(signed(bootstrap=True), bootstrap=True).session_id is None
    # Fijar ambos extremos: si signed() cruza un segundo, un TTL de 301 s
    # calculado con dos relojes distintos podía convertirse accidentalmente en 300.
    for bootstrap in (signed(), signed(bootstrap=True, changes={"iat": now, "nbf": now, "exp": now + 301})):
        with pytest.raises(APIError):
            tokens.verify(bootstrap, bootstrap=True)


def test_strict_api_errors_body_limit_and_no_secret_echo(local_settings: Settings,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(create_app(local_settings)) as client:
        extra = client.post("/api/v1/users/register", json={"nick": "Valid", "email": "a@example.com",
                                                         "password": "TOP_SECRET"})
        assert extra.status_code == 422
        assert extra.json()["error"]["code"] == "UNKNOWN_FIELD"
        assert extra.headers["X-Request-ID"] == extra.json()["error"]["request_id"]
        assert extra.headers["Cache-Control"] == "no-store"
        assert "TOP_SECRET" not in extra.text
        assert client.post("/api/v1/users/register", content="{",
                           headers={"Content-Type": "application/json"}).status_code == 400
        assert client.post("/api/v1/users/register", content="x" * 131073).status_code == 413
        assert client.get("/api/v1/users/me").json()["error"]["code"] == "AUTH_REQUIRED"
    assert "TOP_SECRET" not in capsys.readouterr().out


def test_forwarded_ip_only_from_trusted_proxy(local_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chat.identity_redis.socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("10.1.1.2", 0))])
    for peer, expected in (("10.1.1.2", "203.0.113.1"), ("10.1.1.3", "10.1.1.3")):
        request = Request({"type": "http", "client": (peer, 1),
                           "headers": [(b"x-chat-client-ip", b"203.0.113.1"),
                                       (b"x-forwarded-for", b"198.51.100.1")]})
        assert asyncio.run(client_ip(request, local_settings)) == expected


def test_smtp_uses_tls_and_does_not_expose_provider_errors(local_settings: Settings,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    connection = MagicMock()
    connection.__enter__.return_value = connection
    smtp = MagicMock(return_value=connection)
    monkeypatch.setattr("chat.mailer.smtplib.SMTP", smtp)
    asyncio.run(SMTPMailer(local_settings.email_from).verification("a@example.com", "test-token"))
    connection.starttls.assert_called_once()
    message = connection.send_message.call_args.args[0]
    assert "test-token" in message.get_content()
    connection.starttls.side_effect = smtplib.SMTPException("SECRET_PROVIDER_INFORMATION")
    with pytest.raises(APIError) as error:
        asyncio.run(SMTPMailer(local_settings.email_from).verification("a@example.com", "test-token"))
    assert error.value.status == 503 and "SECRET_PROVIDER" not in str(error.value)
