"""Validar logs de dependencias sin volver a imprimir su contenido."""

import argparse
import json
from datetime import datetime
from pathlib import Path


def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Campos duplicados en el log")
    return result


def validate(lines: list[str]) -> dict[str, int]:
    counts = {"postgresql": 0, "redis": 0}
    for line in lines:
        record = json.loads(line, object_pairs_hook=unique_fields)
        if not isinstance(record, dict) or set(record) != {"timestamp", "level", "service", "event_type"}:
            raise ValueError("Campos de log no permitidos")
        if record["service"] not in counts or record["level"] != "INFO" or record["event_type"] != "dependency_log":
            raise ValueError("Evento de log no permitido")
        stamp = datetime.strptime(record["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        if stamp.strftime("%Y-%m-%dT%H:%M:%SZ") != record["timestamp"]:
            raise ValueError("Timestamp no canónico")
        counts[record["service"]] += 1
    if not all(counts.values()):
        raise ValueError("Faltan logs de una dependencia")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    # Evitar evidencia antigua de éxito cuando una nueva ejecución falla.
    args.report.unlink(missing_ok=True)
    try:
        counts = validate(args.source.read_text().splitlines())
    except Exception as error:
        print(json.dumps({"dependency_logs": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    args.report.write_text(json.dumps({"passed": True, "events": counts}) + "\n")
    print("Logs de PostgreSQL/Redis: solo eventos JSON con campos permitidos.")
