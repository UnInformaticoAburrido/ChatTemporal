"""Puertas §29.4: líneas globales >=85% y cada dominio crítico >=90%."""

import argparse
import json
from pathlib import Path

GROUPS = {
    "auth": ["auth_dependency", "identity", "identity_api", "identity_crypto",
             "identity_dto", "identity_redis", "identity_store"],
    "invitations": ["invitation_codes", "conversations", "conversation_api",
                    "conversation_dto", "conversation_store"],
    "delivery": ["messaging", "delivery_store", "realtime_redis", "reconciliation",
                 "websocket_api", "ws_protocol", "persistence"],
    "voting": ["voting", "vote_api", "vote_store"],
}


def check(report: dict) -> bool:
    files = report["files"]
    # Falla también si se mide otra raíz, faltan módulos o no hay líneas medidas.
    expected = {str(path) for folder in ("chat", "chat_client") for path in Path(folder).rglob("*.py")}
    if not expected or expected != set(files):
        raise ValueError("La cobertura debe incluir todos los módulos de chat y chat_client")
    groups = {"global": (list(files), 85)}
    groups.update({name: ([f"chat/{module}.py" for module in modules], 90)
                   for name, modules in GROUPS.items()})
    passed = True
    for name, (paths, minimum) in groups.items():
        covered = sum(files[path]["summary"]["covered_lines"] for path in paths)
        total = sum(files[path]["summary"]["num_statements"] for path in paths)
        ok = total > 0 and covered * 100 >= minimum * total
        percent = covered * 100 / total if total else 0
        print(f"{name}: {percent:.2f}% ({covered}/{total}), mínimo {minimum}%: {'OK' if ok else 'FALLO'}")
        passed = passed and ok
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if check(json.loads(args.report.read_text())) else 1)
