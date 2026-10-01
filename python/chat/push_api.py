"""Suscripciones Web Push estrictas, propiedad del usuario y sesión vigente."""

from typing import Annotated
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, Response
from pydantic import Field, field_validator

from chat.auth_dependency import Authenticated
from chat.config import Settings
from chat.errors import APIError
from chat.identity import Identity, Principal
from chat.identity_dto import StrictDTO
from chat.identity_store import IdentityStore
from chat.persistence import transaction
from chat.protocol import CanonicalUUID, decode_binary
from chat.web_push import endpoint_url


class Subscription(StrictDTO):
    endpoint: str = Field(repr=False)
    p256dh: str = Field(repr=False)
    auth_secret: str = Field(repr=False)

    @field_validator("endpoint")
    @classmethod
    def check_endpoint(cls, value: str) -> str:
        endpoint_url(value)
        return value

    @field_validator("p256dh")
    @classmethod
    def check_public(cls, value: str) -> str:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode_binary(value, minimum=65, maximum=65))
        return value

    @field_validator("auth_secret")
    @classmethod
    def check_auth(cls, value: str) -> str:
        decode_binary(value, minimum=16, maximum=16)
        return value


class SubscriptionID(StrictDTO):
    id: CanonicalUUID


def push_router(identity: Identity, settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.api_prefix)
    Member = Annotated[Principal, Depends(Authenticated(identity, settings))]

    @router.post("/push/subscriptions", status_code=201)
    async def subscribe(data: Subscription, principal: Member) -> SubscriptionID:
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid, lock=True)
            row = await (await unit.connection.execute("""INSERT INTO web_push_subscriptions
                (id,user_id,session_id,endpoint,p256dh,auth_secret) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh,auth_secret=excluded.auth_secret,
                    session_id=excluded.session_id,revoked_at=NULL,last_seen_at=clock_timestamp()
                WHERE web_push_subscriptions.user_id=excluded.user_id RETURNING id""",
                (uuid4(), principal.user.id, principal.sid, data.endpoint, data.p256dh, data.auth_secret))).fetchone()
            if row is None:
                raise APIError("PUSH_SUBSCRIPTION_CONFLICT", 409, "Subscription unavailable.")
        assert isinstance(row[0], UUID)
        return SubscriptionID(id=row[0])

    @router.delete("/push/subscriptions/{subscription_id}", status_code=204)
    async def unsubscribe(subscription_id: CanonicalUUID, principal: Member) -> Response:
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid, lock=True)
            row = await (await unit.connection.execute("""UPDATE web_push_subscriptions
                SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE id=%s AND user_id=%s RETURNING id""",
                (subscription_id, principal.user.id))).fetchone()
            if row is None:
                raise APIError("PUSH_SUBSCRIPTION_NOT_FOUND", 404, "Subscription not found.")
        return Response(status_code=204)

    return router
