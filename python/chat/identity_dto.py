"""Contratos de identidad de §25.2; extras prohibidos y respuestas sin hashes."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Nick = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{3,32}$", min_length=3, max_length=32)]


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmailDTO(StrictDTO):
    email: str = Field(max_length=254, repr=False)

    @field_validator("email")
    @classmethod
    def email_syntax(cls, value: str) -> str:
        try:
            # §25.1 exige sintaxis, no resolución DNS ni entrega durante validación.
            return str(validate_email(value, check_deliverability=False).normalized)
        except EmailNotValidError:
            raise ValueError("Invalid email address") from None


class Registration(EmailDTO):
    nick: Nick


class Recovery(EmailDTO):
    recovery_mnemonic: str = Field(min_length=1, max_length=2048, repr=False)


class ProfileUpdate(StrictDTO):
    nick: Nick | None = None
    email: str | None = Field(default=None, max_length=254, repr=False)

    @model_validator(mode="after")
    def validate_update(self) -> "ProfileUpdate":
        if not self.model_fields_set or any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Provide at least one non-null field")
        if self.email is not None:
            self.email = EmailDTO(email=self.email).email
        return self


class Refresh(StrictDTO):
    refresh_token: str = Field(min_length=1, max_length=128, repr=False)


class Verification(StrictDTO):
    token: str = Field(min_length=1, max_length=128, repr=False)


class Exchange(StrictDTO):
    bootstrap_token: str = Field(min_length=1, max_length=8192, repr=False)


class TokenPair(StrictDTO):
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = 86400
    refresh_expires_in: int = 2592000
    sid: UUID


class UserPrivate(StrictDTO):
    id: UUID
    nick: str
    email: str = Field(repr=False)
    email_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserPublic(StrictDTO):
    id: UUID
    nick: str


class Registered(StrictDTO):
    user: UserPrivate
    recovery_mnemonic: str = Field(repr=False)
    tokens: TokenPair


class Ticket(StrictDTO):
    ticket: str = Field(repr=False)
    expires_at: datetime
