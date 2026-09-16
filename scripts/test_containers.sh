#!/bin/sh
set -eu
# DEC-20: proyecto y volúmenes separados; nunca usar `down -v` sobre producción.
# Generar antes los secretos locales. No se publican puertos en esta prueba.
docker compose -p chat-tests -f docker-compose.yml -f docker-compose.local.yml build python
docker compose -p chat-tests -f docker-compose.yml -f docker-compose.local.yml up -d --wait --wait-timeout 180 python worker
docker compose -p chat-tests -f docker-compose.yml -f docker-compose.local.yml exec -T python python -m chat.healthcheck api
# Las pruebas están en el host y se ejecutan en un contenedor de test sin Docker socket.
docker build --target test -t chat-python-tests:0.1.0 python
docker compose -p chat-tests -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.test.yml run --rm tests
docker compose -p chat-tests -f docker-compose.yml -f docker-compose.local.yml down
