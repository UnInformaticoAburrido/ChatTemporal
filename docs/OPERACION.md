# Operación de la base

## Preparación de producción

Antes de considerar un release de producto, cerrar N0–N9. Esta entrega prepara el
despliegue de la infraestructura, no certifica el chat para usuarios reales.

1. Provisionar secretos según `secrets/README.md`; no usar .env en producción ni
   claves locales. Chat solo recibe las públicas de bootstrap de la aplicación
   principal. `DATABASE_URL` debe usar `postgresql://`, usuario/db `chat` y el
   mismo password que `postgres_password`.
2. Configurar `python/config/settings.toml`: kid activo, pública VAPID, contacto,
   EMAIL_FROM y orígenes HTTPS explícitos. Configurar SMTP_URL en archivo secreto.
3. Cambiar dominio y email en `caddy/Caddyfile`; configurar DNS hacia el host.
   Las cadenas example.invalid se dejan a propósito para no solicitar certificados
   ni enviar correo a terceros durante preparación.
4. Verificar acceso Docker, permisos de secretos y UID1000 de Python/Redis/Caddy.
   El init de Caddy prepara únicamente sus volúmenes. PostgreSQL usa su entrypoint
   oficial para inicializar datos y permisos.
5. Revisar firewall, NTP y recursos del host. Redis es RAM; evitar swap/core dumps
   que vuelquen su memoria en disco. Habilitar observabilidad para release.
6. `docker compose config --quiet`; después, solo cuando el release esté validado,
   `docker compose --profile observability up --build -d --wait --wait-timeout 180`.

## Actualizar código y esquema

La continuación N2 requiere reconstruir Python por las dependencias nuevas y
aplicar **0003_email_hash** antes de arrancar la API. No basta con `make start`.
La migración convierte hashes hexadecimales de verificación a BYTEA de 32 bytes;
si encuentra valores antiguos inválidos se detiene, sin borrar las filas. Revisar
la procedencia de esos registros antes de corregirlos; no hacer downgrade ni
vaciar la tabla para forzar el arranque. Los tests prueban upgrade desde 0001 y 0002.

Los fuentes están montados desde el host, por exigencia del proyecto. Un cambio
en esos archivos puede afectar al contenedor: usar checkout/directorio de release
controlado, sin editar producción mientras atiende tráfico. No son imágenes
autocontenidas del código; el rollback incluye fuentes, configuración e imagen.

En este despliegue inicial se admite una ventana breve de mantenimiento:

```bash
docker compose stop caddy python worker
docker compose build python caddy
docker compose run --rm migrate
# Continuar solamente si el comando anterior terminó correctamente.
docker compose --profile observability up -d --wait --wait-timeout 180
```

No envolver los comandos con un shell que ignore errores. Para automatizar, usar
`set -eu` y comprobar el exit code de migración. Aunque exista un contenedor
`migrate` anterior completado, el release ejecuta explícitamente la migración
actual. El chequeo de esquema de la aplicación evita arrancar una versión nueva
contra un esquema antiguo.

Rollback: volver al checkout/imagen anterior compatible. No ejecutar downgrade
automático: las migraciones lo rechazan. Los cambios destructivos futuros se
dividen en dos releases (§30.2).

## Configurar identidad y correo (N2)

- `SMTP_URL_FILE` apunta a una URL `smtp://` (STARTTLS obligatorio, puerto 587 por
  defecto) o `smtps://` (TLS implícito, 465). Validación de certificado activa;
  no hay opción de producción para SMTP sin TLS. Usuario/password se leen del
  archivo secreto. Ajustar también `email_from` en la configuración pública.
- El proveedor local de ejemplo `pendiente.invalid` no envía: registro/reenvío/
  cambio de email devuelven 503 y revierten la operación. Las pruebas capturan
  mensajes; no se generan correos externos automáticamente durante el desarrollo.
- El correo contiene un código para `POST /api/v1/users/verify-email`; no hay aún
  frontend de confirmación. Caduca en 30 minutos y el reenvío invalida el anterior.
- Caddy sobrescribe `X-Chat-Client-IP`. `trusted_proxy_host` identifica el servicio
  Docker autorizado (por defecto `caddy`); no cambiar a un host controlado por
  clientes. Acceso directo sin proxy: valor vacío. No habilitar indiscriminadamente
  proxy_headers en Uvicorn ni confiar en X-Forwarded-For de Internet.
- Las cuotas de `RateLimits` tienen los defaults de §27.3. Se pueden configurar
  tablas TOML como `[rate_limits.register_ip]` con `limit = 10` y `seconds = 3600`;
  las cuotas con ráfaga incluyen `burst`. No publicar esta configuración como
  sustituto de pruebas de carga/capacidad.
- Logout/recovery/reuse confirman la revocación en PostgreSQL y publican el sid en
  `auth:session_revoked`. Si Redis falla después, la respuesta es 503 pero la sesión
  sigue revocada. N5 deberá cerrar sus sockets y revalidar sid ante reconexiones;
  el Pub/Sub actual no es una cola durable ni acredita ese cierre todavía.

## Diagnóstico

```bash
docker compose ps -a
docker compose logs --tail=100 migrate python worker
docker compose exec python python -m chat.healthcheck api
docker compose exec worker python -m chat.healthcheck worker
docker compose exec redis sh /etc/redis/healthcheck.sh
docker inspect chat-caddy-1 --format '{{json .State.Health}}'
docker compose logs --tail=60 caddy
```

Para un despliegue local, añadir `-f docker-compose.yml -f docker-compose.local.yml`
a los comandos Compose (o usar `make logs`). Caddy comprueba su API administrativa
en loopback con curl, sin proxies, con timeout de 2 s y 15 s de margen inicial.
Si aparece `unhealthy`, `State.Health.Log` muestra el error de conexión o HTTP;
los logs de Caddy permiten distinguirlo de un fallo de arranque/configuración.

`/health/live` es público y no consulta dependencias. Readiness consulta PostgreSQL
y Redis con timeout corto. La purga deja un heartbeat Redis; worker es unhealthy
si no hay purga reciente. Caddy comprueba disponibilidad cada5s; la retirada no es
instantánea y una petición durante esa ventana puede recibir503.

Un fallo de configuración emite `CONFIG_OR_DEPENDENCY` sin detalles sensibles.
Revisar existencia/permisos/formatos de archivos sin imprimirlos. No ejecutar
comandos que copien tokens/URLs secretas a logs o historial de terminal.

## Mantenimiento y observabilidad

Worker borra contenido expirado, metadatos, verificaciones y familias de refresh
fuera de auditoría. **Cierre de votos y reconciliación de deliveries no están
implementados todavía**, porque dependen del dominio N5/N6.

`docker compose --profile observability up -d` activa Prometheus interno. Métricas
actuales: readiness y edad de la última purga. Alertas iniciales: indisponibilidad
y purga detenida. No se han configurado notificaciones ni métricas del host.

La rotación `local` de Docker limita tamaño, no tiempo. Retención14d y alertas
completas deben implementarse y probarse en N8. No exponer Prometheus al público.

## Backups y restauración: puerta pendiente de N8

No hay aún servicio de copias programadas. No habilitar producción de usuarios
hasta contar con backup lógico cifrado y restauración ensayada. La política es:

- Cada6h, snapshots frecuentes7d y4 snapshots semanales.
- Incluir esquema/migraciones y datos de users, user_keys públicas, invitations,
  conversations, conversation_members; Push solo con cifrado.
- Excluir datos de messages, message_events, message_deliveries, auth_sessions,
  auth_refresh_tokens, email_verification_tokens, votes, vote_ballots. Un `pg_dump`
  completo sin exclusiones o snapshot de volumen contradiría la retención.
- Prueba mensual de restore aislado, RPO≤6h/RTO≤2h. Verificar tablas excluidas
  vacías, metadatos durables presentes y reautenticación requerida.

Nunca usar `docker compose down -v` para parar producción: elimina volúmenes.
`docker compose down` conserva PostgreSQL y estado de certificados; Redis se
pierde de forma intencionada, incluso ante reinicios.

## Referencias técnicas consultadas

- [Docker: orden, healthchecks y dependencias](https://docs.docker.com/compose/how-tos/startup-order/).
- [Docker: servicios y limitaciones de permisos de secrets de archivo](https://docs.docker.com/reference/compose-file/services/).
- [Caddy: comprobaciones activas de upstream](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
- [Caddy: límite de body](https://caddyserver.com/docs/caddyfile/directives/request_body).

La especificación del proyecto sigue siendo la autoridad funcional. Estas fuentes
solo sustentan la mecánica de las herramientas empleadas.
