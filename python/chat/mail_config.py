"""Configuración SMTP opcional en un archivo privado, separada de la plantilla."""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str
    sender: str
    username: str = field(repr=False)
    password: str = field(repr=False)

    def smtp_url(self) -> str:
        scheme = "smtps" if self.security == "tls" else "smtp"
        return f"{scheme}://{quote(self.username, safe='')}:{quote(self.password, safe='')}@{self.host}:{self.port}"


def load_mail_config() -> MailConfig | None:
    filename = os.environ.get("CHAT_MAIL_CONFIG_FILE")
    if not filename:
        return None
    path = Path(filename)
    if not path.exists():
        return None
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
        allowed = {"enabled", "host", "port", "security", "sender", "username", "password"}
        if set(values) - allowed or type(values.get("enabled")) is not bool:
            raise ValueError
        if not values["enabled"]:
            return None
        if set(values) != allowed:
            raise ValueError
        for key in ("host", "security", "sender", "username", "password"):
            if not isinstance(values[key], str) or not values[key] or any(ord(c) < 32 for c in values[key]):
                raise ValueError
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", values["host"]):
            raise ValueError
        if values["host"].endswith(".invalid") or values["sender"].endswith(".invalid"):
            raise ValueError
        if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", values["sender"]):
            raise ValueError
        if type(values["port"]) is not int or not 1 <= values["port"] <= 65535:
            raise ValueError
        if values["security"] not in {"starttls", "tls"}:
            raise ValueError
        return MailConfig(**{key: values[key] for key in allowed - {"enabled"}})
    except (ValueError, TypeError, OSError):
        # No incluir valores ni excepciones de TOML: podrían contener credenciales.
        raise ValueError("Configuración de correo inválida; revisa mail.local.toml") from None
