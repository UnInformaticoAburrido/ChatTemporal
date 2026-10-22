#!/bin/sh
# Dependencias: no conservar mensajes arbitrarios que puedan repetir datos SQL.
# La severidad detallada se diagnostica con métricas; nunca imprimir la línea recibida.
set -eu
service=$1
shift
case "$service" in postgresql|redis) ;; *) exit 2 ;; esac
fifo=/tmp/chat-service-log.$$
mkfifo -m 600 "$fifo"
child=
logger=
cleanup() { rm -f "$fifo"; }
trap cleanup EXIT
forward() { [ -z "$child" ] || kill -"$1" "$child" 2>/dev/null || true; }
trap 'forward TERM' TERM
trap 'forward INT' INT
(
    while IFS= read -r line || [ -n "$line" ]; do
        printf '{"timestamp":"%s","level":"INFO","service":"%s","event_type":"dependency_log"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$service"
    done < "$fifo"
) &
logger=$!
"$@" > "$fifo" 2>&1 &
child=$!
status=0
wait "$child" || status=$?
# wait interrumpido por una señal: esperar que el hijo termine realmente.
if kill -0 "$child" 2>/dev/null; then
    status=0
    wait "$child" || status=$?
fi
wait "$logger" || true
exit "$status"
