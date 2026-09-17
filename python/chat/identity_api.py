"""REST N2; cuotas antes de trabajo criptográfico y autenticación por sid."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from chat.auth_dependency import Authenticated as AuthDependency
from chat.config import Settings
from chat.errors import APIError, unavailable
from chat.identity import Identity, Principal
from chat.identity_crypto import canonical_uuid
from chat.identity_dto import (
    Exchange,
    ProfileUpdate,
    Recovery,
    Refresh,
    Registered,
    Registration,
    Ticket,
    TokenPair,
    UserPrivate,
    UserPublic,
    Verification,
)
from chat.identity_redis import client_ip


def identity_router(identity: Identity, settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.api_prefix)
    rates = settings.rate_limits

    Authenticated = Annotated[Principal, Depends(AuthDependency(identity, settings))]

    @router.post("/users/register", status_code=201)
    async def register(data: Registration, request: Request) -> Registered:
        await identity.redis.limit("register_ip", await client_ip(request, settings), rates.register_ip)
        return await identity.register(data)

    @router.get("/users/me")
    async def me(principal: Authenticated) -> UserPrivate:
        return UserPrivate.model_validate(principal.user, from_attributes=True)

    @router.patch("/users/me")
    async def update(data: ProfileUpdate, request: Request, principal: Authenticated) -> UserPrivate:
        if data.email is not None and data.email.casefold() != principal.user.email.casefold():
            # DEC-44: cambiar email dispara correo; aplicar las mismas cuotas que
            # resend para que PATCH no permita eludir la protección antiabuso.
            await identity.redis.limit("resend_user", str(principal.user.id), rates.resend_user)
            await identity.redis.limit("resend_ip", await client_ip(request, settings), rates.resend_ip)
        return await identity.update(principal, data)

    @router.delete("/users/me", status_code=204)
    async def delete(principal: Authenticated) -> Response:
        await identity.delete(principal)
        return Response(status_code=204)

    @router.post("/users/verify-email", status_code=204)
    async def verify(data: Verification) -> Response:
        await identity.verify_email(data.token)
        return Response(status_code=204)

    @router.post("/users/resend-verification", status_code=202)
    async def resend(request: Request, principal: Authenticated) -> Response:
        await identity.redis.limit("resend_user", str(principal.user.id), rates.resend_user)
        await identity.redis.limit("resend_ip", await client_ip(request, settings), rates.resend_ip)
        await identity.resend(principal)
        return Response(status_code=202)

    @router.get("/users/{user_id}")
    async def public_user(user_id: str, principal: Authenticated) -> UserPublic:
        try:
            target = canonical_uuid(user_id)
        except ValueError:
            raise APIError("USER_NOT_FOUND", 404, "User not found.") from None
        return await identity.public_user(principal, target)

    @router.post("/auth/exchange")
    async def exchange(data: Exchange, request: Request) -> TokenPair:
        await identity.redis.limit("exchange_ip", await client_ip(request, settings), rates.exchange_ip)
        return await identity.exchange(data.bootstrap_token)

    @router.post("/auth/recover")
    async def recover(data: Recovery, request: Request) -> TokenPair:
        await identity.redis.limit("recover_ip", await client_ip(request, settings), rates.recover_ip)
        await identity.redis.limit("recover_account", data.email.casefold(), rates.recover_account)
        return await identity.recover(data.email, data.recovery_mnemonic)

    @router.post("/auth/refresh")
    async def refresh(data: Refresh) -> TokenPair:
        return await identity.refresh(data.refresh_token)

    @router.post("/auth/logout", status_code=204)
    async def logout(principal: Authenticated) -> Response:
        await identity.logout(principal)
        return Response(status_code=204)

    @router.post("/auth/ws-ticket", status_code=201)
    async def ticket(request: Request, principal: Authenticated) -> Ticket:
        if request.app.state.draining:
            raise unavailable()
        await identity.redis.limit("ticket_session", str(principal.sid), rates.ticket_session)
        return await identity.ws_ticket(principal)

    return router
