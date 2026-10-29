"""Renovación de tokens Gmail: HTTPS, endpoint fijo y sin persistencia ni logs."""

import json
import urllib.request
from urllib.parse import urlencode

from chat.mail_config import MailConfig


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise ValueError("Redirección OAuth2 no permitida")


def gmail_access_token(config: MailConfig) -> str:
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urlencode({"grant_type": "refresh_token", "client_id": config.client_id,
                        "client_secret": config.client_secret, "refresh_token": config.refresh_token}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=10) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Respuesta OAuth2 inválida")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Respuesta OAuth2 inválida")
    token = payload.get("access_token")
    token_type = payload.get("token_type")
    if (not isinstance(token, str) or not token or not token.isascii()
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token)
            or not isinstance(token_type, str) or token_type.lower() != "bearer"):
        raise ValueError("Respuesta OAuth2 inválida")
    return token
