"""Una misma autenticación y cuota por usuario para todos los routers REST."""

from fastapi import Request

from chat.config import Settings
from chat.errors import APIError
from chat.identity import Identity, Principal


class Authenticated:
    def __init__(self, identity: Identity, settings: Settings, *, transfer_upload: bool = False) -> None:
        self.identity = identity
        self.settings = settings
        self.transfer_upload = transfer_upload

    async def __call__(self, request: Request) -> Principal:
        authorization = request.headers.get("Authorization", "").split()
        if len(authorization) != 2 or authorization[0].lower() != "bearer":
            raise APIError("AUTH_REQUIRED", 401, "Authentication required.")
        principal = await self.identity.authenticate(authorization[1], transfer_upload=self.transfer_upload)
        await self.identity.redis.limit("authenticated", str(principal.user.id), self.settings.rate_limits.authenticated)
        return principal
