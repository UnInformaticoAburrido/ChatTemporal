FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648

# DEC-24: la imagen oficial aplica cap_net_bind_service=ep al binario. Al quitar
# todas las capabilities del contenedor, exec puede fallar antes del healthcheck.
# Respuesta del usuario: logs "exec /usr/bin/caddy: operation not permitted",
# State=restarting, ExitCode=255 y Health.Log vacío.
# Nuestros puertos internos 8080/8443 no necesitan esa capacidad: quitarla durante
# el build mantiene UID1000, cap_drop ALL, no-new-privileges y raíz de solo lectura.
USER root
RUN setcap -r /usr/bin/caddy \
    && test -z "$(getcap /usr/bin/caddy)" \
    && /usr/bin/caddy version
USER 1000:1000

# Se hereda CMD; Caddyfile y scripts se consumen del bind mount ./caddy.
