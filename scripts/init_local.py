#!/usr/bin/env python3
"""DEC-16: genera secretos solo locales usando CSPRNG y OpenSSL, sin mostrarlos."""

import argparse
import base64
import os
import secrets
import subprocess
from pathlib import Path


def initialize(root: Path) -> None:
    secret_dir = root / "secrets"
    targets = ["postgres_password", "database_url", "redis_password", "redis_url",
               "chat_jwt_private.pem", "invitation_hmac", "vapid_private.pem", "smtp_url",
               "bootstrap_local_private.pem", "chat-public/chat-local-1.pem",
               "bootstrap-public/main-local-1.pem"]
    config_path = root / "python/config/settings.local.toml"
    if config_path.exists() or any((secret_dir / name).exists() for name in targets):
        raise FileExistsError("Ya existe configuración local; no se sobrescribirá")
    # TOML público original se conserva para no contaminar producción con la demo.
    template = (root / "python/config/settings.toml").read_text()
    old_umask = os.umask(0o077)
    try:
        for directory in (secret_dir, secret_dir / "chat-public", secret_dir / "bootstrap-public"):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)

        def write(name: str, value: str | bytes) -> None:
            path = secret_dir / name
            with path.open("xb") as stream:
                stream.write(value.encode() if isinstance(value, str) else value)

        def openssl(*arguments: str) -> bytes:
            return subprocess.run(["openssl", *arguments], check=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL).stdout

        pg_password = secrets.token_urlsafe(48)
        redis_password = secrets.token_urlsafe(48)
        write("postgres_password", pg_password)
        write("database_url", f"postgresql://chat:{pg_password}@postgresql:5432/chat")
        write("redis_password", redis_password)
        write("redis_url", f"redis://:{redis_password}@redis:6379/0")
        write("smtp_url", "smtp://pendiente.invalid:587")
        write("invitation_hmac", secrets.token_bytes(48))
        for private_name, public_name in (
            ("chat_jwt_private.pem", "chat-public/chat-local-1.pem"),
            ("bootstrap_local_private.pem", "bootstrap-public/main-local-1.pem"),
        ):
            write(private_name, openssl("genpkey", "-algorithm", "ED25519"))
            write(public_name, openssl("pkey", "-in", str(secret_dir / private_name), "-pubout"))
        write("vapid_private.pem", openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"))
        der = openssl("pkey", "-in", str(secret_dir / "vapid_private.pem"), "-pubout", "-outform", "DER")
        public_vapid = base64.urlsafe_b64encode(der[-65:]).decode().rstrip("=")
        config_path.write_text(template.replace('"CONFIGURAR"', f'"{public_vapid}"').replace(
            '["https://chat.example.invalid"]', '["http://localhost:18080", "http://127.0.0.1:18080"]'
        ))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        initialize(args.directory)
    except FileExistsError as exc:
        parser.exit(1, str(exc) + "\n")
    print("Configuración LOCAL generada; no habilita correo ni despliegue de producción.")
