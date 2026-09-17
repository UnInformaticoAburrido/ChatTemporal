"""Lecturas autorizadas y una sola clave pública vigente por usuario."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import class_row

from chat.persistence import Position


@dataclass(frozen=True)
class KeyRecord:
    public_key: str
    protocol_version: int
    updated_at: datetime


@dataclass(frozen=True)
class ConversationRecord:
    id: UUID
    mode: Literal["stored", "ephemeral"]
    status: Literal["pending", "active", "closed"]
    role: Literal["host", "guest"]
    grace_message_limit: int
    grace_messages_used: int
    created_at: datetime
    accepted_at: datetime | None
    mode_changed_at: datetime | None
    closed_at: datetime | None
    updated_at: datetime
    peer_id: UUID | None
    peer_nick: str | None


CONVERSATION_SELECT = """
SELECT c.id,c.mode,c.status,me.role,c.grace_message_limit,c.grace_messages_used,
       c.created_at,c.accepted_at,c.mode_changed_at,c.closed_at,c.updated_at,
       CASE WHEN c.status <> 'pending' AND c.accepted_at IS NOT NULL THEN peer.id END AS peer_id,
       CASE WHEN c.status <> 'pending' AND c.accepted_at IS NOT NULL THEN peer.nick END AS peer_nick
FROM conversations c
JOIN conversation_members me ON me.conversation_id=c.id AND me.user_id=%s
LEFT JOIN conversation_members other ON other.conversation_id=c.id AND other.user_id<>me.user_id
LEFT JOIN users peer ON peer.id=other.user_id AND peer.is_active
WHERE me.membership_status<>'left'
"""


class ResourceStore:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def replace_key(self, user: UUID, public_key: str, version: int, *, rotate: bool) -> KeyRecord | None:
        async with self.connection.cursor(row_factory=class_row(KeyRecord)) as cursor:
            if rotate:
                # DEC-48: rotate requiere una fila previa; PUT permite crearla.
                await cursor.execute(
                    """UPDATE user_keys SET public_key=%s,protocol_version=%s,
                    updated_at=CASE WHEN (public_key,protocol_version) IS DISTINCT FROM (%s,%s)
                        THEN clock_timestamp() ELSE updated_at END
                    WHERE user_id=%s RETURNING public_key,protocol_version,updated_at""",
                    (public_key, version, public_key, version, user),
                )
            else:
                await cursor.execute(
                    """INSERT INTO user_keys(user_id,public_key,protocol_version,created_at,updated_at)
                    VALUES (%s,%s,%s,clock_timestamp(),clock_timestamp())
                    ON CONFLICT (user_id) DO UPDATE SET public_key=excluded.public_key,
                      protocol_version=excluded.protocol_version,
                      updated_at=CASE WHEN (user_keys.public_key,user_keys.protocol_version)
                        IS DISTINCT FROM (excluded.public_key,excluded.protocol_version)
                        THEN clock_timestamp() ELSE user_keys.updated_at END
                    RETURNING public_key,protocol_version,updated_at""", (user, public_key, version),
                )
            return await cursor.fetchone()

    async def key(self, viewer: UUID, owner: UUID) -> KeyRecord | None:
        async with self.connection.cursor(row_factory=class_row(KeyRecord)) as cursor:
            await cursor.execute(
                """SELECT k.public_key,k.protocol_version,k.updated_at FROM user_keys k
                JOIN users u ON u.id=k.user_id AND u.is_active
                WHERE k.user_id=%s AND (k.user_id=%s OR EXISTS (
                    SELECT 1 FROM conversation_members me
                    JOIN conversation_members peer ON peer.conversation_id=me.conversation_id
                    JOIN conversations c ON c.id=me.conversation_id
                    WHERE me.user_id=%s AND peer.user_id=k.user_id AND me.membership_status<>'left'
                    AND c.status IN ('pending','active','closed')
                ))""", (owner, viewer, viewer),
            )
            return await cursor.fetchone()

    async def conversation(self, viewer: UUID, conversation: UUID) -> ConversationRecord | None:
        async with self.connection.cursor(row_factory=class_row(ConversationRecord)) as cursor:
            await cursor.execute(CONVERSATION_SELECT + " AND c.id=%s", (viewer, conversation))
            return await cursor.fetchone()

    async def conversations(self, viewer: UUID, limit: int, before: Position | None) -> list[ConversationRecord]:
        async with self.connection.cursor(row_factory=class_row(ConversationRecord)) as cursor:
            await cursor.execute(
                CONVERSATION_SELECT + """
                AND (%s::timestamptz IS NULL OR (c.updated_at,c.id)<(%s,%s::uuid))
                ORDER BY c.updated_at DESC,c.id DESC LIMIT %s""",
                (viewer, before.timestamp if before else None, before.timestamp if before else None,
                 before.id if before else None, limit + 1),
            )
            return await cursor.fetchall()
