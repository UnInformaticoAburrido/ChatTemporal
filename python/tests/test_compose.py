import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def config(local: bool = False, port: str | None = None) -> dict:
    args = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
    if local:
        args += ["-f", str(ROOT / "docker-compose.local.yml")]
    env = {**os.environ, "CHAT_HTTP_PORT": port or ""}
    return json.loads(subprocess.check_output([*args, "config", "--format", "json"], cwd=ROOT, env=env))


def test_dependency_gates_and_public_ports() -> None:
    services = config()["services"]
    assert services["migrate"]["depends_on"]["postgresql"]["condition"] == "service_healthy"
    for name in ("python", "worker"):
        assert services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        assert not services[name].get("ports")
    assert services["caddy"]["depends_on"]["python"]["condition"] == "service_healthy"
    assert {p["published"] for p in services["caddy"]["ports"]} == {"80", "443"}
    for name, service in services.items():
        if name != "caddy":
            assert not service.get("ports")
    assert services["postgresql"]["networks"] == {"backend": None}
    assert services["redis"]["networks"] == {"backend": None}


def test_source_bind_mounts_and_redis_volatility() -> None:
    services = config()["services"]
    # Regresión: Compose acepta YAML [ruta:opción,mode=1777], pero el daemon
    # interpreta el segundo elemento como una ruta relativa y rechaza el montaje.
    for name, service in services.items():
        for mount in service.get("tmpfs", []):
            assert mount.split(":", 1)[0].startswith("/"), (name, mount)
    assert any(v["type"] == "bind" and v["source"].endswith("/python")
               and v["target"] == "/app" and v["read_only"] for v in services["python"]["volumes"])
    assert all(v["type"] != "volume" for v in services["redis"]["volumes"])
    assert any(v.startswith("/data:") for v in services["redis"]["tmpfs"])
    redis = (ROOT / "BD/redis/redis.conf").read_text()
    assert 'save ""' in redis and "appendonly no" in redis and "maxmemory-policy noeviction" in redis


def test_local_override_only_exposes_loopback() -> None:
    services = config(local=True)["services"]
    ports = services["caddy"]["ports"]
    assert len(ports) == 1 and ports[0]["host_ip"] == "127.0.0.1" and ports[0]["published"] == "18080"
    for name in ("python", "worker", "migrate"):
        assert services[name]["environment"]["APP_ENV"] == "development"
        assert services[name]["environment"]["CHAT_LOCAL_MAILBOX"] == "1"
        assert any(v["target"] == "/etc/chat/settings.toml" and v["source"].endswith("settings.local.toml")
                   for v in services[name]["volumes"])
    custom = config(local=True, port="18081")["services"]
    assert custom["caddy"]["ports"][0]["published"] == "18081"
    assert custom["caddy"]["ports"][0]["target"] == 8080
    for name in ("python", "worker", "migrate"):
        assert custom[name]["environment"]["CHAT_LOCAL_HTTP_PORT"] == "18081"


def test_local_mailbox_is_private_volatile_and_gates_api() -> None:
    services = config(local=True)["services"]
    mailbox = services["mailpit"]
    assert "@sha256:" in mailbox["image"]
    assert mailbox["networks"] == {"backend": None, "egress": None}
    assert mailbox["read_only"] and not mailbox.get("volumes")
    assert mailbox["ports"] == [{"mode": "ingress", "host_ip": "127.0.0.1",
                                  "target": 8025, "published": "18025", "protocol": "tcp"}]
    assert mailbox["environment"]["MP_DATABASE"].startswith("/tmp/")
    assert mailbox["environment"]["MP_QUIET"] == "true"
    assert services["python"]["depends_on"]["mailpit"]["condition"] == "service_healthy"
    production = config()["services"]
    assert "mailpit" not in production
    for name in ("python", "worker", "migrate"):
        assert "CHAT_LOCAL_MAILBOX" not in production[name]["environment"]


def test_container_tests_do_not_publish_mailbox_ports() -> None:
    services = json.loads(subprocess.check_output([
        "docker", "compose", "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(ROOT / "docker-compose.local.yml"), "-f", str(ROOT / "docker-compose.test.yml"),
        "config", "--format", "json",
    ], cwd=ROOT))["services"]
    assert not services["mailpit"].get("ports")


def test_published_ports_have_a_non_internal_network() -> None:
    # Docker puede aceptar ports pero no publicarlos si todas las redes son internas.
    for local in (False, True):
        compose = config(local=local)
        for name, service in compose["services"].items():
            if service.get("ports"):
                assert any(not compose["networks"][network].get("internal", False)
                           for network in service["networks"]), name
