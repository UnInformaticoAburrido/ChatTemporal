"""Estado durable de entregas; nunca persiste payload efímero."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import class_row

from chat.persistence import DeliveryState, Mode
from chat.ws_protocol import DeliveryStatus


@dataclass(frozen=True)
class MessageState:
    message_id: UUID
    conversation_id: UUID
    sender_id: UUID
    recipient_id: UUID
    mode_at_send: Mode | None
    status: DeliveryState
    sent_at: datetime
    received_at: datetime | None
    expires_at: datetime
    deadline_at: datetime | None
    connection_id: UUID | None

    def dto(self) -> DeliveryStatus:
        return DeliveryStatus(message_id=self.message_id, status=self.status, sent_at=self.sent_at,
                              received_at=self.received_at, expires_at=self.expires_at)


STATE_SELECT = """SELECT e.id AS message_id,e.conversation_id,e.sender_id,
    d.recipient_user_id AS recipient_id,e.mode_at_send,d.status,e.sent_at,d.received_at,e.expires_at,
    d.deadline_at,d.connection_id FROM message_events e JOIN message_deliveries d ON d.message_id=e.id """


class DeliveryStore:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def state(self, message: UUID) -> MessageState | None:
        async with self.connection.cursor(row_factory=class_row(MessageState)) as cursor:
            await cursor.execute(STATE_SELECT + "WHERE e.id=%s AND e.expires_at>clock_timestamp()", (message,))
            return await cursor.fetchone()

    async def pending(self, conversation: UUID | None = None, connection: UUID | None = None) -> list[MessageState]:
        async with self.connection.cursor(row_factory=class_row(MessageState)) as cursor:
            await cursor.execute(STATE_SELECT + """WHERE e.mode_at_send='ephemeral' AND d.status='pending'
                AND (%s::uuid IS NULL OR e.conversation_id=%s)
                AND (%s::uuid IS NULL OR d.connection_id=%s)
                ORDER BY d.deadline_at NULLS FIRST,e.id LIMIT 1000""", (conversation, conversation, connection, connection))
            return await cursor.fetchall()

    async def deadline(self, message: UUID, connection: UUID, seconds: int) -> None:
        await self.connection.execute("""UPDATE message_deliveries d SET connection_id=%s,
            deadline_at=e.sent_at+%s*interval '1 second' FROM message_events e WHERE e.id=d.message_id AND e.id=%s""",
                                      (connection, seconds, message))

    async def finish(self, message: UUID, status: DeliveryState) -> MessageState:
        await self.connection.execute("""UPDATE message_deliveries SET status=%s,
            received_at=CASE WHEN %s='delivered' THEN clock_timestamp() ELSE NULL END,
            updated_at=clock_timestamp() WHERE message_id=%s AND status='pending'""", (status, status, message))
        state = await self.state(message)
        assert state
        return state
