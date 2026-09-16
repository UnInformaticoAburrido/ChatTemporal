"""§30.3: campos permitidos, sin URL completa, cuerpo ni repr de excepciones."""

import json
from datetime import UTC, datetime


def event(name: str, *, service: str = "python", level: str = "INFO", **fields: object) -> None:
    permitted = {"request_id", "endpoint", "status", "error_code"}
    record = {"timestamp": datetime.now(UTC).isoformat(), "level": level,
              "service": service, "event_type": name}
    record.update({key: str(value) for key, value in fields.items() if key in permitted})
    print(json.dumps(record), flush=True)
