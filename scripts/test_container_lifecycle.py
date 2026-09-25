"""Persistencia, volatilidad y recuperación en el proyecto aislado chat-tests."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
from redis import Redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from chat.config import read_secret  # noqa: E402

STATE = Path("/evidence/lifecycle-state.json")
REPORT = Path("/evidence/lifecycle.json")


def run(phase: str) -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("RUN_INTEGRATION") != "1":
        raise RuntimeError("Requiere el runner aislado de integración")
    if phase == "unavailable":
        with httpx.Client(base_url="http://python:8000", trust_env=False, timeout=10) as api:
            assert api.get("/health/live").status_code == 200
            assert api.get("/health/ready").status_code == 503
        print("Dependencias detenidas: liveness 200, readiness 503.")
        return
    with psycopg.connect(read_secret("DATABASE_URL"), connect_timeout=5) as db, \
            Redis.from_url(read_secret("REDIS_URL"), socket_timeout=5) as redis:
        if phase == "prepare":
            identifier = uuid4()
            db.execute("""INSERT INTO users(id,nick,email,memory_hash,email_verified)
                VALUES (%s,%s,%s,'lifecycle-test',true)""",
                (identifier, "restart_" + identifier.hex[:12], identifier.hex + "@example.com"))
            redis.set(f"test:lifecycle:{identifier}", "synthetic")
            assert redis.ttl(f"test:lifecycle:{identifier}") == -1
            STATE.write_text(json.dumps({"user": str(identifier), "prepared_at": time.time(),
                "redis_run_id": redis.info("server")["run_id"],
                "postgres_started": str(db.execute("SELECT pg_postmaster_start_time()").fetchone()[0])}))
            return
        state = json.loads(STATE.read_text())
        identifier = UUID(state["user"])
        try:
            assert db.execute("SELECT id FROM users WHERE id=%s", (identifier,)).fetchone() == (identifier,)
            assert str(db.execute("SELECT pg_postmaster_start_time()").fetchone()[0]) != state["postgres_started"]
            assert redis.info("server")["run_id"] != state["redis_run_id"]
            assert not redis.exists(f"test:lifecycle:{identifier}")
            assert redis.config_get("save")["save"] == ""
            assert redis.config_get("appendonly")["appendonly"] == "no"
            assert redis.config_get("maxmemory-policy")["maxmemory-policy"] == "noeviction"
            deadline = time.monotonic() + 90
            with httpx.Client(base_url="http://python:8000", trust_env=False, timeout=10) as api:
                while time.monotonic() < deadline:
                    stamp = redis.get("maintenance:last_success")
                    if api.get("/health/ready").status_code == 200 and stamp and float(stamp) > state["prepared_at"]:
                        break
                    time.sleep(1)
                else:
                    raise AssertionError("API/worker no se recuperaron tras reiniciar dependencias")
            REPORT.write_text(json.dumps({"passed": True, "postgres_persisted": True,
                "redis_volatile": True, "readiness_recovered": True, "worker_recovered": True}) + "\n")
            print("Reinicio real: PostgreSQL persistente, Redis volátil, API/worker recuperados.")
        finally:
            db.execute("DELETE FROM users WHERE id=%s", (identifier,))
            db.commit()
            redis.delete(f"test:lifecycle:{identifier}")
            STATE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "unavailable", "verify"))
    args = parser.parse_args()
    try:
        run(args.phase)
    except Exception as error:
        print(json.dumps({"lifecycle": "failed", "phase": args.phase, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
