"""N8: datos por lista positiva, snapshot único y AES-256-GCM en streaming."""

import argparse
import contextlib
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

import psycopg
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from psycopg.conninfo import conninfo_to_dict

from chat.logging import event

DURABLE = ("alembic_version", "users", "user_keys", "invitations", "conversations", "conversation_members")
MAGIC = b"CHATBACKUP1\0"
CHUNK = 1024 * 1024


def pg_environment(dsn: str) -> dict[str, str]:
    # No URL/password en argv; libpq admite también Unix sockets/sslmode.
    values = conninfo_to_dict(dsn)
    mapping = {"dbname": "PGDATABASE", "user": "PGUSER", "password": "PGPASSWORD", "host": "PGHOST",
               "port": "PGPORT", "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT"}
    if values.keys() - mapping.keys():
        raise ValueError("Parámetro libpq no admitido para backup")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update({mapping[k]: str(v) for k, v in values.items()})
    env["PGCONNECT_TIMEOUT"] = "5"
    return env


def dump_chunks(dsn: str, pg_bin: Path) -> Iterator[bytes]:
    with psycopg.connect(dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = db.execute("SELECT pg_export_snapshot()").fetchone()
        assert row
        common = [str(pg_bin / "pg_dump"), "--no-owner", "--no-privileges", "--no-large-objects",
                  "--schema=public", "--extension=citext", "--extension=pgcrypto", "--strict-names", "--snapshot=" + row[0], "--lock-wait-timeout=10s"]
        phases = [["--section=pre-data"], ["--data-only", *["--table=public." + t for t in DURABLE]],
                  ["--section=post-data"]]
        for phase in phases:
            with subprocess.Popen([*common, *phase], env=pg_environment(dsn), stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL) as process:
                assert process.stdout
                try:
                    while chunk := process.stdout.read(CHUNK):
                        yield chunk
                except BaseException:
                    process.terminate()
                    raise
                if process.wait() != 0:
                    raise RuntimeError("pg_dump falló; copia descartada")


def encrypt(chunks: Iterator[bytes], target: BinaryIO, key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("La clave requiere 32 bytes")
    nonce = os.urandom(12)
    header = MAGIC + nonce
    crypt = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    crypt.authenticate_additional_data(header)
    target.write(header)
    for chunk in chunks:
        target.write(crypt.update(chunk))
    target.write(crypt.finalize())
    target.write(crypt.tag)


def decrypt(source: BinaryIO, target: BinaryIO, key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("La clave requiere 32 bytes")
    header = source.read(len(MAGIC) + 12)
    if not header.startswith(MAGIC) or len(header) != len(MAGIC) + 12:
        raise ValueError("Formato de backup inválido")
    source.seek(-16, os.SEEK_END)
    end = source.tell()
    tag = source.read(16)
    source.seek(len(header))
    crypt = Cipher(algorithms.AES(key), modes.GCM(header[-12:], tag)).decryptor()
    crypt.authenticate_additional_data(header)
    while source.tell() < end:
        target.write(crypt.update(source.read(min(CHUNK, end - source.tell()))))
    target.write(crypt.finalize())  # No ejecutar SQL antes de autenticar TODO.


def retain(directory: Path, now: datetime) -> None:
    copies: list[tuple[datetime, Path]] = []
    for path in directory.glob("*.chatbak"):
        if re.fullmatch(r"\d{8}T\d{12}Z.chatbak", path.name):
            copies.append((datetime.strptime(path.stem, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC), path))
    weeks: dict[tuple[int, int], Path] = {}
    keep: set[Path] = set()
    for stamp, path in sorted(copies, reverse=True):
        week = stamp.isocalendar()[:2]
        weeks.setdefault(week, path)
        if stamp >= now - timedelta(days=7):
            keep.add(path)
    keep.update(list(weeks.values())[:4])
    for _, path in copies:
        if path not in keep:
            path.unlink()


def backup(dsn: str, directory: Path, key: bytes, pg_bin: Path) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".chatbak"
    temporary = directory / (name + ".partial")
    try:
        with temporary.open("xb") as output:
            os.chmod(temporary, 0o600)
            encrypt(dump_chunks(dsn, pg_bin), output, key)
            output.flush()
            os.fsync(output.fileno())
        result = directory / name
        temporary.rename(result)
        # fsync del directorio: un éxito debe sobrevivir a un reinicio abrupto.
        descriptor = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        retain(directory, datetime.now(UTC))
        return result
    finally:
        temporary.unlink(missing_ok=True)


def restore(dsn: str, source: Path, key: bytes, pg_bin: Path, scratch: Path) -> None:
    # El operador debe proporcionar tmpfs privado; nunca restaurar encima de datos.
    with psycopg.connect(dsn) as db:
        if db.execute("SELECT 1 FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone():
            raise ValueError("La restauración requiere una base vacía")
    with tempfile.TemporaryFile(dir=scratch) as clear, source.open("rb") as encrypted:
        decrypt(encrypted, clear, key)
        clear.seek(0)
        subprocess.run([str(pg_bin / "psql"), "-X", "--single-transaction", "--set=ON_ERROR_STOP=1",
                        "--command=DROP SCHEMA public", "--file=-"],
                       env=pg_environment(dsn), stdin=clear, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=7200)
    with psycopg.connect(dsn) as db:
        tables = db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
        for (table,) in tables:
            if table not in DURABLE:
                from psycopg import sql
                if db.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
                    raise RuntimeError("Restauración contiene datos temporales")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path, default=Path("/usr/bin"))
    parser.add_argument("--directory", type=Path, default=Path("/backups"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--scratch", type=Path, default=Path("/dev/shm"))
    args = parser.parse_args()
    started = time.monotonic()
    try:
        key = args.key_file.read_bytes()
        if len(key) != 32:
            raise ValueError("La clave requiere 32 bytes")
        dsn = args.database_url_file.read_text().strip()
        if args.action == "backup":
            backup(dsn, args.directory, key, args.pg_bin)
        else:
            if args.source is None:
                raise ValueError("Falta --source")
            restore(dsn, args.source, key, args.pg_bin, args.scratch)
        event(args.action + "_complete", service="operations", status=round(time.monotonic() - started, 2))
        return 0
    except Exception:
        event(args.action + "_failed", service="operations", level="ERROR", error_code="BACKUP_OPERATION_FAILED")
        return 1


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        raise SystemExit(main())
