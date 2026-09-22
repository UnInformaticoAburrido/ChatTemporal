"""Estado volátil, presencia por conexión y eventos entre procesos API."""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

from chat.config import Settings
from chat.errors import APIError, unavailable
from chat.identity_redis import redis_connection


@dataclass
class Attempt:
    message: str
    conversation: str
    sender: str
    recipient: str
    request: str
    deadline: float
    phase: str = "offer"
    connection: str | None = None
    failure: str | None = None
    retain_until: float = field(default_factory=lambda: time.time() + 2592000)

    @property
    def offer_key(self) -> str:
        return f"ephemeral:offer:{self.message}:{self.recipient}"

    @property
    def delivery_key(self) -> str:
        return f"ephemeral:delivery:{self.message}:{self.recipient}"


async def publish(user: UUID | str, event: dict[str, Any], connection: str | None = None) -> None:
    async with redis_connection() as redis:
        await redis.publish(f"events:user:{user}", json.dumps({"event": event, "connection": connection}))


class RealtimeRedis:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def presence(self, user: UUID, sid: UUID, connection: UUID, *, renew: bool = False) -> None:
        async with redis_connection() as redis:
            key = f"connection:{connection}"
            value = json.dumps({"user_id": str(user), "sid": str(sid)})
            # No resucitar una conexión después de perder Redis: handshake nuevo.
            ok = await redis.set(key, value, ex=self.settings.presence_ttl_seconds, xx=renew, nx=not renew)
            if not ok:
                raise APIError("SESSION_REVOKED", 401, "Connection no longer valid.")
            await redis.sadd(f"user_connections:{user}", str(connection))
            await redis.expire(f"user_connections:{user}", self.settings.presence_ttl_seconds)

    async def disconnect(self, user: UUID, connection: UUID) -> None:
        async with redis_connection() as redis:
            await redis.delete(f"connection:{connection}")
            await redis.srem(f"user_connections:{user}", str(connection))

    async def connected(self, connection: str | None, recipient: str) -> bool:
        if connection is None:
            return False
        async with redis_connection() as redis:
            raw = await redis.get(f"connection:{connection}")
            return bool(raw and json.loads(raw)["user_id"] == recipient)

    async def get(self, message: UUID | str) -> Attempt | None:
        async with redis_connection() as redis:
            raw = await redis.get(f"ephemeral:attempt:{message}")
            return Attempt(**json.loads(raw)) if raw else None

    async def guarded_get(self, message: UUID, connection: UUID) -> Attempt | None:
        # Una pérdida de Redis nunca convierte un intento ephemeral olvidado en
        # un envío stored. Presencia e intento se observan en la misma operación.
        async with redis_connection() as redis:
            result = await redis.eval("""if redis.call('EXISTS',KEYS[1]) == 0 then return {0} end
                return {1,redis.call('GET',KEYS[2]) or ''}""", 2,
                                      f"connection:{connection}", f"ephemeral:attempt:{message}")
            if result[0] != 1:
                raise unavailable()
            return Attempt(**json.loads(result[1])) if result[1] else None

    async def create(self, attempt: Attempt) -> None:
        async with redis_connection() as redis:
            # Retener solo metadatos del intento para no convertir un send efímero
            # tardío en stored después de upgrade. Ciphertext siempre tiene TTL corto.
            ok = await redis.set(f"ephemeral:attempt:{attempt.message}", json.dumps(asdict(attempt)), nx=True, ex=2592000)
            if not ok:
                raise APIError("MESSAGE_ID_CONFLICT", 409, "Message identifier already used.")
            await redis.set(attempt.offer_key, attempt.message, px=max(1, int((attempt.deadline-time.time())*1000)))
            await redis.zadd("ephemeral:deadlines", {attempt.message: attempt.deadline})
            await redis.zadd(f"ephemeral:conversation:{attempt.conversation}", {attempt.message: attempt.deadline})
            await redis.expire(f"ephemeral:conversation:{attempt.conversation}", 2592000)
            await redis.zadd(f"ephemeral:recipient:{attempt.recipient}", {attempt.message: attempt.deadline})
            await redis.expire(f"ephemeral:recipient:{attempt.recipient}", 2592000)

    async def save(self, attempt: Attempt) -> None:
        async with redis_connection() as redis:
            await redis.set(f"ephemeral:attempt:{attempt.message}", json.dumps(asdict(attempt)),
                            px=max(1, int((attempt.retain_until-time.time())*1000)))
            if attempt.phase != "offer":
                await redis.zrem(f"ephemeral:recipient:{attempt.recipient}", attempt.message)
            if attempt.phase in ("failed", "delivered"):
                await redis.delete(attempt.offer_key, attempt.delivery_key)
                await redis.zrem("ephemeral:deadlines", attempt.message)
                await redis.zrem(f"ephemeral:conversation:{attempt.conversation}", attempt.message)
                if attempt.connection:
                    await redis.zrem(f"ephemeral:connection:{attempt.connection}", attempt.message)
            else:
                await redis.zadd("ephemeral:deadlines", {attempt.message: attempt.deadline})
                await redis.zadd(f"ephemeral:conversation:{attempt.conversation}", {attempt.message: attempt.deadline})
                if attempt.connection:
                    await redis.zadd(f"ephemeral:connection:{attempt.connection}", {attempt.message: attempt.deadline})
                    await redis.expire(f"ephemeral:connection:{attempt.connection}", 2592000)
                if attempt.phase != "offer":
                    await redis.delete(attempt.offer_key)

    async def payload(self, attempt: Attempt, event: dict[str, Any]) -> None:
        async with redis_connection() as redis:
            remaining = int((attempt.deadline-time.time())*1000)
            if remaining <= 0:
                raise APIError("DELIVERY_TIMEOUT", 409, "Delivery expired.")
            await redis.set(attempt.delivery_key, json.dumps(event), px=remaining)

    async def has_payload(self, attempt: Attempt) -> bool:
        async with redis_connection() as redis:
            return bool(await redis.exists(attempt.delivery_key))

    async def active(self, *, conversation: UUID | None = None, connection: UUID | None = None,
                     recipient: UUID | None = None, due_only: bool = False) -> list[Attempt]:
        async with redis_connection() as redis:
            index = (f"ephemeral:conversation:{conversation}" if conversation else
                     f"ephemeral:connection:{connection}" if connection else
                     f"ephemeral:recipient:{recipient}" if recipient else "ephemeral:deadlines")
            identifiers = await redis.zrangebyscore(index, "-inf",
                                                  time.time() if due_only else "+inf", start=0, num=1000)
            result = []
            for identifier in identifiers:
                assert isinstance(identifier, bytes)
                raw = await redis.get(b"ephemeral:attempt:" + identifier)
                if raw:
                    result.append(Attempt(**json.loads(raw)))
                else:
                    await redis.zrem(index, identifier)
            return result
