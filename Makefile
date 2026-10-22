.PHONY: init config up start stop down logs reload-web test test-web-e2e check coverage-check
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

# Aplicar rutas/cabeceras nuevas de los archivos montados sin reconstruir imágenes.
reload-web:
	$(LOCAL) exec -T caddy caddy reload --config /etc/caddy/Caddyfile.local --adapter caddyfile

test:
	cd python && python -m pytest -m 'not integration'

# Ejecutar con el Python que tiene requirements-dev.lock instalado.
test-web-e2e:
	python scripts/test_web_containers.py

check:
	cd python && ruff check --config pyproject.toml . ../scripts ../operations ../web/tests/interop.py && mypy chat chat_client

# Requiere haber combinado cobertura unitaria e integración; falla si faltan módulos.
coverage-check:
	cd python && python -m coverage json -o ../artifacts/n9/coverage.json
	cd python && python ../scripts/check_coverage.py ../artifacts/n9/coverage.json
