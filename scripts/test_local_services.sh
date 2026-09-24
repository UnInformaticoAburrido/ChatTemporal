#!/bin/sh
set -eu
# DEC-30: alternativa de pruebas reales sin Docker. Solo sockets Unix privados;
# binarios aportados por el operador, sin instalar ni tocar servicios del host.
: "${CHAT_TEST_PG_BIN:?Indica el directorio de binarios PostgreSQL}"
: "${CHAT_TEST_REDIS_SERVER:?Indica el ejecutable redis-server}"
CHAT_TEST_PYTHON=${CHAT_TEST_PYTHON:-python3}
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
task_dir=$(mktemp -d /tmp/chat-persistence-test.XXXXXX)
chmod 700 "$task_dir"
redis_pid=
cleanup() {
    if [ -n "$redis_pid" ]; then
        kill "$redis_pid" 2>/dev/null || true
        wait "$redis_pid" 2>/dev/null || true
    fi
    "$CHAT_TEST_PG_BIN/pg_ctl" -D "$task_dir/pgdata" -m fast -w stop >/dev/null 2>&1 || true
    # Conservar evidencias locales; únicamente datos sintéticos y logs de prueba.
    echo "Evidencias de pruebas: $task_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$CHAT_TEST_PG_BIN/initdb" -D "$task_dir/pgdata" -A trust --no-locale -E UTF8 >"$task_dir/init.log"
"$CHAT_TEST_PG_BIN/pg_ctl" -D "$task_dir/pgdata" -l "$task_dir/postgres.log" \
    -o "-k $task_dir -c listen_addresses='' -c unix_socket_permissions=0700" -w start
"$CHAT_TEST_REDIS_SERVER" --port 0 --unixsocket "$task_dir/redis.sock" --unixsocketperm 700 \
    --save '' --appendonly no --maxmemory 256mb --maxmemory-policy noeviction \
    --dir "$task_dir" >"$task_dir/redis.log" 2>&1 &
redis_pid=$!
umask 077
printf 'postgresql:///postgres?host=%s\n' "$task_dir" >"$task_dir/database_url"
printf 'unix://%s/redis.sock\n' "$task_dir" >"$task_dir/redis_url"
export DATABASE_URL_FILE="$task_dir/database_url"
export REDIS_URL_FILE="$task_dir/redis_url"
export MIGRATIONS_DIR="$root/BD/postgresql/migrations"
export APP_ENV=test RUN_INTEGRATION=1
cd "$root/python"
"$CHAT_TEST_PYTHON" - <<'PY'
import time

from alembic import command
from redis import Redis, RedisError

from chat.bootstrap import migration_config
from chat.config import read_secret

with Redis.from_url(read_secret("REDIS_URL"), socket_timeout=1) as redis:
    for _ in range(100):
        try:
            redis.ping()
            break
        except (RedisError, OSError):
            time.sleep(0.1)
    else:
        raise RuntimeError("Redis temporal no disponible")
command.upgrade(migration_config(), "head")
PY
"$CHAT_TEST_PYTHON" -m pytest -q -m integration -p no:cacheprovider "$@"
