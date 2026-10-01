"""Endpoints normativos de transferencia (§25.3)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import Field

from chat.auth_dependency import Authenticated
from chat.config import Settings
from chat.identity import Identity, Principal
from chat.identity_dto import StrictDTO
from chat.protocol import CanonicalUUID
from chat.transfers import TransferBlob, TransferCreated, Transfers


class UploadBlob(StrictDTO):
    encrypted_blob: str = Field(repr=False)


def recovery_router(identity: Identity, settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.api_prefix)
    service = Transfers(settings)
    Member = Annotated[Principal, Depends(Authenticated(identity, settings))]
    Uploader = Annotated[Principal, Depends(Authenticated(identity, settings, transfer_upload=True))]

    @router.post("/key-transfers", status_code=201)
    async def create(principal: Member) -> TransferCreated:
        return await service.create(principal)

    @router.put("/key-transfers/{transfer_id}", status_code=204)
    async def upload(transfer_id: CanonicalUUID, data: UploadBlob, principal: Uploader) -> Response:
        await service.access(principal, transfer_id, "put", data.encrypted_blob)
        return Response(status_code=204)

    @router.get("/key-transfers/{transfer_id}")
    async def download(transfer_id: CanonicalUUID, principal: Member) -> TransferBlob:
        result = await service.access(principal, transfer_id, "get")
        assert result
        return result

    @router.delete("/key-transfers/{transfer_id}", status_code=204)
    async def delete(transfer_id: CanonicalUUID, principal: Member) -> Response:
        await service.access(principal, transfer_id, "delete")
        return Response(status_code=204)

    return router
