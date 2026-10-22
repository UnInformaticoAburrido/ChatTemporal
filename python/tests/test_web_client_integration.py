"""E2E opt-in: Chromium → assets/API/WS reales → PostgreSQL/Redis aislados.

Solo fixture local. Los controles, buzón y emisor NO forman parte de Chat API.
"""

import asyncio
import contextlib
import hmac
import os
import secrets
import shutil
import socket
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from test_identity_integration import Mailbox

from chat.app import create_app
from chat.config import RateRule, Settings, read_secret
from chat.identity_redis import redis_connection
from chat.worker import maintenance
from chat_client.bootstrap import BootstrapIssuer

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test"
    or os.environ.get("RUN_WEB_E2E") != "1",
    reason="Requiere RUN_WEB_E2E=1, servicios aislados, Node y Chromium de Playwright",
)]
CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob:; "
       "connect-src 'self'; media-src 'self' blob:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
HEADERS = {"Cache-Control": "no-store", "Content-Security-Policy": CSP,
           "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


def listener() -> socket.socket:
    channel = socket.socket()
    channel.bind(("127.0.0.1", 0))
    return channel


@pytest.fixture
def web_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Settings:
    database, redis = os.environ["DATABASE_URL_FILE"], os.environ["REDIS_URL_FILE"]
    settings = request.getfixturevalue("local_settings")
    monkeypatch.setenv("DATABASE_URL_FILE", database)
    monkeypatch.setenv("REDIS_URL_FILE", redis)
    monkeypatch.setenv("APP_ENV", "test")
    return settings


def test_browser_with_real_api_ws_database_and_redis(web_environment: Settings) -> None:
    with psycopg.connect(read_secret("DATABASE_URL")) as db:
        assert db.execute("SELECT count(*) FROM users").fetchone() == (0,), "Requiere BD vacía de pruebas"
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0007_recovery_push",)
    node = shutil.which("node")
    assert node and (ROOT / "web/node_modules/@playwright/test/cli.js").is_file(), "Instalar dependencias web"

    async def run() -> None:
        chat_socket, main_socket = listener(), listener()
        chat_origin = f"http://127.0.0.1:{chat_socket.getsockname()[1]}"
        main_origin = f"http://127.0.0.1:{main_socket.getsockname()[1]}"
        settings = Settings.model_validate({**web_environment.model_dump(), "allowed_origins": [chat_origin],
            "bootstrap_web_origins": [main_origin], "trusted_proxy_host": "", "cleanup_interval_seconds": 2})
        settings.rate_limits.register_ip = RateRule(limit=100, seconds=3600)
        mailbox = Mailbox()
        app = create_app(settings, mailer=mailbox)
        main = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        control = secrets.token_urlsafe(32)
        main_sessions: dict[str, UUID] = {}
        issuer = BootstrapIssuer(Path(os.environ["BOOTSTRAP_PUBLIC_KEYS_DIR"]).parent
                                 / "bootstrap_local_private.pem", "main-local-1", 60)

        def authorized(request: Request) -> bool:
            return bool(request.client and request.client.host == "127.0.0.1"
                        and hmac.compare_digest(request.headers.get("X-Chat-Test-Control", ""), control))

        @app.post("/__test__/mail")
        async def mail(request: Request) -> Response:
            if not authorized(request):
                return Response(status_code=404)
            email = (await request.json())["email"]
            token = next((token for recipient, token in reversed(mailbox.messages) if recipient == email), None)
            return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})

        @app.post("/__test__/conversation")
        async def inspect_conversation(request: Request) -> Response:
            if not authorized(request):
                return Response(status_code=404)
            data = await request.json()
            identifier = UUID(data["id"])
            async with await psycopg.AsyncConnection.connect(read_secret("DATABASE_URL")) as db:
                room = await (await db.execute("SELECT mode,status FROM conversations WHERE id=%s", (identifier,))).fetchone()
                rows = await (await db.execute("""SELECT m.ciphertext,m.crypto_meta FROM messages m
                    JOIN message_events e ON e.id=m.id WHERE e.conversation_id=%s""", (identifier,))).fetchall()
                events = await (await db.execute("SELECT count(*) FROM message_events WHERE conversation_id=%s", (identifier,))).fetchone()
                deliveries = await (await db.execute("""SELECT d.status FROM message_deliveries d
                    JOIN message_events e ON e.id=d.message_id WHERE e.conversation_id=%s""", (identifier,))).fetchall()
                if data.get("expire_content"):
                    await db.execute("""UPDATE messages m SET content_expires_at=clock_timestamp()-interval '1 second'
                        FROM message_events e WHERE e.id=m.id AND e.conversation_id=%s""", (identifier,))
            markers = [text.encode() for text in data.get("markers", [])]
            leaked = any(marker in bytes(field) for row in rows for field in row for marker in markers)
            return JSONResponse({"mode": room[0] if room else None, "status": room[1] if room else None,
                                 "stored": len(rows), "events": events[0] if events else 0,
                                 "deliveries": [row[0] for row in deliveries], "plaintext_found": leaked})

        @app.post("/__test__/disconnect")
        async def disconnect(request: Request) -> Response:
            if not authorized(request):
                return Response(status_code=404)
            for websocket in list(app.state.sockets):
                with contextlib.suppress(RuntimeError):
                    await websocket.close(code=1001)
            return Response(status_code=204)

        @app.post("/__test__/transfer")
        async def inspect_transfer(request: Request) -> Response:
            if not authorized(request):
                return Response(status_code=404)
            data = await request.json()
            identifier, user = UUID(data["id"]), UUID(data["user"])
            async with redis_connection() as redis:
                blob = await redis.get(f"key_transfer:{identifier}")
                meta = await redis.get(f"key_transfer_meta:{identifier}")
            async with await psycopg.AsyncConnection.connect(read_secret("DATABASE_URL")) as db:
                active = await (await db.execute("""SELECT count(*) FROM auth_sessions WHERE user_id=%s
                    AND revoked_at IS NULL AND expires_at>clock_timestamp()""", (user,))).fetchone()
            return JSONResponse({"blob": blob is not None, "meta": meta is not None,
                "active_sessions": active[0] if active else 0})

        # Misma lista pública que Caddy, sin montar el checkout como StaticFiles.
        paths = next(line.split()[2:] for line in (ROOT / "caddy/routes.caddy").read_text().splitlines()
                     if line.startswith("@web path "))
        paths.extend("/" + path.relative_to(ROOT / "web/public").as_posix()
                     for path in (ROOT / "web/public/assets").rglob("*") if path.is_file())

        @app.get("/{asset:path}")
        async def asset(asset: str) -> Response:
            if "/" + asset not in paths:
                return Response(status_code=404)
            return FileResponse(ROOT / "web/public" / (asset or "index.html"), headers=HEADERS)

        @main.post("/__test__/bind")
        async def bind_main_session(request: Request) -> Response:
            if not authorized(request):
                return Response(status_code=404)
            user = UUID((await request.json())["user"])
            async with await psycopg.AsyncConnection.connect(read_secret("DATABASE_URL")) as db:
                assert await (await db.execute("SELECT 1 FROM users WHERE id=%s AND email_verified", (user,))).fetchone()
            session = secrets.token_urlsafe(24)
            main_sessions[session] = user
            response = Response(status_code=204)
            # Solo sesión del emisor sintético; nunca access/refresh de chat.
            response.set_cookie("e2e_main_session", session, httponly=True, samesite="strict", path="/bootstrap")
            return response

        @main.post("/bootstrap")
        async def issue(request: Request) -> Response:
            user = main_sessions.get(request.cookies.get("e2e_main_session", ""))
            if not user or request.headers.get("Origin") != main_origin:
                return Response(status_code=403)
            return JSONResponse({"bootstrap_token": issuer.issue(user)}, headers={"Cache-Control": "no-store"})

        @main.get("/{asset:path}")
        async def main_asset(asset: str) -> Response:
            if asset == "main-app.js":
                source = (ROOT / "web/tests/fixtures/main-app.js").read_text().replace("http://127.0.0.1:4173", chat_origin)
                return Response(source, media_type="text/javascript", headers=HEADERS)
            files = {"": ROOT / "web/tests/fixtures/main-app.html",
                     "launch-chat.mjs": ROOT / "integrations/main-app/launch-chat.mjs"}
            if asset not in files:
                return Response(status_code=404)
            return FileResponse(files[asset], headers=HEADERS)

        servers = [uvicorn.Server(uvicorn.Config(application, log_config=None, log_level="critical",
                   access_log=False, lifespan="off", ws="websockets", timeout_graceful_shutdown=2))
                   for application in (app, main)]
        tasks = [asyncio.create_task(instance.serve(sockets=[channel]))
                 for instance, channel in zip(servers, (chat_socket, main_socket), strict=True)]
        stopping = asyncio.Event()
        worker = asyncio.create_task(maintenance(settings, stopping)) # Sin envíos Push externos.
        process = None
        try:
            async with asyncio.timeout(10):
                while not all(instance.started for instance in servers):
                    for task in tasks:
                        if task.done():
                            task.result()
                    await asyncio.sleep(0.01)
            environment = {**os.environ, "CHAT_E2E_BASE_URL": chat_origin, "CHAT_E2E_MAIN_URL": main_origin,
                           "CHAT_E2E_CONTROL": control}
            process = await asyncio.create_subprocess_exec(node, "node_modules/@playwright/test/cli.js", "test",
                "--config=playwright.integration.config.mjs", cwd=ROOT / "web", env=environment,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            async with asyncio.timeout(600):
                output, _ = await process.communicate()
            print(output.decode(), end="")
            assert process.returncode == 0, "Falló la suite web E2E local (ver salida del navegador)"
        finally:
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            stopping.set()
            await asyncio.wait_for(worker, 5)
            for instance in servers:
                instance.should_exit = True
            await asyncio.wait_for(asyncio.gather(*tasks), 10)
            chat_socket.close()
            main_socket.close()
            # Solo cuentas creadas por el buzón de esta fixture, sin borrar tablas.
            emails = list({email for email, _ in mailbox.messages})
            async with await psycopg.AsyncConnection.connect(read_secret("DATABASE_URL")) as db:
                await db.execute("""DELETE FROM conversations WHERE id IN (SELECT conversation_id
                    FROM conversation_members WHERE user_id IN (SELECT id FROM users WHERE email=ANY(%s)))""", (emails,))
                await db.execute("DELETE FROM users WHERE email=ANY(%s)", (emails,))

    asyncio.run(run())
