"""Primitivas internas de N1; no sustituyen autenticación ni el protocolo WS.

DEC-25: SQL explícito y una conexión/transacción por unidad de trabajo. Los
servicios N2–N6 deberán autorizar la operación antes de utilizar estas escrituras.
Nunca devolver directamente estos registros internos como DTOs públicos.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import class_row

from chat.config import read_secret

Mode = Literal["stored", "ephemeral"]
Status = Literal["pending", "active", "closed"]
DeliveryState = Literal["pending", "delivered", "failed", "expired"]


class PersistenceConflict(Exception):
    """Conflicto interno; el adaptador REST/WS asignará el error normativo."""


class MessageIdConflict(PersistenceConflict):
    pass


class ConversationUnavailable(PersistenceConflict):
    pass


@dataclass(frozen=True)
class Position:
    """Clave de paginación interna; la codificación del cursor corresponde a N3."""

    timestamp: datetime
    id: UUID

    def __post_init__(self) -> None:
        if self.timestamp.utcoffset() is None:
            raise ValueError("La posición requiere una fecha con zona horaria")


@dataclass(frozen=True)
class User:
    id: UUID
    nick: str
    email: str = field(repr=False)
    memory_hash: str = field(repr=False)
    email_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Conversation:
    id: UUID
    mode: Mode
    status: Status
    grace_message_limit: int
    grace_messages_used: int
    updated_at: datetime


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    sender_role: Literal["host", "guest"]
    sent_at: datetime
    protocol_version: int
    ciphertext: bytes = field(repr=False)
    crypto_meta: bytes = field(repr=False)
    is_grace_message: bool
    content_expires_at: datetime


@dataclass(frozen=True)
class MessageEvent:
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    sent_at: datetime
    expires_at: datetime
    payload_fingerprint: bytes | None = field(repr=False)
    mode_at_send: Mode | None


@dataclass(frozen=True)
class Delivery:
    message_id: UUID
    recipient_user_id: UUID
    status: DeliveryState
    received_at: datetime | None
    updated_at: datetime
    deadline_at: datetime | None
    connection_id: UUID | None


@dataclass(frozen=True)
class WriteResult:
    event: MessageEvent
    created: bool
    deliveries: list[Delivery]


@dataclass(frozen=True)
class MessageWrite:
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    protocol_version: int
    crypto_meta: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.protocol_version != 1:
            raise ValueError("Versión criptográfica no soportada en v1")
        if len(self.crypto_meta) != 56:
            raise ValueError("crypto_meta debe contener 56 bytes")

    @property
    def fingerprint(self) -> bytes:
        # §28.2: bytes originales, versión uint32 big-endian, sin Base64.
        return sha256(self.protocol_version.to_bytes(4, "big") + self.crypto_meta + self.ciphertext).digest()


class Users:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def create(self, *, nick: str, email: str, memory_hash: str) -> User:
        # El hash debe llegar ya calculado por N2; nunca aceptar aquí la frase.
        async with self.connection.cursor(row_factory=class_row(User)) as cursor:
            await cursor.execute(
                "INSERT INTO users(nick,email,memory_hash) VALUES (%s,%s,%s) RETURNING *",
                (nick, email, memory_hash),
            )
            row = await cursor.fetchone()
            assert row is not None
            return row

    async def get(self, user_id: UUID) -> User | None:
        async with self.connection.cursor(row_factory=class_row(User)) as cursor:
            await cursor.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            return await cursor.fetchone()


CONVERSATION_COLUMNS = "c.id,c.mode,c.status,c.grace_message_limit,c.grace_messages_used,c.updated_at"


def validate_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")


class Conversations:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def lock(self, conversation_id: UUID) -> Conversation:
        """§28.1: conservar este bloqueo hasta el commit de TODA la operación."""
        async with self.connection.cursor(row_factory=class_row(Conversation)) as cursor:
            await cursor.execute(
                f"SELECT {CONVERSATION_COLUMNS} FROM conversations c WHERE c.id=%s FOR UPDATE",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ConversationUnavailable()
            return row

    async def list_for_member(
        self, user_id: UUID, *, limit: int = 50, before: Position | None = None,
    ) -> list[Conversation]:
        validate_limit(limit)
        async with self.connection.cursor(row_factory=class_row(Conversation)) as cursor:
            await cursor.execute(
                f"""SELECT {CONVERSATION_COLUMNS} FROM conversations c
                JOIN conversation_members m ON m.conversation_id=c.id
                WHERE m.user_id=%s AND m.membership_status <> 'left'
                  AND (%s::timestamptz IS NULL OR (c.updated_at,c.id) < (%s,%s::uuid))
                ORDER BY c.updated_at DESC,c.id DESC LIMIT %s""",
                (user_id, before.timestamp if before else None,
                 before.timestamp if before else None, before.id if before else None, limit),
            )
            return await cursor.fetchall()


class Messages:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def _event(self, message_id: UUID) -> MessageEvent | None:
        async with self.connection.cursor(row_factory=class_row(MessageEvent)) as cursor:
            await cursor.execute("SELECT * FROM message_events WHERE id=%s", (message_id,))
            return await cursor.fetchone()

    async def _deliveries(self, message_id: UUID) -> list[Delivery]:
        async with self.connection.cursor(row_factory=class_row(Delivery)) as cursor:
            await cursor.execute(
                "SELECT * FROM message_deliveries WHERE message_id=%s ORDER BY recipient_user_id",
                (message_id,),
            )
            return await cursor.fetchall()

    async def _duplicate(self, message: MessageWrite, event: MessageEvent) -> WriteResult:
        if (event.conversation_id != message.conversation_id or event.sender_id != message.sender_id
                or event.payload_fingerprint != message.fingerprint):
            raise MessageIdConflict()
        return WriteResult(event, False, await self._deliveries(message.id))

    async def record(self, message: MessageWrite) -> WriteResult:
        """Persistir un envío autorizado por el servicio; ephemeral no escribe payload.

        N5 debe validar ticket/sesión, email, límites y permiso ready antes de llamar.
        Este método no publica ni escribe Redis. Solo created=True admite esos efectos.
        """
        # DEC-26: savepoint para deshacer TODA la escritura incluso si el servicio
        # captura el conflicto y continúa dentro de su unidad de trabajo.
        async with self.connection.transaction():
            conversation = await Conversations(self.connection).lock(message.conversation_id)
            # §28.2: un retry compatible recupera estado incluso tras close/upgrade.
            existing = await self._event(message.id)
            if existing is not None:
                return await self._duplicate(message, existing)
            if conversation.status == "closed":
                raise ConversationUnavailable()
            members = await (await self.connection.execute(
                """SELECT user_id FROM conversation_members
                WHERE conversation_id=%s AND membership_status <> 'left'""",
                (message.conversation_id,),
            )).fetchall()
            member_ids = [row[0] for row in members]
            if message.sender_id not in member_ids or len(member_ids) != 2:
                raise ConversationUnavailable()
            vote = await (await self.connection.execute(
                """SELECT 1 FROM votes WHERE conversation_id=%s AND status='open'
                AND expires_at > clock_timestamp() LIMIT 1""", (message.conversation_id,),
            )).fetchone()
            if vote is not None:
                raise ConversationUnavailable()
            grace = conversation.status == "pending"
            if grace and conversation.grace_messages_used >= min(conversation.grace_message_limit, 5):
                raise ConversationUnavailable()
            # DEC-27: el reloj se toma DESPUÉS de esperar el lock. now() conserva
            # el inicio de transacción y podría anticipar sent_at/expiraciones.
            async with self.connection.cursor(row_factory=class_row(MessageEvent)) as cursor:
                await cursor.execute(
                    """WITH moment AS (SELECT clock_timestamp() AS ts)
                    INSERT INTO message_events
                      (id,conversation_id,sender_id,sent_at,expires_at,payload_fingerprint,mode_at_send)
                    SELECT %s,%s,%s,ts,ts + interval '30 days',%s,%s FROM moment
                    ON CONFLICT (id) DO NOTHING RETURNING *""",
                    (message.id, message.conversation_id, message.sender_id, message.fingerprint, conversation.mode),
                )
                event = await cursor.fetchone()
            if event is None:
                # Carrera de UUID global desde otra conversación: el índice único
                # espera al commit rival. Una nueva sentencia ve ese commit.
                existing = await self._event(message.id)
                if existing is None:
                    raise MessageIdConflict()
                return await self._duplicate(message, existing)
            if conversation.mode == "stored":
                await self.connection.execute(
                    """INSERT INTO messages
                    (id,ciphertext,crypto_meta,protocol_version,is_grace_message,content_expires_at)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (message.id, message.ciphertext, message.crypto_meta, message.protocol_version,
                     grace, event.sent_at + timedelta(days=30)),
                )
            await self.connection.execute(
                """INSERT INTO message_deliveries(message_id,recipient_user_id)
                SELECT %s,user_id FROM conversation_members
                WHERE conversation_id=%s AND user_id<>%s AND membership_status<>'left'""",
                (message.id, message.conversation_id, message.sender_id),
            )
            await self.connection.execute(
                """UPDATE conversations SET grace_messages_used=grace_messages_used+%s,
                updated_at=%s WHERE id=%s""", (int(grace), event.sent_at, message.conversation_id),
            )
            return WriteResult(event, True, await self._deliveries(message.id))

    async def history(
        self, conversation_id: UUID, user_id: UUID, *, limit: int = 50, before: Position | None = None,
        include_next: bool = False,
    ) -> list[StoredMessage]:
        validate_limit(limit)
        # DEC-28: el permiso se comprueba en cada consulta, independiente del cursor.
        # SQL devuelve solo rol del emisor para no exponer su identidad en pending.
        async with self.connection.cursor(row_factory=class_row(StoredMessage)) as cursor:
            await cursor.execute(
                """SELECT e.id,sender.role AS sender_role,e.sent_at,m.protocol_version,
                m.ciphertext,m.crypto_meta,m.is_grace_message,m.content_expires_at
                FROM message_events e JOIN messages m ON m.id=e.id
                JOIN conversations c ON c.id=e.conversation_id
                JOIN conversation_members viewer ON viewer.conversation_id=c.id AND viewer.user_id=%s
                JOIN conversation_members sender ON sender.conversation_id=c.id AND sender.user_id=e.sender_id
                WHERE c.id=%s AND c.mode='stored' AND viewer.membership_status<>'left'
                AND m.content_expires_at>statement_timestamp() AND e.expires_at>statement_timestamp()
                AND (%s::timestamptz IS NULL OR (e.sent_at,e.id)<(%s,%s::uuid))
                ORDER BY e.sent_at DESC,e.id DESC LIMIT %s""",
                (user_id, conversation_id, before.timestamp if before else None,
                 before.timestamp if before else None, before.id if before else None, limit + int(include_next)),
            )
            return await cursor.fetchall()


class UnitOfWork:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection
        self.users = Users(connection)
        self.conversations = Conversations(connection)
        self.messages = Messages(connection)


@asynccontextmanager
async def transaction() -> AsyncIterator[UnitOfWork]:
    """Commit solo al salir correctamente; rollback y cierre en error/cancelación."""
    async with await psycopg.AsyncConnection[tuple[object, ...]].connect(
        read_secret("DATABASE_URL"), connect_timeout=3,
    ) as connection:
        async with connection.transaction():
            # DEC-25: límites locales; no alterar configuración global del servidor.
            await connection.execute("SET LOCAL statement_timeout = '10s'")
            await connection.execute("SET LOCAL lock_timeout = '5s'")
            yield UnitOfWork(connection)
