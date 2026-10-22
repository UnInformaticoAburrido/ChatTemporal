"""Herramientas CI Linux amd64 fijadas por versión y SHA-256 oficial."""

import argparse
import hashlib
import io
import platform
import tarfile
import urllib.request
from pathlib import Path

TOOLS = {
    "trivy": (
        "https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_Linux-64bit.tar.gz",
        "2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a",
        "trivy",
    ),
    "actionlint": (
        "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz",
        "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
        "actionlint",
    ),
    "alertmanager": (
        "https://github.com/prometheus/alertmanager/releases/download/v0.34.1/alertmanager-0.34.1.linux-amd64.tar.gz",
        "265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8",
        "alertmanager-0.34.1.linux-amd64/alertmanager",
    ),
    "amtool": (
        "https://github.com/prometheus/alertmanager/releases/download/v0.34.1/alertmanager-0.34.1.linux-amd64.tar.gz",
        "265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8",
        "alertmanager-0.34.1.linux-amd64/amtool",
    ),
}


def install(name: str, directory: Path) -> None:
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("Este instalador requiere Linux amd64")
    url, expected, member_name = TOOLS[name]
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f"Checksum incorrecto: {name}")
    # Extraer solo el ejecutable conocido, sin rutas ni enlaces del archivo.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.getmember(member_name)
        if not member.isfile():
            raise RuntimeError("El ejecutable no es un archivo regular")
        stream = archive.extractfile(member)
        assert stream
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / (name + ".partial")
        temporary.write_bytes(stream.read())
        temporary.chmod(0o755)
        temporary.replace(directory / name)
    print(f"{name}: SHA-256 verificado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tools", nargs="+", choices=TOOLS)
    parser.add_argument("--directory", type=Path, default=Path("artifacts/n9/tools"))
    args = parser.parse_args()
    for tool in args.tools:
        install(tool, args.directory)
