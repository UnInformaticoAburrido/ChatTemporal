"""N5: transacciones de envío/ACK y entrega efímera ligada a una conexión."""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from chat.config import Settings
from chat.conversation_store import ConversationStore
from chat.conversations import verified
from chat.delivery_store import DeliveryStore, MessageState
from chat.errors import APIError
from chat.identity import Principal
from chat.identity_redis import IdentityRedis
from chat.identity_store import IdentityStore
from chat.persistence import MessageIdConflict, MessageWrite, UnitOfWork, transaction
from chat.protocol import decode_binary
from chat.realtime_redis import Attempt, RealtimeRedis, publish
from chat.resource_dto import MessageSend
from chat.resource_store import ConversationRecord
from chat.ws_protocol import DeliveryStatus, MessageControl, frame, utc_text


async def authenticate(unit: UnitOfWork, principal: Principal) -> None:
    await unit.connection.execute("SELECT id FROM users WHERE id=%s FOR NO KEY UPDATE", (principal.user.id,))
    verified(await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid))


async def permitted(unit: UnitOfWork, user: UUID, conversation: UUID) -> ConversationRecord:
    store = ConversationStore(unit.connection)
    await store.lock(user, conversation)
    row = await store.visible(user, conversation)
    if row.status == "closed":
        raise APIError("CONVERSATION_CLOSED", 409, "Conversation closed.")
    vote = await (await unit.connection.execute("""SELECT 1 FROM votes WHERE conversation_id=%s
        AND status='open' AND expires_at>clock_timestamp() LIMIT 1""", (conversation,))).fetchone()
    if vote:
        raise APIError("VOTE_OPEN", 409, "Voting in progress.")
    if row.status == "pending" and row.grace_messages_used >= min(5, row.grace_message_limit):
        raise APIError("GRACE_LIMIT_REACHED", 409, "Grace limit reached.")
    return row


def delivered(state: MessageState, request: UUID | None = None) -> dict[str, Any]:
    assert state.received_at
    return frame("message.delivered", state.conversation_id,
                 {"message_id": str(state.message_id), "received_at": utc_text(state.received_at)}, request)


def failed(message: UUID, conversation: UUID, code: str, request: UUID | None = None) -> dict[str, Any]:
    return frame("message.failed", conversation, {"message_id": str(message), "code": code,
                 "retryable": code not in ("CONVERSATION_CLOSED", "VOTE_OPEN")}, request)


class Messaging:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis = RealtimeRedis(settings)

    async def status(self, principal: Principal, message: UUID) -> DeliveryStatus:
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid)
            state = await DeliveryStore(unit.connection).state(message)
            if state is None or principal.user.id not in (state.sender_id, state.recipient_id):
                raise APIError("MESSAGE_NOT_FOUND", 404, "Message not found.")
            await ConversationStore(unit.connection).lock(principal.user.id, state.conversation_id)
            # Aplicar plazo sin depender de que el reconciliador haya despertado.
            state = await DeliveryStore(unit.connection).state(message)
            assert state
            if (state.mode_at_send == "ephemeral" and state.status == "pending" and state.deadline_at
                    and state.deadline_at <= datetime.now(UTC)):
                state = await DeliveryStore(unit.connection).finish(message, "expired")
            return state.dto()

    async def offer(self, principal: Principal, message: MessageControl) -> None:
        async with transaction() as unit:
            await authenticate(unit, principal)
            row = await permitted(unit, principal.user.id, message.conversation_id)
            if row.mode != "ephemeral":
                raise APIError("CONVERSATION_NOT_EPHEMERAL", 409, "Offer requires ephemeral mode.")
            existing = await DeliveryStore(unit.connection).state(message.payload.message_id)
            if existing:
                raise APIError("MESSAGE_ID_CONFLICT", 409, "Message identifier already used.")
            attempt = await self.redis.get(message.payload.message_id)
            if attempt:
                if attempt.sender != str(principal.user.id) or attempt.conversation != str(row.id):
                    raise APIError("MESSAGE_ID_CONFLICT", 409, "Message identifier already used.")
                if attempt.phase != "offer" or attempt.deadline <= time.time():
                    raise APIError("OFFER_TIMEOUT", 409, "Start a new message attempt.")
            else:
                peer = await (await unit.connection.execute("""SELECT user_id FROM conversation_members
                    WHERE conversation_id=%s AND user_id<>%s AND membership_status<>'left'""",
                                                           (row.id, principal.user.id))).fetchone()
                if peer is None:
                    raise APIError("CONVERSATION_CLOSED", 409, "Recipient unavailable.")
                await IdentityRedis().limit("messages_user", str(principal.user.id), self.settings.rate_limits.messages_user)
                attempt = Attempt(str(message.payload.message_id), str(row.id), str(principal.user.id),
                                  str(peer[0]), str(message.request_id),
                                  time.time()+self.settings.ephemeral_offer_timeout_seconds)
                await self.redis.create(attempt)
        await publish(attempt.recipient, frame("message.offer", message.conversation_id,
                      {"message_id": attempt.message}, message.request_id))

    async def ready(self, principal: Principal, connection: UUID, message: MessageControl) -> None:
        async with transaction() as unit:
            await authenticate(unit, principal)
            row = await permitted(unit, principal.user.id, message.conversation_id)
            attempt = await self.redis.get(message.payload.message_id)
            if (attempt is None or attempt.recipient != str(principal.user.id)
                    or attempt.conversation != str(row.id)):
                raise APIError("OFFER_NOT_FOUND", 404, "Offer unavailable.")
            if (row.mode != "ephemeral" or attempt.deadline <= time.time()
                    or attempt.phase not in ("offer", "ready")
                    or (attempt.connection and attempt.connection != str(connection))):
                raise APIError("OFFER_TIMEOUT", 409, "Offer no longer available.")
            attempt.phase, attempt.connection = "ready", str(connection)
            await self.redis.save(attempt)
        await publish(attempt.sender, frame("message.ready", row.id, {"message_id": attempt.message}, message.request_id))

    async def send(self, principal: Principal, connection: UUID, message: MessageSend) -> dict[str, Any] | None:
        identifier = message.payload.message_id
        write = MessageWrite(identifier, message.conversation_id, principal.user.id, 1,
                             decode_binary(message.payload.crypto_meta, maximum=56),
                             decode_binary(message.payload.ciphertext, maximum=self.settings.max_ciphertext_bytes))
        attempt: Attempt | None = None
        async with transaction() as unit:
            await authenticate(unit, principal)
            store = ConversationStore(unit.connection)
            await store.lock(principal.user.id, message.conversation_id)
            existing = await DeliveryStore(unit.connection).state(identifier)
            if existing:
                try:
                    await unit.messages.record(write)
                except MessageIdConflict:
                    raise APIError("MESSAGE_ID_CONFLICT", 409, "Conflicting message identifier.") from None
                if existing.status == "delivered":
                    return delivered(existing, message.request_id)
                if existing.status in ("failed", "expired"):
                    return failed(identifier, message.conversation_id, "DELIVERY_TIMEOUT", message.request_id)
                return None  # REST status es la confirmación normativa stored.
            row = await permitted(unit, principal.user.id, message.conversation_id)
            attempt = await self.redis.guarded_get(identifier, connection)
            if attempt and (attempt.sender != str(principal.user.id) or attempt.conversation != str(row.id)):
                raise APIError("MESSAGE_ID_CONFLICT", 409, "Conflicting message identifier.")
            if attempt and row.mode == "stored":
                return failed(identifier, row.id, "TEMPORARY_UNAVAILABLE", message.request_id)
            if row.mode == "ephemeral":
                if attempt is None or attempt.phase != "ready" or attempt.deadline <= time.time():
                    return failed(identifier, row.id, "OFFER_TIMEOUT", message.request_id)
                if not await self.redis.connected(attempt.connection, attempt.recipient):
                    return failed(identifier, row.id, "RECIPIENT_DISCONNECTED", message.request_id)
            else:
                await IdentityRedis().limit("messages_user", str(principal.user.id), self.settings.rate_limits.messages_user)
            try:
                result = await unit.messages.record(write)
            except MessageIdConflict:
                raise APIError("MESSAGE_ID_CONFLICT", 409, "Conflicting message identifier.") from None
            if not result.created:
                return None
            if attempt:
                assert attempt.connection
                await DeliveryStore(unit.connection).deadline(identifier, UUID(attempt.connection),
                                                              self.settings.ephemeral_delivery_timeout_seconds)
                attempt.phase = "sending"
                attempt.deadline = result.event.sent_at.timestamp()+self.settings.ephemeral_delivery_timeout_seconds
                await self.redis.save(attempt)
            recipient = result.deliveries[0].recipient_user_id
            event = frame("message.new", row.id, {"message_id": str(identifier), "sender_role": row.role,
                          "sent_at": utc_text(result.event.sent_at), "protocol_version": 1,
                          "ciphertext": message.payload.ciphertext, "crypto_meta": message.payload.crypto_meta},
                          message.request_id)
        # El commit precede a toda publicación; si el proceso cae aquí, el deadline
        # durable permite reconciliar sin deducir el modo actual de conversación.
        if attempt:
            async with transaction() as unit:
                await unit.conversations.lock(message.conversation_id)
                state = await DeliveryStore(unit.connection).state(identifier)
                assert state
                if state.status != "pending":
                    return failed(identifier, row.id, "DELIVERY_TIMEOUT", message.request_id)
                if not await self.redis.connected(attempt.connection, attempt.recipient):
                    await DeliveryStore(unit.connection).finish(identifier, "failed")
                    attempt.phase, attempt.failure = "failed", "RECIPIENT_DISCONNECTED"
                    await self.redis.save(attempt)
                    return failed(identifier, row.id, attempt.failure, message.request_id)
                await self.redis.payload(attempt, event)
                attempt.phase = "sent"
                await self.redis.save(attempt)
        await publish(recipient, event, attempt.connection if attempt else None)
        return None

    async def ack(self, principal: Principal, connection: UUID, message: MessageControl) -> None:
        async with transaction() as unit:
            await authenticate(unit, principal)
            await ConversationStore(unit.connection).lock(principal.user.id, message.conversation_id)
            store = DeliveryStore(unit.connection)
            state = await store.state(message.payload.message_id)
            if state is None or state.conversation_id != message.conversation_id or state.recipient_id != principal.user.id:
                raise APIError("MESSAGE_NOT_FOUND", 404, "Message not found.")
            if state.status in ("failed", "expired"):
                raise APIError("DELIVERY_TIMEOUT", 409, "Delivery already failed.")
            attempt = await self.redis.get(state.message_id) if state.mode_at_send == "ephemeral" else None
            if state.mode_at_send == "ephemeral" and state.status == "pending":
                if state.connection_id != connection:
                    raise APIError("OFFER_NOT_FOUND", 403, "Delivery belongs to another connection.")
                if (attempt is None or state.deadline_at is None or state.deadline_at <= datetime.now(UTC)
                        or not await self.redis.has_payload(attempt)):
                    raise APIError("DELIVERY_TIMEOUT", 409, "Delivery expired.")
            state = await store.finish(state.message_id, "delivered")
            if attempt:
                attempt.phase = "delivered"
                await self.redis.save(attempt)
        await publish(state.sender_id, delivered(state, message.request_id))
