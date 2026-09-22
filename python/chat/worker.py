"""Purga y reconciliación N5. El cierre de votos corresponde a N6."""

import asyncio
import signal
import time

import psycopg
from redis.asyncio import Redis

from chat.config import load_settings, read_secret
from chat.logging import event
from chat.reconciliation import reconcile_once

# DUD-02: §18 borra sesiones al expirar; contradice la auditoría de §28.3.
# Respuesta del usuario: no solicitada; §23 establece prioridad de regla específica.
# Se conservan sesión e historial 30 días después del último vencimiento de la familia.
# DEC-29: considerar también sesiones con vencimiento posterior a sus tokens.
CLEANUP_SQL = """
DELETE FROM messages WHERE content_expires_at <= now();
DELETE FROM message_events WHERE expires_at <= now();
DELETE FROM email_verification_tokens WHERE expires_at <= now() OR used_at IS NOT NULL;
DELETE FROM auth_refresh_tokens t
WHERE t.expires_at < now() - interval '30 days'
  AND NOT EXISTS (
      SELECT 1 FROM auth_refresh_tokens other
      WHERE other.token_family_id = t.token_family_id
        AND other.expires_at >= now() - interval '30 days'
  )
  AND NOT EXISTS (
      SELECT 1 FROM auth_sessions family
      WHERE family.token_family_id = t.token_family_id
        AND family.expires_at >= now() - interval '30 days'
  );
DELETE FROM auth_sessions s
WHERE s.expires_at < now() - interval '30 days'
  AND NOT EXISTS (SELECT 1 FROM auth_refresh_tokens t WHERE t.session_id = s.id);
"""


async def cleanup_once() -> None:
    async with await psycopg.AsyncConnection.connect(
        read_secret("DATABASE_URL"), connect_timeout=3
    ) as connection:
        # DEC-15: lock transaccional evita dos purgas simultáneas durante un despliegue.
        row = await (await connection.execute("SELECT pg_try_advisory_xact_lock(6734512)")).fetchone()
        if row and row[0]:
            await connection.execute("SET LOCAL statement_timeout = '10s'")
            await connection.execute(CLEANUP_SQL, prepare=False)
        else:
            return
    async with Redis.from_url(read_secret("REDIS_URL"), socket_timeout=2) as redis:
        await redis.set("maintenance:last_success", str(time.time()), ex=120)


async def run() -> None:
    settings = load_settings()
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopping.set)
    while not stopping.is_set():
        try:
            await cleanup_once()
        except Exception:
            event("cleanup_failed", service="worker", level="ERROR", error_code="TEMPORARY_UNAVAILABLE")
        try:
            async with asyncio.timeout(settings.cleanup_interval_seconds):
                while not stopping.is_set():
                    try:
                        await reconcile_once(settings)
                    except Exception:
                        event("reconcile_failed", service="worker", level="ERROR", error_code="TEMPORARY_UNAVAILABLE")
                    try:
                        await asyncio.wait_for(stopping.wait(), timeout=1)
                    except TimeoutError:
                        pass
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(run())
