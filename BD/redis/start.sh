#!/bin/sh
set -eu
# DEC-07: el password no se pasa por argv ni se escribe en almacenamiento durable.
umask 077
password=$(cat /run/secrets/redis_password)
case "$password" in ''|*[!a-zA-Z0-9_-]*) exit 1 ;; esac
test "${#password}" -ge 32
cat /etc/redis/redis.conf > /tmp/redis.conf
printf '\nrequirepass %s\n' "$password" >> /tmp/redis.conf
unset password
exec redis-server /tmp/redis.conf
