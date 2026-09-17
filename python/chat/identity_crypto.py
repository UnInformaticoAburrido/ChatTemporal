"""§24: formatos criptográficos; no IO de red ni persistencia de tokens originales."""

import base64
import binascii
import os
import secrets
import time
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mnemonic import Mnemonic

from chat.config import Settings
from chat.errors import APIError

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1,
                        salt_len=16, hash_len=32, type=Type.ID)
WORDS = Mnemonic("english")


def canonical_phrase(phrase: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", phrase).split())


def new_phrase() -> str:
    # DEC-32: biblioteca BIP-39 de referencia; entropía explícita del CSPRNG.
    return WORDS.to_mnemonic(secrets.token_bytes(32))


def hash_phrase(phrase: str) -> str:
    return HASHER.hash(canonical_phrase(phrase))


def verify_phrase(encoded: str, phrase: str) -> bool:
    try:
        return HASHER.verify(encoded, canonical_phrase(phrase))
    except (VerificationError, InvalidHashError):
        return False


def opaque_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def token_hash(token: str) -> bytes:
    try:
        if len(token) != 43:
            raise ValueError()
        raw = base64.b64decode(token + "=", altchars=b"-_", validate=True)
        if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != token:
            raise ValueError()
    except (ValueError, binascii.Error):
        raise APIError("TOKEN_INVALID", 401, "Invalid token.") from None
    return sha256(raw).digest()


def canonical_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError()
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError()
    return parsed


@dataclass(frozen=True)
class Claims:
    user_id: UUID
    session_id: UUID | None
    jti: UUID
    expires_at: int


class Tokens:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def access(self, user_id: UUID, session_id: UUID) -> str:
        key = serialization.load_pem_private_key(
            Path(os.environ["CHAT_JWT_PRIVATE_KEY_FILE"]).read_bytes(), password=None,
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Chat requires Ed25519")
        stamp = int(time.time())
        return jwt.encode({
            "sub": str(user_id), "sid": str(session_id), "iss": "chat-api", "aud": "chat-client",
            "iat": stamp, "nbf": stamp, "exp": stamp + 86400, "jti": str(uuid4()),
        }, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": self.settings.chat_jwt_active_kid})

    def verify(self, token: str, *, bootstrap: bool = False) -> Claims:
        try:
            if len(token) > 8192:
                raise ValueError()
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (header.get("alg") != "EdDSA" or header.get("typ") != "JWT"
                    or not isinstance(kid, str) or not kid or Path(kid).name != kid
                    or "/" in kid or "\\" in kid or "crit" in header):
                raise ValueError()
            directory = "BOOTSTRAP_PUBLIC_KEYS_DIR" if bootstrap else "CHAT_JWT_PUBLIC_KEYS_DIR"
            # DEC-33: leer las públicas locales en cada validación permite retirar
            # un kid comprometido sin mantener una caché que siga aceptándolo.
            key = serialization.load_pem_public_key((Path(os.environ[directory]) / f"{kid}.pem").read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError()
            required = ["sub", "iss", "aud", "iat", "nbf", "exp", "jti"]
            if not bootstrap:
                required.append("sid")
            data = jwt.decode(token, key, algorithms=["EdDSA"],
                              issuer="main-app" if bootstrap else "chat-api",
                              audience="chat-api" if bootstrap else "chat-client", leeway=30,
                              options={"require": required, "strict_aud": True})
            if any(type(data[name]) is not int for name in ("iat", "nbf", "exp")):
                raise ValueError()
            ttl = data["exp"] - data["iat"]
            if bootstrap:
                # No consumir un jti cuyo TTL ya venció, aunque esté dentro del skew.
                if not 0 < ttl <= self.settings.bootstrap_max_ttl_seconds or data["exp"] <= time.time():
                    raise ValueError()
            elif ttl != 86400:
                raise ValueError()
            if not data["iat"] <= data["nbf"] < data["exp"]:
                raise ValueError()
            return Claims(canonical_uuid(data["sub"]),
                          None if bootstrap else canonical_uuid(data["sid"]),
                          canonical_uuid(data["jti"]), data["exp"])
        except jwt.ExpiredSignatureError:
            raise APIError("TOKEN_EXPIRED", 401, "Token expired.") from None
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OSError):
            raise APIError("TOKEN_INVALID", 401, "Invalid token.") from None
