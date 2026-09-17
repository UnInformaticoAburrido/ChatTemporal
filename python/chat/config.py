"""DEC-10: configuración pública TOML; los secretos se leen exclusivamente de archivos."""

import base64
import os
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RateRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    limit: int = Field(gt=0)
    seconds: int = Field(gt=0)
    burst: int = Field(default=0, ge=0)


class RateLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authenticated: RateRule = RateRule(limit=120, seconds=60, burst=30)
    register_ip: RateRule = RateRule(limit=10, seconds=3600)
    resend_user: RateRule = RateRule(limit=5, seconds=3600)
    resend_ip: RateRule = RateRule(limit=20, seconds=3600)
    recover_account: RateRule = RateRule(limit=5, seconds=900)
    recover_ip: RateRule = RateRule(limit=20, seconds=900)
    exchange_ip: RateRule = RateRule(limit=30, seconds=60)
    refresh_session: RateRule = RateRule(limit=30, seconds=60)
    redeem_user: RateRule = RateRule(limit=30, seconds=60)
    redeem_ip: RateRule = RateRule(limit=60, seconds=60)
    ticket_session: RateRule = RateRule(limit=20, seconds=60)
    messages_user: RateRule = RateRule(limit=120, seconds=60, burst=20)
    replay: RateRule = RateRule(limit=100, seconds=1)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    api_prefix: str = "/api/v1"
    ws_path: str = "/ws/v1"
    chat_jwt_active_kid: str
    allowed_origins: list[str]
    vapid_public_key: str
    vapid_subject: str
    email_from: str
    access_token_ttl_seconds: int = Field(default=86400, ge=86400, le=86400)
    refresh_token_ttl_seconds: int = Field(default=2592000, ge=2592000, le=2592000)
    bootstrap_max_ttl_seconds: int = Field(default=300, gt=0, le=300)
    ws_ticket_ttl_seconds: int = Field(default=30, ge=30, le=30)
    ephemeral_offer_timeout_seconds: int = Field(default=60, gt=0)
    ephemeral_delivery_timeout_seconds: int = Field(default=60, gt=0)
    ws_ping_interval_seconds: int = Field(default=25, gt=0)
    ws_dead_after_seconds: int = Field(default=75, gt=0)
    presence_ttl_seconds: int = Field(default=90, gt=0, le=90)
    max_message_length: int = Field(default=256, gt=0)
    max_envelope_bytes: int = Field(default=8192, gt=0)
    max_http_body_bytes: int = Field(default=131072, gt=0, le=131072)
    max_key_transfer_blob_bytes: int = Field(default=65536, gt=0, le=65536)
    pagination_default_limit: int = Field(default=50, gt=0)
    pagination_max_limit: int = Field(default=100, gt=0, le=100)
    log_level: str = "INFO"
    startup_timeout_seconds: int = Field(default=120, gt=0)
    dependency_timeout_seconds: int = Field(default=2, gt=0, le=5)
    cleanup_interval_seconds: int = Field(default=30, gt=0, le=60)
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    trusted_proxy_host: str = "caddy"

    @model_validator(mode="after")
    def check_consistency(self) -> "Settings":
        if self.api_prefix != "/api/v1" or self.ws_path != "/ws/v1":
            raise ValueError("Las rutas v1 están fijadas por la especificación")
        if not self.ws_ping_interval_seconds < self.ws_dead_after_seconds < self.presence_ttl_seconds:
            raise ValueError("Heartbeat incompatible con presencia")
        if self.pagination_default_limit > self.pagination_max_limit:
            raise ValueError("Paginación incompatible")
        if not self.allowed_origins:
            raise ValueError("Es obligatorio declarar orígenes")
        for origin in self.allowed_origins:
            parts = urlsplit(origin)
            if parts.scheme not in {"https", "http"} or not parts.hostname or "*" in origin:
                raise ValueError("Origen inválido")
            if parts.path or parts.query or parts.fragment or parts.username or parts.password:
                raise ValueError("El origen no admite ruta, credenciales ni query")
        return self


def load_settings() -> Settings:
    path = Path(os.environ.get("CHAT_SETTINGS_FILE", "/etc/chat/settings.toml"))
    with path.open("rb") as handle:
        settings = Settings.model_validate(tomllib.load(handle))
    # DEC-22: sincronizar los orígenes loopback con el puerto elegido en Compose
    # solo en desarrollo. Producción conserva sus orígenes HTTPS explícitos.
    if os.environ.get("APP_ENV") == "development" and "CHAT_LOCAL_HTTP_PORT" in os.environ:
        port = int(os.environ["CHAT_LOCAL_HTTP_PORT"])
        if not 1 <= port <= 65535:
            raise ValueError("Puerto HTTP local inválido")
        for host in ("localhost", "127.0.0.1"):
            origin = f"http://{host}:{port}"
            if origin not in settings.allowed_origins:
                settings.allowed_origins.append(origin)
    return settings


def read_secret(name: str) -> str:
    return Path(os.environ[name + "_FILE"]).read_text().strip()


def validate_secrets(settings: Settings) -> None:
    """Antes de abrir puertos (§27.2); no imprimir excepciones con sus valores."""
    environment = os.environ.get("APP_ENV", "production")
    if environment not in {"production", "staging", "development", "test"}:
        raise ValueError("APP_ENV inválido")
    production = environment == "production"
    for name, schemes in (("DATABASE_URL", {"postgresql"}), ("REDIS_URL", {"redis"}),
                          ("SMTP_URL", {"smtp", "smtps"})):
        parsed = urlsplit(read_secret(name))
        if parsed.scheme not in schemes or not parsed.hostname:
            raise ValueError("URL de servicio inválida")
        if name != "SMTP_URL" and (not parsed.password or len(parsed.password) < 32):
            raise ValueError("Credencial de servicio insuficiente")
        if production and parsed.hostname.endswith(".invalid"):
            raise ValueError("Proveedor pendiente de configurar")
    private = serialization.load_pem_private_key(
        Path(os.environ["CHAT_JWT_PRIVATE_KEY_FILE"]).read_bytes(), password=None
    )
    if not isinstance(private, ed25519.Ed25519PrivateKey):
        raise ValueError("Chat requiere Ed25519")
    public_dir = Path(os.environ["CHAT_JWT_PUBLIC_KEYS_DIR"])
    if Path(settings.chat_jwt_active_kid).name != settings.chat_jwt_active_kid:
        raise ValueError("kid inválido")
    active = serialization.load_pem_public_key(
        (public_dir / (settings.chat_jwt_active_kid + ".pem")).read_bytes()
    )
    if not isinstance(active, ed25519.Ed25519PublicKey):
        raise ValueError("Pública Chat inválida")
    if private.public_key().public_bytes_raw() != active.public_bytes_raw():
        raise ValueError("Par Chat incompatible")
    chat_keys = set()
    for path in public_dir.glob("*.pem"):
        key = serialization.load_pem_public_key(path.read_bytes())
        if not isinstance(key, ed25519.Ed25519PublicKey):
            raise ValueError("Pública Chat inválida")
        chat_keys.add(key.public_bytes_raw())
    bootstrap_paths = list(Path(os.environ["BOOTSTRAP_PUBLIC_KEYS_DIR"]).glob("*.pem"))
    if not bootstrap_paths:
        raise ValueError("Bootstrap necesita al menos una pública")
    for path in bootstrap_paths:
        key = serialization.load_pem_public_key(path.read_bytes())
        if not isinstance(key, ed25519.Ed25519PublicKey) or key.public_bytes_raw() in chat_keys:
            raise ValueError("Bootstrap debe usar un par Ed25519 independiente")
    if len(Path(os.environ["INVITATION_HMAC_SECRET_FILE"]).read_bytes()) < 32:
        raise ValueError("HMAC insuficiente")
    vapid = serialization.load_pem_private_key(
        Path(os.environ["VAPID_PRIVATE_KEY_FILE"]).read_bytes(), password=None
    )
    if not isinstance(vapid, ec.EllipticCurvePrivateKey) or not isinstance(vapid.curve, ec.SECP256R1):
        raise ValueError("VAPID requiere P-256")
    raw = vapid.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != settings.vapid_public_key:
        raise ValueError("Par VAPID incompatible")
    if not settings.vapid_subject.startswith(("mailto:", "https://")) or "@" not in settings.email_from:
        raise ValueError("Contacto operativo inválido")
    if production:
        values = [*settings.allowed_origins, settings.email_from, settings.vapid_subject]
        if any(".invalid" in value for value in values):
            raise ValueError("Configuración de ejemplo en producción")
        if any(not origin.startswith("https://") for origin in settings.allowed_origins):
            raise ValueError("Producción requiere HTTPS")


def public_config() -> dict[str, Any]:
    """Solo para herramientas locales; nunca incluye credenciales."""
    return load_settings().model_dump()
