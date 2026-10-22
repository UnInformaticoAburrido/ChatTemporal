"""SMTP con TLS, timeout y errores seguros. No registrar contenido del correo."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import unquote, urlsplit

from chat.config import read_secret, use_local_mailbox
from chat.errors import unavailable


class Mailer(Protocol):
    async def verification(self, email: str, token: str) -> None: ...


class SMTPMailer:
    def __init__(self, sender: str) -> None:
        self.sender = sender

    async def verification(self, email: str, token: str) -> None:
        await asyncio.to_thread(self._send, email, token)

    def _send(self, email: str, token: str) -> None:
        try:
            smtp_url = read_secret("SMTP_URL")
            local_mailbox = use_local_mailbox(smtp_url)
            url = urlsplit("smtp://mailpit:1025" if local_mailbox else smtp_url)
            if url.scheme not in {"smtp", "smtps"} or not url.hostname:
                raise ValueError()
            message = EmailMessage()
            message["From"] = self.sender
            message["To"] = email
            message["Subject"] = "Verificación de tu cuenta de chat"
            # El cliente permite introducir este código en su formulario de verificación.
            message.set_content(f"Tu código de verificación es:\n\n{token}\n\nCaduca en 30 minutos.")
            context = ssl.create_default_context()
            connection: smtplib.SMTP
            if url.scheme == "smtps":
                connection = smtplib.SMTP_SSL(url.hostname, url.port or 465, timeout=5, context=context)
            else:
                connection = smtplib.SMTP(url.hostname, url.port or 587, timeout=5)
            with connection:
                if url.scheme == "smtp" and not local_mailbox:
                    connection.starttls(context=context)
                if url.username is not None:
                    connection.login(unquote(url.username), unquote(url.password or ""))
                connection.send_message(message)
        except (OSError, smtplib.SMTPException, ValueError):
            raise unavailable() from None
