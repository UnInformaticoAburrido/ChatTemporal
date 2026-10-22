"""Emisor de main-app: privada independiente, sub mapeado por el servidor anfitrión.

La API de chat no instancia este emisor ni recibe/monta su clave privada.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass(frozen=True)
class BootstrapIssuer:
    private_key_file: Path = field(repr=False)
    kid: str
    ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if (not isinstance(self.private_key_file, Path) or not isinstance(self.kid, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.kid) is None
                or type(self.ttl_seconds) is not int or not 1 <= self.ttl_seconds <= 300):
            raise ValueError("Invalid bootstrap configuration")

    def issue(self, chat_user_id: UUID) -> str:
        """El llamador autentica al usuario y resuelve su UUID de chat; nunca un sub libre."""
        if not isinstance(chat_user_id, UUID):
            raise ValueError("Chat UUID required")
        try:
            # Lectura por emisión: permite reemplazar la clave sin cachear la privada.
            with self.private_key_file.open("rb") as source:
                pem = source.read(4097)
            if len(pem) > 4096:
                raise ValueError()
            private = serialization.load_pem_private_key(pem, password=None)
            if not isinstance(private, Ed25519PrivateKey):
                raise ValueError()
            stamp = int(time.time())
            return jwt.encode({"sub": str(chat_user_id), "iss": "main-app", "aud": "chat-api",
                               "iat": stamp, "nbf": stamp, "exp": stamp + self.ttl_seconds,
                               "jti": str(uuid4())}, private, algorithm="EdDSA",
                              headers={"typ": "JWT", "kid": self.kid})
        except (OSError, ValueError, TypeError, jwt.PyJWTError):
            raise ValueError("Bootstrap signing unavailable") from None
