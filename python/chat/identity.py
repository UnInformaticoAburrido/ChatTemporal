"""Casos de uso N2. Toda decisión durable de autenticación reside en PostgreSQL."""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from chat.config import Settings
from chat.errors import APIError
from chat.identity_crypto import Tokens, hash_phrase, new_phrase, opaque_token, token_hash, verify_phrase
from chat.identity_dto import (
    ProfileUpdate,
    Registered,
    Registration,
    Ticket,
    TokenPair,
    UserPrivate,
    UserPublic,
)
from chat.identity_redis import IdentityRedis
from chat.identity_store import IdentityStore
from chat.mailer import Mailer, SMTPMailer
from chat.persistence import User, transaction


@dataclass(frozen=True)
class Principal:
    user: User
    sid: UUID


class Identity:
    def __init__(self, settings: Settings, *, mailer: Mailer | None = None) -> None:
        self.settings = settings
        self.tokens = Tokens(settings)
        self.redis = IdentityRedis()
        self.mailer = mailer or SMTPMailer(settings.email_from)
        # DEC-40: como máximo dos hashes simultáneos por proceso (128 MiB), fuera
        # del event loop. Los límites Redis se aplican antes de llegar aquí.
        self.crypto_gate = asyncio.Semaphore(2)

    async def _session(self, store: IdentityStore, user_id: UUID) -> TokenPair:
        refresh = opaque_token()
        session = await store.create_session(user_id, token_hash(refresh))
        return TokenPair(access_token=self.tokens.access(user_id, session.id), refresh_token=refresh, sid=session.id)

    async def _verification(self, store: IdentityStore, user: User) -> None:
        token = opaque_token()
        await store.verification(user.id, token_hash(token))
        # DEC-38: SMTP se confirma antes del commit. Si falla, rollback completo
        # y 503; no crear una cuenta cuya única frase nunca llegó a la respuesta.
        await self.mailer.verification(user.email, token)

    async def register(self, data: Registration) -> Registered:
        phrase = new_phrase()
        async with self.crypto_gate:
            encoded = await asyncio.to_thread(hash_phrase, phrase)
        async with transaction() as unit:
            user = await unit.users.create(nick=data.nick, email=data.email, memory_hash=encoded)
            store = IdentityStore(unit.connection)
            tokens = await self._session(store, user.id)
            await self._verification(store, user)
        return Registered(user=UserPrivate.model_validate(user, from_attributes=True),
                          recovery_mnemonic=phrase, tokens=tokens)

    async def authenticate(self, access: str, *, transfer_upload: bool = False) -> Principal:
        claims = self.tokens.verify(access)
        assert claims.session_id is not None
        async with transaction() as unit:
            user = await IdentityStore(unit.connection).authenticated(claims.user_id, claims.session_id,
                                                                     transfer_upload=transfer_upload)
        return Principal(user, claims.session_id)

    async def recover(self, email: str, phrase: str) -> TokenPair:
        async with self.crypto_gate:
            async with transaction() as unit:
                store = IdentityStore(unit.connection)
                user = await store.by_email(email)
                if user is None:
                    # Mismo coste Argon2 para cuentas desconocidas; mismo error.
                    await asyncio.to_thread(hash_phrase, phrase)
                    valid = False
                else:
                    valid = await asyncio.to_thread(verify_phrase, user.memory_hash, phrase)
                if not valid or user is None or not user.is_active:
                    raise APIError("INVALID_RECOVERY_PHRASE", 401, "Invalid recovery credentials.")
                revoked = await store.revoke(user.id)
                tokens = await self._session(store, user.id)
        await self.redis.revoke(revoked)
        return tokens

    async def exchange(self, bootstrap: str) -> TokenPair:
        claims = self.tokens.verify(bootstrap, bootstrap=True)
        await self.redis.consume_bootstrap(claims.jti, claims.expires_at)
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            user = await store.user(claims.user_id, lock=True)
            if user is None or not user.is_active:
                raise APIError("TOKEN_INVALID", 401, "Invalid token.")
            # Una sola sesión normal; el predecesor solo podrá cargar el blob
            # cifrado para este sucesor mediante la dependencia exclusiva de PUT.
            source = await store.transfer_predecessor(user.id)
            revoked = await store.revoke(user.id)
            tokens = await self._session(store, user.id)
            await store.transfer_grant(user.id, source, tokens.sid)
        await self.redis.revoke(revoked)
        return tokens

    async def refresh(self, token: str) -> TokenPair:
        digest = token_hash(token)
        reused = False
        revoked: list[UUID] = []
        tokens: TokenPair | None = None
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            owner = await store.refresh_owner(digest)
            if owner is None:
                raise APIError("TOKEN_INVALID", 401, "Invalid token.")
            user = await store.user(owner, lock=True)
            record = await store.refresh(digest)
            if user is None or not user.is_active or record is None:
                raise APIError("TOKEN_INVALID", 401, "Invalid token.")
            session = await store.session(record.session_id)
            if session is None:
                raise APIError("SESSION_REVOKED", 401, "Session revoked or expired.")
            await self.redis.limit("refresh_session", str(session.id), self.settings.rate_limits.refresh_session)
            if record.used_at is not None:
                # DEC-42: NO lanzar la excepción dentro de la transacción: eso
                # revertiría la revocación que justamente debe persistir.
                revoked = await store.revoke(owner, family=record.token_family_id)
                reused = True
            elif (session.revoked_at is not None or record.revoked_at is not None
                  or session.expires_at.timestamp() <= time.time() or record.expires_at.timestamp() <= time.time()):
                raise APIError("SESSION_REVOKED", 401, "Session revoked or expired.")
            else:
                replacement = opaque_token()
                updated = await store.rotate(session, digest, token_hash(replacement))
                tokens = TokenPair(access_token=self.tokens.access(owner, updated.id),
                                   refresh_token=replacement, sid=updated.id)
        await self.redis.revoke(revoked)
        if reused:
            raise APIError("REFRESH_REUSE_DETECTED", 401, "Refresh token reuse detected.")
        assert tokens is not None
        return tokens

    async def logout(self, principal: Principal) -> None:
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            await store.authenticated(principal.user.id, principal.sid, lock=True)
            revoked = await store.revoke(principal.user.id, sid=principal.sid)
        await self.redis.revoke(revoked)

    async def verify_email(self, token: str) -> None:
        digest = token_hash(token)
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            owner = await store.verification_owner(digest)
            user = await store.user(owner, lock=True) if owner else None
            if (user is None or not user.is_active or not await store.verify_email(user.id, digest)):
                raise APIError("TOKEN_INVALID", 401, "Invalid verification token.")

    async def resend(self, principal: Principal) -> None:
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            user = await store.authenticated(principal.user.id, principal.sid, lock=True)
            await self._verification(store, user)

    async def update(self, principal: Principal, data: ProfileUpdate) -> UserPrivate:
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            user = await store.authenticated(principal.user.id, principal.sid, lock=True)
            changed_email = data.email is not None and data.email.casefold() != user.email.casefold()
            await unit.connection.execute(
                """UPDATE users SET nick=COALESCE(%s,nick),email=COALESCE(%s,email),
                email_verified=CASE WHEN %s THEN false ELSE email_verified END,
                updated_at=clock_timestamp() WHERE id=%s""", (data.nick, data.email, changed_email, user.id),
            )
            updated = await store.user(user.id)
            assert updated is not None
            if changed_email:
                await self._verification(store, updated)
        return UserPrivate.model_validate(updated, from_attributes=True)

    async def delete(self, principal: Principal) -> None:
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            await store.authenticated(principal.user.id, principal.sid, lock=True)
            revoked = await store.revoke(principal.user.id)
            await unit.connection.execute("DELETE FROM users WHERE id=%s", (principal.user.id,))
        await self.redis.revoke(revoked)

    async def public_user(self, principal: Principal, target: UUID) -> UserPublic:
        async with transaction() as unit:
            user = await unit.users.get(target)
            visible = target == principal.user.id
            if not visible:
                # §25.6: compartir pending NO permite revelar el perfil. Las
                # claves públicas tienen su contrato diferente en N3.
                visible = bool(await (await unit.connection.execute(
                    """SELECT 1 FROM conversation_members me JOIN conversation_members peer
                    ON me.conversation_id=peer.conversation_id
                    JOIN conversations c ON c.id=me.conversation_id
                    WHERE me.user_id=%s AND peer.user_id=%s AND c.status IN ('active','closed')
                    AND c.accepted_at IS NOT NULL
                    AND me.membership_status<>'left' LIMIT 1""", (principal.user.id, target),
                )).fetchone())
            if user is None or not user.is_active or not visible:
                raise APIError("USER_NOT_FOUND", 404, "User not found.")
        return UserPublic(id=user.id, nick=user.nick)

    async def ws_ticket(self, principal: Principal) -> Ticket:
        async with transaction() as unit:
            user = await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid, lock=True)
            if not user.email_verified:
                raise APIError("EMAIL_NOT_VERIFIED", 403, "Email verification required.")
            ticket = opaque_token()
            expires = datetime.now(UTC) + timedelta(seconds=30)
            await self.redis.ticket(token_hash(ticket), user.id, principal.sid)
        return Ticket(ticket=ticket, expires_at=expires)
