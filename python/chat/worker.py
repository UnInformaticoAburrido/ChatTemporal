"""Purga, reconciliación N5 y resolución automática de votaciones N6."""

import asyncio
import signal
import time

import psycopg
from prometheus_client import CollectorRegistry, ProcessCollector, start_http_server
from redis.asyncio import Redis

from chat.config import Settings, load_settings, read_secret
from chat.logging import event
from chat.metrics import PUSH, TIMEOUTS, WORKER_ERRORS
from chat.push import push_once
from chat.reconciliation import reconcile_once
from chat.voting import resolve_votes_once

# DUD-02: §18 borra sesiones al expirar; contradice la auditoría de §28.3.
# Respuesta del usuario: no solicitada; §23 establece prioridad de regla específica.
# Se conservan sesión e historial 30 días después del último vencimiento de la familia.
# DEC-29: considerar también sesiones con vencimiento posterior a sus tokens.
CLEANUP_SQL = """
DELETE FROM transfer_upload_grants WHERE expires_at<=clock_timestamp();
DELETE FROM push_jobs WHERE expires_at<=clock_timestamp();
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
    registry = CollectorRegistry()
    for metric in (PUSH, TIMEOUTS, WORKER_ERRORS):
        registry.register(metric)
    ProcessCollector(registry=registry)
    start_http_server(8001, registry=registry)
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopping.set)
    push_task = asyncio.create_task(push_loop(settings, stopping))
    try:
        await maintenance(settings, stopping)
    finally:
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass


async def push_loop(settings: Settings, stopping: asyncio.Event) -> None:
    # Un proveedor lento no bloquea votaciones, heartbeat de purga ni deliveries.
    while not stopping.is_set():
        try:
            await push_once(settings)
        except Exception:
            WORKER_ERRORS.labels("push_worker").inc()
            event("push_worker_failed", service="worker", level="ERROR", error_code="TEMPORARY_UNAVAILABLE")
        try:
            await asyncio.wait_for(stopping.wait(), timeout=1)
        except TimeoutError:
            pass


async def maintenance(settings: Settings, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            await cleanup_once()
        except Exception:
            WORKER_ERRORS.labels("cleanup").inc()
            event("cleanup_failed", service="worker", level="ERROR", error_code="TEMPORARY_UNAVAILABLE")
        try:
            async with asyncio.timeout(settings.cleanup_interval_seconds):
                while not stopping.is_set():
                    try:
                        await resolve_votes_once()
                    except Exception:
                        WORKER_ERRORS.labels("vote_resolution").inc()
                        event("vote_resolution_failed", service="worker", level="ERROR",
                              error_code="TEMPORARY_UNAVAILABLE")
                    try:
                        await reconcile_once(settings)
                    except Exception:
                        WORKER_ERRORS.labels("reconcile").inc()
                        event("reconcile_failed", service="worker", level="ERROR", error_code="TEMPORARY_UNAVAILABLE")
                    try:
                        await asyncio.wait_for(stopping.wait(), timeout=1)
                    except TimeoutError:
                        pass
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(run())
