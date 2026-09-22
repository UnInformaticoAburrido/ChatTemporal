"""Reconciliación idempotente: fallo de proceso/Redis, deadlines y cancelaciones N4."""

import time
from datetime import UTC, datetime
from uuid import UUID

from chat.config import Settings
from chat.delivery_store import DeliveryStore
from chat.identity_redis import redis_connection
from chat.messaging import failed
from chat.persistence import transaction
from chat.realtime_redis import RealtimeRedis, publish


async def reconcile_once(settings: Settings, *, disconnected: UUID | None = None,
                         conversation: UUID | None = None) -> None:
    redis = RealtimeRedis(settings)
    for candidate in await redis.active(conversation=conversation, connection=disconnected):
        notification = None
        async with transaction() as unit:
            row = await (await unit.connection.execute("SELECT status,mode FROM conversations WHERE id=%s FOR UPDATE",
                                                      (candidate.conversation,))).fetchone()
            attempt = await redis.get(candidate.message)
            if attempt is None or attempt.phase in ("failed", "delivered"):
                continue
            state = await DeliveryStore(unit.connection).state(UUID(attempt.message))
            reason = None
            if state and state.status != "pending":
                attempt.phase = "delivered" if state.status == "delivered" else "failed"
                await redis.save(attempt)
                continue
            if attempt.deadline <= time.time():
                reason = "DELIVERY_TIMEOUT" if state else "OFFER_TIMEOUT"
            elif attempt.connection and (attempt.connection == str(disconnected)
                                         or not await redis.connected(attempt.connection, attempt.recipient)):
                reason = "RECIPIENT_DISCONNECTED"
            elif not state:
                if row is None or row[0] == "closed":
                    reason = "CONVERSATION_CLOSED"
                elif row[1] == "stored":
                    reason = "TEMPORARY_UNAVAILABLE"
                elif await (await unit.connection.execute("""SELECT 1 FROM votes WHERE conversation_id=%s
                        AND status='open' AND expires_at>clock_timestamp()""", (attempt.conversation,))).fetchone():
                    reason = "VOTE_OPEN"
            if reason:
                if state:
                    await DeliveryStore(unit.connection).finish(state.message_id,
                                                                "expired" if reason == "DELIVERY_TIMEOUT" else "failed")
                attempt.phase, attempt.failure = "failed", reason
                await redis.save(attempt)
                notification = failed(UUID(attempt.message), UUID(attempt.conversation), reason, UUID(attempt.request))
        if notification:
            await publish(candidate.sender, notification)

    # Incluye commit PostgreSQL seguido de caída antes de SET Redis, y reinicio
    # completo de Redis sin metadata. El modo se toma del evento histórico.
    async with transaction() as unit:
        candidates = await DeliveryStore(unit.connection).pending(conversation, disconnected)
    for candidate_state in candidates:
        notification = None
        async with transaction() as unit:
            await unit.connection.execute("SELECT id FROM conversations WHERE id=%s FOR UPDATE",
                                          (candidate_state.conversation_id,))
            store = DeliveryStore(unit.connection)
            state = await store.state(candidate_state.message_id)
            if state is None or state.status != "pending":
                continue
            expired = state.deadline_at is None or state.deadline_at <= datetime.now(UTC)
            lost = state.connection_id == disconnected if disconnected else False
            if state.connection_id and not expired:
                lost = lost or not await redis.connected(str(state.connection_id), str(state.recipient_id))
            if expired or lost:
                await store.finish(state.message_id, "expired" if expired else "failed")
                async with redis_connection() as client:
                    await client.delete(f"ephemeral:delivery:{state.message_id}:{state.recipient_id}")
                notification = failed(state.message_id, state.conversation_id,
                                      "DELIVERY_TIMEOUT" if expired else "RECIPIENT_DISCONNECTED")
        if notification:
            await publish(candidate_state.sender_id, notification)
