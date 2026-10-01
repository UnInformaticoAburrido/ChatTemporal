#!/usr/bin/env python3
"""Timer host: backup/ensayo en BD aislada, sin publicar PostgreSQL."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from chat.backup import DURABLE, backup, restore  # noqa: E402
from chat.logging import event  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "drill"])
    args = parser.parse_args()
    destination = Path(os.environ.get("CHAT_BACKUP_DIR", str(ROOT / "backups")))
    metrics = ROOT / "artifacts/operations"
    metrics.mkdir(parents=True, exist_ok=True)
    os.chmod(metrics, 0o755)
    with (metrics / "backup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        container = subprocess.check_output(["docker", "compose", "ps", "-q", "postgresql"], cwd=ROOT, text=True).strip()
        networks = json.loads(subprocess.check_output(["docker", "inspect", "--format",
                              "{{json .NetworkSettings.Networks}}", container], text=True))
        addresses = [v["IPAddress"] for k, v in networks.items() if k.endswith("_backend")]
        if len(addresses) != 1:
            raise RuntimeError("Red PostgreSQL ambigua")
        config = conninfo_to_dict((ROOT / "secrets/database_url").read_text().strip())
        config.update(host=addresses[0], port="5432")
        dsn = make_conninfo(**config)
        key = Path(os.environ["CHAT_BACKUP_KEY_FILE"]).read_bytes()
        if len(key) != 32:
            raise ValueError("Clave de backup inválida")
        pg_bin = Path(os.environ.get("CHAT_PG_BIN", "/usr/lib/postgresql/17/bin"))
        if args.action == "backup":
            source = backup(dsn, destination, key, pg_bin)
        else:
            source = max(destination.glob("*.chatbak"))
            stamp = datetime.strptime(source.stem, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
            age = time.time() - stamp.timestamp()
            if not 0 <= age <= 21600:
                raise RuntimeError("RPO excede seis horas")
            name = "chat_restore_" + uuid4().hex
            with psycopg.connect(dsn, autocommit=True) as admin:
                admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
                try:
                    target = make_conninfo(dsn, dbname=name)
                    restore(target, source, key, pg_bin, Path(os.environ.get("CHAT_RESTORE_TMP", "/run/chat-backup")))
                    with psycopg.connect(target) as db:
                        # Todas las tablas durables y versión del esquema deben existir.
                        for table in DURABLE:
                            db.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()
                        if not db.execute("SELECT version_num FROM alembic_version").fetchone():
                            raise RuntimeError("Backup sin versión Alembic")
                    if time.monotonic() - started > 7200:
                        raise RuntimeError("RTO excede dos horas")
                finally:
                    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        metric = "chat_backup" if args.action == "backup" else "chat_restore_drill"
        temporary = metrics / (metric + ".tmp")
        successful_at = (datetime.strptime(source.stem, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC).timestamp()
                         if args.action == "backup" else time.time())
        temporary.write_text(f"{metric}_last_success_seconds {successful_at}\n"
                             f"{metric}_duration_seconds {time.monotonic() - started}\n")
        os.chmod(temporary, 0o644)  # Solo métricas numéricas, legibles por node-exporter.
        temporary.replace(metrics / (metric + ".prom"))
        event(args.action + "_complete", service="operations", status="ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        event("backup_job_failed", service="operations", level="ERROR", error_code="BACKUP_OPERATION_FAILED")
        raise SystemExit(1) from None
