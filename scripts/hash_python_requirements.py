"""Añade hashes SHA-256 publicados por PyPI sin cambiar las versiones fijadas.

Uso: python scripts/hash_python_requirements.py
Incluye las distribuciones de todas las plataformas para conservar la portabilidad.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "python" / name for name in (
    "requirements.lock", "requirements-client.lock", "requirements-dev.lock",
)]


def hashes(requirement: str) -> tuple[str, list[str]]:
    name, version = requirement.split("==")
    with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
        metadata = json.load(response)
    values = sorted({item["digests"]["sha256"] for item in metadata["urls"] if not item["yanked"]})
    if not values or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in values):
        raise ValueError(f"No hay hashes válidos para {requirement}")
    return requirement, values


def main() -> None:
    sources = {path: [line.rstrip(" \\") for line in path.read_text().splitlines()
                      if not line.lstrip().startswith("--hash=")] for path in FILES}
    requirements = sorted({line for lines in sources.values() for line in lines
                           if re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", line)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved = dict(pool.map(hashes, requirements))
    for path, lines in sources.items():
        output = []
        for line in lines:
            if line in resolved:
                output.append(line + " \\")
                values = resolved[line]
                output.extend("    --hash=sha256:" + value + (" \\" if index < len(values) - 1 else "")
                              for index, value in enumerate(values))
            else:
                output.append(line)
        path.write_text("\n".join(output) + "\n")
    print(f"Hashes actualizados para {len(requirements)} versiones fijadas.")


if __name__ == "__main__":
    main()
