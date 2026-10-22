#!/usr/bin/env bash
# Genera el paquete local ignorado; las plantillas sí viajan en Git.
set -Eeuo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
task_output="$task_root/instalador-servidor"
umask 077
[[ ! -L "$task_output" ]] || { echo 'El destino no puede ser un enlace.' >&2; exit 1; }
mkdir -p -- "$task_output"
chmod 0700 -- "$task_output"
for task_file in instalar.sh README.md; do
    [[ ! -L "$task_output/$task_file" ]] || { echo 'El destino no puede ser un enlace.' >&2; exit 1; }
    install -m 0600 -- "$task_root/scripts/servidor/$task_file" "$task_output/$task_file"
done
chmod 0700 -- "$task_output/instalar.sh"
echo "Instalador preparado en $task_output (excluido de Git)."
