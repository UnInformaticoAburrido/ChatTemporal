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
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    auth_method: str = "password"
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)

    def smtp_url(self) -> str:
        scheme = "smtps" if self.security == "tls" else "smtp"
        if self.auth_method == "oauth2":
            return f"{scheme}://{self.host}:{self.port}"
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
        common = {"enabled", "host", "port", "security", "sender"}
        allowed = common | {"username", "password", "auth_method", "client_id", "client_secret", "refresh_token"}
        if set(values) - allowed or type(values.get("enabled")) is not bool:
            raise ValueError
        if not values["enabled"]:
            return None
        if not common <= set(values):
            raise ValueError
        for key in ("host", "security", "sender"):
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
        method = values.get("auth_method", "password")
        if method not in {"password", "oauth2"}:
            raise ValueError
        for key in set(values) & {"username", "password", "client_id", "client_secret", "refresh_token"}:
            if not isinstance(values[key], str) or any(ord(c) < 32 or ord(c) == 127 for c in values[key]):
                raise ValueError
        required = {"username", "password"} if method == "password" else {"client_id", "client_secret", "refresh_token"}
        if not all(values.get(key) for key in required):
            raise ValueError
        if method == "oauth2":
            # OAuth2 de esta versión está limitado al servidor de Gmail.
            if values["host"] != "smtp.gmail.com" or values.get("password"):
                raise ValueError
            values.setdefault("username", values["sender"])
            if not values["username"] or any(c.isspace() for c in values["username"]):
                raise ValueError
        elif any(values.get(key) for key in ("client_id", "client_secret", "refresh_token")):
            raise ValueError
        return MailConfig(**{key: value for key, value in values.items() if key != "enabled"})
    except (ValueError, TypeError, OSError):
        # No incluir valores ni excepciones de TOML: podrían contener credenciales.
        raise ValueError("Configuración de correo inválida; revisa mail.local.toml") from None
