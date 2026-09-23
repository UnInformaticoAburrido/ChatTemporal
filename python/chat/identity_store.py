"""Persistencia de N2. Orden de locks: usuario → sesión → refresh/verificación."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import class_row

from chat.errors import APIError
from chat.persistence import User


@dataclass(frozen=True)
class Session:
    id: UUID
    user_id: UUID
    refresh_token_hash: str = field(repr=False)
    token_family_id: UUID
    created_at: datetime
    last_rotated_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class RefreshRecord:
    token_hash: bytes = field(repr=False)
    session_id: UUID
    token_family_id: UUID
    issued_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


class IdentityStore:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def user(self, user_id: UUID, *, lock: bool = False) -> User | None:
        async with self.connection.cursor(row_factory=class_row(User)) as cursor:
            await cursor.execute("SELECT * FROM users WHERE id=%s" + (" FOR UPDATE" if lock else ""), (user_id,))
            return await cursor.fetchone()

    async def by_email(self, email: str) -> User | None:
        async with self.connection.cursor(row_factory=class_row(User)) as cursor:
            await cursor.execute("SELECT * FROM users WHERE email=%s FOR UPDATE", (email,))
            return await cursor.fetchone()

    async def authenticated(self, user_id: UUID, sid: UUID, *, lock: bool = False,
                            transfer_upload: bool = False) -> User:
        user = await self.user(user_id, lock=lock)
        valid = await (await self.connection.execute(
            """SELECT 1 FROM auth_sessions WHERE id=%s AND user_id=%s
            AND revoked_at IS NULL AND expires_at>clock_timestamp()""", (sid, user_id),
        )).fetchone()
        if not valid and transfer_upload:
            valid = await (await self.connection.execute("""SELECT 1 FROM transfer_upload_grants g
                JOIN auth_sessions target ON target.id=g.target_sid
                WHERE g.user_id=%s AND g.source_sid=%s AND g.expires_at>clock_timestamp()
                AND target.revoked_at IS NULL AND target.expires_at>clock_timestamp()""", (user_id, sid))).fetchone()
        if user is None or not user.is_active or not valid:
            raise APIError("SESSION_REVOKED", 401, "Session revoked or expired.")
        return user

    async def transfer_predecessor(self, user_id: UUID) -> UUID | None:
        row = await (await self.connection.execute("""SELECT id FROM auth_sessions
            WHERE user_id=%s AND revoked_at IS NULL AND expires_at>clock_timestamp()
            ORDER BY created_at DESC,id DESC LIMIT 1""", (user_id,))).fetchone()
        return row[0] if row and isinstance(row[0], UUID) else None

    async def transfer_grant(self, user: UUID, source: UUID | None, target: UUID) -> None:
        await self.connection.execute("DELETE FROM transfer_upload_grants WHERE user_id=%s", (user,))
        if source:
            await self.connection.execute("""INSERT INTO transfer_upload_grants(user_id,source_sid,target_sid,expires_at)
                SELECT %s,id,%s,LEAST(expires_at,clock_timestamp()+interval '24 hours')
                FROM auth_sessions WHERE id=%s""", (user, target, source))

    async def session(self, sid: UUID) -> Session | None:
        async with self.connection.cursor(row_factory=class_row(Session)) as cursor:
            await cursor.execute("SELECT * FROM auth_sessions WHERE id=%s FOR UPDATE", (sid,))
            return await cursor.fetchone()

    async def refresh(self, digest: bytes) -> RefreshRecord | None:
        async with self.connection.cursor(row_factory=class_row(RefreshRecord)) as cursor:
            await cursor.execute("SELECT * FROM auth_refresh_tokens WHERE token_hash=%s", (digest,))
            return await cursor.fetchone()

    async def refresh_owner(self, digest: bytes) -> UUID | None:
        row = await (await self.connection.execute(
            """SELECT s.user_id FROM auth_refresh_tokens r
            JOIN auth_sessions s ON s.id=r.session_id WHERE r.token_hash=%s""", (digest,),
        )).fetchone()
        return row[0] if row and isinstance(row[0], UUID) else None

    async def create_session(self, user_id: UUID, digest: bytes) -> Session:
        async with self.connection.cursor(row_factory=class_row(Session)) as cursor:
            # DEC-39: el campo TEXT original es un puntero hex al hash binario
            # vigente. El historial BYTEA es la autoridad para detectar reuse.
            await cursor.execute(
                """WITH moment AS (SELECT clock_timestamp() AS ts)
                INSERT INTO auth_sessions(id,user_id,refresh_token_hash,token_family_id,
                    created_at,last_rotated_at,expires_at)
                SELECT %s,%s,%s,%s,ts,ts,ts+interval '30 days' FROM moment RETURNING *""",
                (uuid4(), user_id, digest.hex(), uuid4()),
            )
            session = await cursor.fetchone()
            assert session is not None
        await self.insert_refresh(session, digest)
        return session

    async def insert_refresh(self, session: Session, digest: bytes) -> None:
        await self.connection.execute(
            """INSERT INTO auth_refresh_tokens
            (token_hash,session_id,token_family_id,issued_at,expires_at) VALUES (%s,%s,%s,%s,%s)""",
            (digest, session.id, session.token_family_id, session.last_rotated_at, session.expires_at),
        )

    async def rotate(self, session: Session, old: bytes, new: bytes) -> Session:
        await self.connection.execute(
            "UPDATE auth_refresh_tokens SET used_at=clock_timestamp() WHERE token_hash=%s", (old,),
        )
        async with self.connection.cursor(row_factory=class_row(Session)) as cursor:
            await cursor.execute(
                """UPDATE auth_sessions SET refresh_token_hash=%s,last_rotated_at=clock_timestamp(),
                expires_at=clock_timestamp()+interval '30 days' WHERE id=%s RETURNING *""",
                (new.hex(), session.id),
            )
            updated = await cursor.fetchone()
            assert updated is not None
        await self.insert_refresh(updated, new)
        return updated

    async def revoke(self, user_id: UUID, *, family: UUID | None = None, sid: UUID | None = None) -> list[UUID]:
        rows = await (await self.connection.execute(
            """UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at,clock_timestamp())
            WHERE user_id=%s AND (%s::uuid IS NULL OR token_family_id=%s)
              AND (%s::uuid IS NULL OR id=%s) RETURNING id""", (user_id, family, family, sid, sid),
        )).fetchall()
        await self.connection.execute(
            """UPDATE auth_refresh_tokens SET revoked_at=COALESCE(revoked_at,clock_timestamp())
            WHERE session_id IN (SELECT id FROM auth_sessions WHERE user_id=%s
              AND (%s::uuid IS NULL OR token_family_id=%s) AND (%s::uuid IS NULL OR id=%s))""",
            (user_id, family, family, sid, sid),
        )
        return [row[0] for row in rows if isinstance(row[0], UUID)]

    async def verification(self, user_id: UUID, digest: bytes) -> None:
        await self.connection.execute("DELETE FROM email_verification_tokens WHERE user_id=%s", (user_id,))
        await self.connection.execute(
            """INSERT INTO email_verification_tokens(user_id,token_hash,created_at,expires_at)
            VALUES (%s,%s,clock_timestamp(),clock_timestamp()+interval '30 minutes')""", (user_id, digest),
        )

    async def verification_owner(self, digest: bytes) -> UUID | None:
        row = await (await self.connection.execute(
            "SELECT user_id FROM email_verification_tokens WHERE token_hash=%s", (digest,),
        )).fetchone()
        return row[0] if row and isinstance(row[0], UUID) else None

    async def verify_email(self, user_id: UUID, digest: bytes) -> bool:
        row = await (await self.connection.execute(
            """DELETE FROM email_verification_tokens WHERE user_id=%s AND token_hash=%s
            AND used_at IS NULL AND expires_at>clock_timestamp() RETURNING id""", (user_id, digest),
        )).fetchone()
        if row is None:
            return False
        await self.connection.execute(
            "UPDATE users SET email_verified=true,updated_at=clock_timestamp() WHERE id=%s", (user_id,),
        )
        return True
