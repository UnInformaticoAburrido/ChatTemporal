Los secretos reales están excluidos de Git y nunca deben copiarse a una imagen.

Para desarrollo, `python3 scripts/init_local.py` genera claves y contraseñas locales.
No sobrescribe ningún archivo existente. No crea credenciales válidas para producción.
Los archivos se crean con modo 0600 y los directorios con 0700. Compose local con
secrets de archivo conserva permisos del host: Python se ejecuta con UID 1000.
En otro host, adaptar UID/GID y permisos antes de iniciar; `mode:` de Compose no
cambia permisos de secretos respaldados por bind mounts.

Producción debe provisionar: `postgres_password`, `database_url`, `redis_password`,
`redis_url`, `chat_jwt_private.pem`, `invitation_hmac` (32 bytes binarios o más),
`vapid_private.pem` (P-256 PEM), `smtp_url`, `chat-public/<kid>.pem` y
`bootstrap-public/<kid>.pem` (públicas Ed25519 de la aplicación principal).
El password de Redis usa al menos 32 caracteres Base64URL sin padding.
Actualizar también TOML y Caddy con dominio, orígenes, kid y pública VAPID reales.

El secreto bootstrap privado local, si se genera, queda solo en el host como
`bootstrap_local_private.pem`; nunca se monta en Chat.
