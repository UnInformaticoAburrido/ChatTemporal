.PHONY: init config up start stop down logs test check
LOCAL = docker compose -f docker-compose.yml -f docker-compose.local.yml

init:
	python3 scripts/init_local.py

config:
	docker compose config --quiet
	$(LOCAL) config --quiet

up:
	$(LOCAL) up --build -d --wait --wait-timeout 180

# DEC-21: reutilizar imágenes construidas y aplicar dependencias/configuración
# al arrancar, también tras un intento incompleto o un cambio de montajes.
start:
	$(LOCAL) up --no-build -d --wait --wait-timeout 180

stop:
	$(LOCAL) stop

down:
	$(LOCAL) down

logs:
	$(LOCAL) logs --tail=100 -f

test:
	cd python && python -m pytest -m 'not integration'

check:
	cd python && ruff check . && mypy chat
