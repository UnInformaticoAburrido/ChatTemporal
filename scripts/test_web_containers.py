"""E2E web con dependencias Docker nuevas; nunca reutiliza secretos/volúmenes del proyecto."""

import contextlib
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]


def configuration(directory: Path) -> dict:
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    pg = base["postgresql"]
    redis = base["redis"]
    return {"services": {
        "postgresql": {
            "image": pg["image"], "build": {"context": str(ROOT / "BD/postgresql")},
            "environment": {"POSTGRES_USER": "e2e", "POSTGRES_DB": "e2e",
                            "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
                            "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
            "ports": ["127.0.0.1::5432"],
            "tmpfs": ["/var/lib/postgresql/data:size=256m,mode=0700"],
            "volumes": [f"{directory / 'postgres_password'}:/run/secrets/postgres_password:ro",
                        f"{ROOT / 'BD/postgresql/postgresql.conf'}:/etc/postgresql/postgresql.conf:ro"],
            "command": pg["command"],
            "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U e2e -d e2e"],
                            "interval": "2s", "timeout": "3s", "retries": 60},
        },
        "redis": {
            "image": redis["image"], "user": redis["user"], "read_only": True,
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
            "ports": ["127.0.0.1::6379"], "tmpfs": redis["tmpfs"],
            "volumes": [f"{ROOT / 'BD/redis'}:/etc/redis:ro",
                        f"{directory / 'redis_password'}:/run/secrets/redis_password:ro"],
            "command": redis["command"], "healthcheck": redis["healthcheck"],
            "mem_limit": redis["mem_limit"],
        },
    }}


def execute(args: list[str], *, timeout: int = 180) -> str:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Salidas de Docker/migraciones pueden contener configuración sensible.
        raise RuntimeError("Falló una operación del entorno E2E; salida privada omitida")
    return result.stdout


def port(compose: list[str], service: str, number: int) -> int:
    address = execute([*compose, "port", service, str(number)]).strip()
    host, value = address.rsplit(":", 1)
    if host != "127.0.0.1" or not 1 <= int(value) <= 65535:
        raise RuntimeError("La dependencia debe publicar únicamente en loopback")
    return int(value)


def run() -> int:
    # UUID evita cruzar recursos con cualquier instancia existente de Chat.
    project = "chat-web-e2e-" + uuid4().hex
    print(f"Proyecto temporal: {project}", flush=True)
    with tempfile.TemporaryDirectory(prefix="chat-web-e2e-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        passwords = {name: secrets.token_urlsafe(32) for name in ("postgres_password", "redis_password")}
        for name, value in passwords.items():
            path = directory / name
            path.write_text(value)
            # El padre privado impide lectura en el host; el montaje permite UID del contenedor.
            path.chmod(0o444)
        config = directory / "compose.json"
        config.write_text(json.dumps(configuration(directory)))
        compose = ["docker", "compose", "-p", project, "-f", str(config)]
        try:
            print("Construyendo dependencias aisladas de E2E…", flush=True)
            execute([*compose, "build", "postgresql"], timeout=600)
            execute([*compose, "up", "-d", "--wait", "--wait-timeout", "180"], timeout=240)
            database = directory / "database_url"
            redis = directory / "redis_url"
            database.write_text(f"postgresql://e2e:{passwords['postgres_password']}@127.0.0.1:"
                                f"{port(compose, 'postgresql', 5432)}/e2e")
            redis.write_text(f"redis://:{passwords['redis_password']}@127.0.0.1:"
                             f"{port(compose, 'redis', 6379)}/0")
            database.chmod(0o600)
            redis.chmod(0o600)
            environment = {**os.environ, "APP_ENV": "test", "RUN_INTEGRATION": "1", "RUN_WEB_E2E": "1",
                           "DATABASE_URL_FILE": str(database), "REDIS_URL_FILE": str(redis),
                           "MIGRATIONS_DIR": str(ROOT / "BD/postgresql/migrations")}
            migration = subprocess.run([sys.executable, "-c", "from alembic import command; "
                "from chat.bootstrap import migration_config; command.upgrade(migration_config(), 'head')"],
                cwd=ROOT / "python", env=environment, capture_output=True, timeout=60)
            if migration.returncode:
                raise RuntimeError("Falló la migración aislada; salida privada omitida")
            print("Ejecutando navegador con API/WS, PostgreSQL y Redis reales…", flush=True)
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s", "--tb=short",
                "-p", "no:cacheprovider", "tests/test_web_client_integration.py"],
                cwd=ROOT / "python", env=environment, timeout=660)
            return result.returncode
        finally:
            # Solo el proyecto recién creado; no down global ni borrado de volúmenes ajenos.
            execute([*compose, "down", "--remove-orphans", "--timeout", "5"])


def interrupted(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(run())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
        print("No se pudo completar E2E aislado. Revisar Docker, dependencias y navegador.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        with contextlib.suppress(BrokenPipeError):
            print("E2E interrumpido; limpieza del proyecto solicitada.", file=sys.stderr)
        sys.exit(130)
