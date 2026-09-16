#!/bin/sh
set -eu
# DEC-23: la imagen oficial fijada incluye curl. Consultar únicamente el admin
# loopback; ignorar proxies y .curlrc para comprobar el proceso de este contenedor.
# Los errores quedan en State.Health.Log; nunca se imprime el JSON de configuración.
exec curl -q --noproxy '*' --fail --silent --show-error \
    --connect-timeout 1 --max-time 2 --output /dev/null \
    http://127.0.0.1:2019/config/
