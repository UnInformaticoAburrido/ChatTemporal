"""Smoke HTTPS/WSS real por Caddy; solo proyecto aislado, sin correo/ACME."""

import asyncio
import json
import os
import ssl
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from load_chat import Pair, benchmark  # noqa: E402

from chat.config import load_settings, read_secret  # noqa: E402
from chat.identity_crypto import Tokens  # noqa: E402
from chat.protocol import encode_binary  # noqa: E402
from chat_client.crypto import KeyPair  # noqa: E402

ORIGIN = "https://caddy:8443"


async def run() -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("RUN_INTEGRATION") != "1":
        raise RuntimeError("Requiere el runner aislado de integración")
    settings = load_settings()
    tls = ssl.create_default_context(cafile="/evidence/test-root.crt")
    users, sessions = [uuid4() for _ in range(4)], [uuid4() for _ in range(4)]
    keys = [KeyPair.generate() for _ in users]
    tokens = [Tokens(settings).access(user, sid) for user, sid in zip(users, sessions, strict=True)]
    dsn = read_secret("DATABASE_URL")
    with psycopg.connect(dsn) as db:
        for user, sid, key in zip(users, sessions, keys, strict=True):
            db.execute("""INSERT INTO users(id,nick,email,memory_hash,email_verified)
                VALUES (%s,%s,%s,'proxy-test',true)""", (user, "tls_" + user.hex[:12], user.hex + "@example.com"))
            db.execute("""INSERT INTO auth_sessions(id,user_id,refresh_token_hash,token_family_id,expires_at)
                VALUES (%s,%s,%s,%s,now()+interval '1 hour')""", (sid, user, uuid4().hex, uuid4()))
            db.execute("INSERT INTO user_keys(user_id,public_key) VALUES (%s,%s)", (user, encode_binary(key.public_key)))
    try:
        async with httpx.AsyncClient(base_url=ORIGIN, verify=tls, trust_env=False, timeout=10) as api:
            print("Proxy: salud y rutas privadas.", flush=True)
            assert (await api.get("/health/live")).status_code == 200
            for path in ("/metrics", "/health/ready", "/docs", "/openapi.json"):
                assert (await api.get(path)).status_code == 404
            assert (await api.get("/api/v1/users/me")).status_code == 401
            print("Proxy: redirección HTTP.", flush=True)
            redirect = await api.get("http://caddy:8080/health/live")
            assert redirect.status_code == 308 and redirect.headers["location"] == ORIGIN + "/health/live"
            print("Proxy: CORS y confianza TLS.", flush=True)
            denied = await api.options("/api/v1/users/me", headers={"Origin": "https://evil.invalid",
                                        "Access-Control-Request-Method": "GET"})
            assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers
            # La CA del ensayo es necesaria: nunca usar verify=False.
            async with httpx.AsyncClient(trust_env=False, timeout=5) as untrusted:
                try:
                    await untrusted.get(ORIGIN + "/health/live")
                except httpx.ConnectError:
                    pass
                else:
                    raise AssertionError("La CA interna no debe estar en el trust store por defecto")
            pairs, votes = [], []
            print("Proxy: invitaciones y votaciones.", flush=True)
            for index, mode in ((0, "stored"), (2, "ephemeral")):
                host = {"Authorization": "Bearer " + tokens[index]}
                guest = {"Authorization": "Bearer " + tokens[index + 1]}
                response = await api.get("/api/v1/invitations/me", headers=host)
                assert response.status_code == 200
                redeemed = await api.post("/api/v1/invitations/redeem", headers=guest,
                                          json={"code": response.json()[mode + "_code"]})
                assert redeemed.status_code == 201
                conversation = redeemed.json()["id"]
                accepted = await api.post(f"/api/v1/conversations/{conversation}/accept", headers=host)
                assert accepted.status_code == 200
                votes.append((accepted.json()["vote"]["id"], host))
                pairs.append(Pair(UUID(conversation), mode, tokens[index + 1], tokens[index],
                                  keys[index + 1], keys[index]))
            # Ventana normativa real de 30 s, resuelta por el worker real.
            async with asyncio.timeout(45):
                for identifier, host in votes:
                    while True:
                        response = await api.get(f"/api/v1/votes/{identifier}", headers=host)
                        assert response.status_code == 200
                        if response.json()["status"] != "open":
                            break
                        await asyncio.sleep(1)
        print("Proxy: mensajería cifrada WSS.", flush=True)
        result = await benchmark(ORIGIN, pairs, expected_peak=2, duration=1, interval=.25, tls=tls)
        Path("/evidence/proxy-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
        assert result["passed"], "Fallo del recorrido cifrado HTTPS/WSS"
        print("Caddy: TLS verificado, redirect, rutas privadas, CORS y stored/ephemeral WSS correctos.")
    finally:
        with psycopg.connect(dsn) as db:
            db.execute("""DELETE FROM conversations WHERE id IN
                (SELECT conversation_id FROM conversation_members WHERE user_id=ANY(%s))""", (users,))
            db.execute("DELETE FROM users WHERE id=ANY(%s)", (users,))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as error:
        # Nunca imprimir DSN, tokens ni repr de respuestas durante un fallo.
        print(json.dumps({"proxy_smoke": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
