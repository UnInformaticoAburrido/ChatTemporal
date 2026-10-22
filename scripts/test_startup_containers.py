"""Ensayar fallo de migración y reinicio directo en un proyecto Docker desechable."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "artifacts/n9"
REPORT = EVIDENCE / "startup-gates.json"


def command(*args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
    # Las salidas pueden contener configuración: nunca imprimir stdout/stderr crudos.
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=check, timeout=timeout)


def fault_config(directory: Path) -> tuple[Path, str]:
    directory.chmod(0o755)  # UID 1000 de los contenedores necesita atravesarlo.
    migrations = directory / "migrations"
    shutil.copytree(ROOT / "BD/postgresql/migrations", migrations,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    head = ScriptDirectory(str(migrations)).get_current_head()
    assert head is not None
    (migrations / "versions/9999_failure.py").write_text(
        "from alembic import op\n"
        "revision = 'n9_expected_failure'\n"
        f"down_revision = {head!r}\n"
        "def upgrade():\n"
        "    op.execute('CREATE TABLE n9_rollback_probe (id integer)')\n"
        "    op.execute(\"SELECT 'n9-sensitive-migration-canary'::integer\")\n"
        "def downgrade():\n"
        "    raise RuntimeError('Test-only migration')\n"
    )
    config = directory / "fault.json"
    config.write_text(json.dumps({"services": {name: {
        "volumes": [{"type": "bind", "source": str(migrations), "target": "/migrations", "read_only": True}],
    } for name in ("migrate", "python", "worker")}}))
    return config, head


class Trial:
    def __init__(self) -> None:
        self.project = "chat-startup-" + uuid4().hex
        self.compose = ["docker", "compose", "-p", self.project,
                        "-f", str(ROOT / "docker-compose.yml"),
                        "-f", str(ROOT / "docker-compose.local.yml"),
                        "-f", str(ROOT / "docker-compose.test.yml")]

    def run(self, *args: str, fault: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        return command(*self.compose, *(["-f", str(fault)] if fault else []), *args, check=check)

    def identifier(self, service: str) -> str:
        identifier = self.run("ps", "--all", "--quiet", service).stdout.strip()
        if not identifier or "\n" in identifier:
            raise AssertionError("Se esperaba exactamente un contenedor del ensayo")
        return identifier

    def state(self, service: str) -> dict:
        identifier = self.identifier(service)
        # Confirmar propiedad antes de usar un ID en docker start/restart/stop.
        owner = command("docker", "inspect", "--format",
                        '{{index .Config.Labels "com.docker.compose.project"}}', identifier).stdout.strip()
        assert owner == self.project
        return json.loads(command("docker", "inspect", "--format", "{{json .State}}", identifier).stdout)

    def direct(self, action: str, *services: str) -> None:
        for service in services:
            self.state(service)
        command("docker", action, *(self.identifier(service) for service in services))

    def wait(self, services: tuple[str, ...], *, healthy: bool) -> None:
        deadline = time.monotonic() + (150 if healthy else 30)
        while time.monotonic() < deadline:
            states = [self.state(service) for service in services]
            if healthy:
                if all(state.get("Health", {}).get("Status") == "healthy" for state in states):
                    return
            elif all(state["Status"] == "exited" for state in states):
                assert all(state["ExitCode"] == 1 and not state["OOMKilled"] for state in states)
                return
            time.sleep(1)
        raise AssertionError("Los contenedores no alcanzaron el estado esperado")

    def probe(self, phase: str, head: str) -> None:
        self.run("run", "--rm", "--no-deps", "tests", "python",
                 "/workspace/scripts/test_startup_containers.py", "--probe", phase, "--head", head)


def isolated_url(value: str, service: str, scheme: str, port: int) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == scheme and parsed.hostname == service and parsed.port in (None, port)
                and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def probe(phase: str, head: str) -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("RUN_INTEGRATION") != "1":
        raise RuntimeError("Requiere el runner aislado de integración")
    if phase == "waiting":
        for _ in range(3):
            for host, port in (("python", 8000), ("worker", 8001)):
                try:
                    with socket.create_connection((host, port), timeout=2):
                        raise AssertionError("Puerto abierto antes de recuperar dependencias")
                except ConnectionRefusedError:
                    pass
            time.sleep(1)
        return
    import psycopg

    sys.path.insert(0, str(ROOT / "python"))
    from chat.config import read_secret

    if phase == "isolation":
        # Rechazar DSN externos antes de ejecutar ninguna migración, incluso
        # si el operador reutiliza por error archivos de secretos de otro entorno.
        assert isolated_url(read_secret("DATABASE_URL"), "postgresql", "postgresql", 5432)
        assert isolated_url(read_secret("REDIS_URL"), "redis", "redis", 6379)
        return
    with psycopg.connect(read_secret("DATABASE_URL"), connect_timeout=5) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
        assert db.execute("SELECT to_regclass('public.n9_rollback_probe')").fetchone() == (None,)


def run() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    REPORT.unlink(missing_ok=True)
    trial = Trial()
    with tempfile.TemporaryDirectory(prefix="startup-", dir=EVIDENCE) as folder:
        fault, head = fault_config(Path(folder))
        # Deshabilitar únicamente el auto-restart del ensayo para observar exit 1.
        config = Path(folder) / "restart.json"
        config.write_text(json.dumps({"services": {name: {"restart": "no"} for name in ("python", "worker")}}))
        trial.compose.extend(["-f", str(config)])
        try:
            services = json.loads(trial.run("config", "--format", "json").stdout)["services"]
            assert not any(service.get("ports") for service in services.values())
            print("Arranque: base aislada y migración inicial.", flush=True)
            trial.run("up", "-d", "--wait", "--wait-timeout", "150", "postgresql", "redis")
            trial.probe("isolation", head)
            trial.run("run", "--rm", "--no-deps", "migrate")

            print("Arranque: migración SQL fallida bloquea API y worker.", flush=True)
            failed = trial.run("up", "-d", "--wait", "--wait-timeout", "30", "python", "worker",
                               fault=fault, check=False)
            assert failed.returncode != 0
            trial.wait(("migrate",), healthy=False)
            for service in ("python", "worker"):
                assert trial.state(service)["Status"] == "created"
            trial.probe("rollback", head)

            print("Arranque: docker start directo rechaza el esquema pendiente.", flush=True)
            migration_finished = trial.state("migrate")["FinishedAt"]
            trial.direct("start", "python", "worker")
            trial.wait(("python", "worker"), healthy=False)
            assert trial.state("migrate")["FinishedAt"] == migration_finished
            for service in ("python", "worker", "migrate"):
                logs = trial.run("logs", "--no-log-prefix", "--no-color", service).stdout
                events = [json.loads(line) for line in logs.splitlines()]
                assert any(event.get("event_type") == "startup_failed" for event in events)
                assert "n9-sensitive-migration-canary" not in logs

            print("Arranque: recuperar release compatible y reiniciar sin Compose.", flush=True)
            trial.run("up", "-d", "--wait", "--wait-timeout", "150", "python", "worker")
            trial.direct("stop", "postgresql", "redis")
            trial.direct("restart", "python", "worker")
            started = {name: trial.state(name)["StartedAt"] for name in ("python", "worker")}
            trial.probe("waiting", head)
            assert all(trial.state(name)["Running"] for name in started)
            trial.direct("start", "postgresql", "redis")
            trial.wait(("postgresql", "redis", "python", "worker"), healthy=True)
            assert all(trial.state(name)["StartedAt"] == stamp for name, stamp in started.items())
            assert trial.state("migrate")["ExitCode"] == 0
            trial.probe("rollback", head)
        finally:
            # Solo recursos del proyecto aleatorio creado en esta invocación.
            trial.run("down", "--volumes", "--remove-orphans")
    REPORT.write_text(json.dumps({"passed": True, "migration_blocks_services": True,
        "migration_rolled_back": True, "direct_start_checks_schema": True,
        "direct_restart_waits_for_dependencies": True, "recovered_without_restart": True}) + "\n")
    print("Puertas de arranque verificadas; proyecto temporal eliminado.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", choices=("isolation", "rollback", "waiting"))
    parser.add_argument("--head", default="")
    args = parser.parse_args()
    try:
        probe(args.probe, args.head) if args.probe else run()
    except Exception as error:
        print(json.dumps({"startup_gates": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
