#!/bin/sh
set -eu
# DEC-20: proyecto y volúmenes separados; nunca usar `down -v` sobre producción.
# Generar antes los secretos locales. No se publican puertos en esta prueba.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
compose() {
    docker compose -p chat-tests -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.test.yml "$@"
}
cleanup() {
    # También ante fallos; conservar volúmenes del proyecto de pruebas.
    compose down --remove-orphans
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose build python tests caddy
compose up -d --wait --wait-timeout 180 postgresql redis
compose run --rm --no-deps migrate
# No ejecutar el worker durante fixtures que migran o fuerzan vencimientos.
if [ "${CHAT_TEST_COVERAGE:-0}" = 1 ]; then
    compose run --rm --no-deps tests python -m coverage run \
        --data-file=/evidence/.coverage.integration -m pytest -q -m integration -p no:cacheprovider
else
    compose run --rm --no-deps tests
fi
compose up -d --wait --wait-timeout 180 python worker
compose exec -T python python -m chat.healthcheck api
compose exec -T worker python -m chat.healthcheck worker
compose up -d --wait --wait-timeout 180 caddy
compose cp caddy:/data/caddy/pki/authorities/local/root.crt artifacts/n9/test-root.crt
chmod 644 artifacts/n9/test-root.crt
compose run --rm --no-deps tests python /workspace/scripts/test_proxy.py
