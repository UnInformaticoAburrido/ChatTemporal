"""Rate limiting y operaciones one-time atómicas; fallan cerradas sin Redis."""

import asyncio
import ipaddress
import json
import socket
import time
from hashlib import sha256
from uuid import UUID, uuid4

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from chat.config import RateRule, Settings, read_secret
from chat.errors import APIError, unavailable

# DEC-35: TIME de Redis + script atómico. Sliding window para cuotas sin burst;
# token bucket para las dos cuotas que especifican ráfaga. No usar contadores
# de ventana fija que permitan duplicar la cuota en el cambio de minuto.
RATE_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + tonumber(t[2]) / 1000
local count, window, burst = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
if burst > 0 then
    local state = redis.call('HMGET', KEYS[1], 'tokens', 'stamp')
    local tokens = tonumber(state[1]) or burst
    local stamp = tonumber(state[2]) or now
    tokens = math.min(burst, tokens + math.max(0, now-stamp)*count/window)
    if tokens < 1 then return 0 end
    redis.call('HSET', KEYS[1], 'tokens', tokens-1, 'stamp', now)
    redis.call('PEXPIRE', KEYS[1], math.ceil(window * math.max(1, burst/count)))
else
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-window)
    if redis.call('ZCARD', KEYS[1]) >= count then return 0 end
    redis.call('ZADD', KEYS[1], now, ARGV[4])
    redis.call('PEXPIRE', KEYS[1], window)
end
return 1
"""


def redis_connection() -> Redis:
    return Redis.from_url(read_secret("REDIS_URL"), socket_timeout=2, socket_connect_timeout=2)


class IdentityRedis:
    async def limit(self, name: str, subject: str, rule: RateRule) -> None:
        key = f"rate:{name}:{sha256(subject.encode()).hexdigest()}"
        try:
            async with redis_connection() as redis:
                allowed = await redis.eval(RATE_SCRIPT, 1, key, rule.limit, rule.seconds * 1000,
                                           rule.burst, uuid4().hex)
        except (RedisError, OSError):
            raise unavailable() from None
        if allowed != 1:
            raise APIError("RATE_LIMITED", 429, "Rate limit exceeded.")

    async def consume_bootstrap(self, jti: UUID, expires_at: int) -> None:
        ttl_ms = int((expires_at - time.time()) * 1000)
        if ttl_ms <= 0:
            raise APIError("TOKEN_EXPIRED", 401, "Token expired.")
        try:
            async with redis_connection() as redis:
                accepted = await redis.set(f"bootstrap:jti:{jti}", "1", nx=True, px=ttl_ms)
        except (RedisError, OSError):
            raise unavailable() from None
        if not accepted:
            raise APIError("TOKEN_INVALID", 401, "Invalid token.")

    async def revoke(self, session_ids: list[UUID]) -> None:
        if not session_ids:
            return
        try:
            async with redis_connection() as redis:
                for sid in session_ids:
                    # DEC-36: canal interno que N5 consumirá; solo identificador
                    # de sesión, sin JWT, refresh ni información del perfil.
                    await redis.publish("auth:session_revoked", json.dumps({"sid": str(sid)}))
        except (RedisError, OSError):
            # La revocación durable YA está confirmada: jamás revertirla.
            raise unavailable() from None

    async def ticket(self, digest: bytes, user_id: UUID, sid: UUID) -> None:
        try:
            async with redis_connection() as redis:
                accepted = await redis.set(f"ws:ticket:{digest.hex()}",
                                           json.dumps({"user_id": str(user_id), "sid": str(sid)}),
                                           ex=30, nx=True)
        except (RedisError, OSError):
            raise unavailable() from None
        if not accepted:
            raise unavailable()


async def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    # DEC-37: Uvicorn conserva el peer TCP; solo aceptar la cabecera sobrescrita
    # por Caddy cuando ese peer coincide con la IP actual de su servicio Docker.
    # Resolver al pedirlo evita un ciclo de arranque: Caddy depende de la API.
    if settings.trusted_proxy_host and request.headers.get("X-Chat-Client-IP"):
        try:
            addresses = await asyncio.to_thread(socket.getaddrinfo, settings.trusted_proxy_host, None)
            if peer in {address[4][0] for address in addresses}:
                return str(ipaddress.ip_address(request.headers["X-Chat-Client-IP"]))
        except (OSError, ValueError):
            pass
    return peer
