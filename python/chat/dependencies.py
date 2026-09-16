import asyncio
import time

import psycopg
from redis.asyncio import Redis

from chat.config import Settings, read_secret


async def probe_database(timeout: int) -> None:
    async with await psycopg.AsyncConnection.connect(
        read_secret("DATABASE_URL"), connect_timeout=timeout
    ) as connection:
        await connection.execute("SELECT 1")


async def probe_redis(timeout: int) -> None:
    async with Redis.from_url(read_secret("REDIS_URL"), socket_timeout=timeout,
                              socket_connect_timeout=timeout) as client:
        await client.ping()


async def dependencies_ready(settings: Settings) -> bool:
    timeout = settings.dependency_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(probe_database(timeout), probe_redis(timeout))
        return True
    except Exception:
        # Deliberadamente sin texto de excepción: las URLs contienen credenciales.
        return False


async def wait_for_dependencies(settings: Settings) -> None:
    # DEC-11: revalidar también en reinicios del daemon; depends_on solo actúa en Compose.
    deadline = time.monotonic() + settings.startup_timeout_seconds
    while time.monotonic() < deadline:
        if await dependencies_ready(settings):
            return
        await asyncio.sleep(1)
    raise RuntimeError("Dependencias no disponibles dentro del plazo")
