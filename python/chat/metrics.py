"""Métricas sin etiquetas aportadas por clientes; registro por proceso."""

import asyncio
import time

from prometheus_client import Counter, Gauge, Histogram
from redis.asyncio import Redis

from chat.config import Settings, read_secret
from chat.persistence import transaction

HTTP = Histogram("chat_http_duration_seconds", "Tiempo REST incluyendo errores", ["endpoint", "method", "status"])
ERRORS = Counter("chat_errors_total", "Errores de contrato", ["code"])
WS = Gauge("chat_ws_connections", "WebSockets aceptados activos")
FRAMES = Counter("chat_ws_frames_total", "Frames válidos recibidos/enviados (no mensajes únicos)", ["direction", "kind"])
WS_ERRORS = Counter("chat_ws_errors_total", "Errores WebSocket", ["code"])
TIMEOUTS = Counter("chat_delivery_timeouts_total", "Timeouts reconciliados", ["phase"])
REUSE = Counter("chat_refresh_reuse_total", "Reutilización refresh confirmada")
PUSH = Counter("chat_push_failures_total", "Intentos Push fallidos", ["status_class"])
DEPENDENCY = Gauge("chat_dependency_up", "Sondeo independiente", ["dependency"])
REDIS_MEMORY = Gauge("chat_redis_memory_bytes", "Memoria Redis usada")
REDIS_LIMIT = Gauge("chat_redis_maxmemory_bytes", "Límite de memoria Redis")
REDIS_OOM = Gauge("chat_redis_oom_errors_total", "Contador Redis de escrituras rechazadas por OOM")
PG_CONNECTIONS = Gauge("chat_postgres_connections", "Conexiones PostgreSQL del servidor")
PG_LIMIT = Gauge("chat_postgres_max_connections", "Límite PostgreSQL; no existe pool en aplicación")
CLEANUP_AGE = Gauge("chat_cleanup_age_seconds", "Edad del último ciclo de purga correcto")
CLEANUP_LAG = Gauge("chat_cleanup_lag_seconds", "Antigüedad del ciphertext expirado aún presente")
VOTE_LAG = Gauge("chat_vote_lag_seconds", "Demora del voto vencido más antiguo")
WORKER_ERRORS = Counter("chat_worker_failures_total", "Fallos de ciclos del worker", ["task"])


async def collect(settings: Settings) -> None:
    async def postgres() -> None:
        try:
            async with asyncio.timeout(settings.dependency_timeout_seconds):
                async with transaction() as unit:
                    row = await (await unit.connection.execute("""SELECT
                        (SELECT count(*) FROM pg_stat_activity), current_setting('max_connections')::int,
                        COALESCE((SELECT GREATEST(0,extract(epoch FROM clock_timestamp()-min(content_expires_at)))
                            FROM messages WHERE content_expires_at<=clock_timestamp()),0),
                        COALESCE((SELECT GREATEST(0,extract(epoch FROM clock_timestamp()-min(expires_at)))
                            FROM votes WHERE status='open' AND expires_at<=clock_timestamp()),0)""")).fetchone()
                    assert row
                    for metric, value in zip((PG_CONNECTIONS, PG_LIMIT, CLEANUP_LAG, VOTE_LAG), row, strict=True):
                        metric.set(float(str(value)))
            DEPENDENCY.labels("postgresql").set(1)
        except Exception:
            DEPENDENCY.labels("postgresql").set(0)

    async def redis() -> None:
        try:
            async with asyncio.timeout(settings.dependency_timeout_seconds):
                async with Redis.from_url(read_secret("REDIS_URL"), socket_timeout=2) as client:
                    info = await client.info()
                    errors = await client.info("errorstats")
                    stamp = await client.get("maintenance:last_success")
                    REDIS_MEMORY.set(info["used_memory"])
                    REDIS_LIMIT.set(info["maxmemory"])
                    REDIS_OOM.set(errors.get("errorstat_OOM", {}).get("count", 0))
                    CLEANUP_AGE.set(max(0, time.time() - float(stamp)) if stamp else 1e9)
            DEPENDENCY.labels("redis").set(1)
        except Exception:
            DEPENDENCY.labels("redis").set(0)
            CLEANUP_AGE.set(1e9)

    await asyncio.gather(postgres(), redis())
