# Operación de N8

Implementación comprobada localmente; **no es una homologación del host de
producción**. No ejecutar los cambios de sistema en un equipo de desarrollo.
Las rutas de las unidades son `/opt/chat`: adaptarlas juntas si el checkout de
release vive en otro directorio. La migración sigue siendo `0007_recovery_push`.

## 1. Preparar observabilidad y secretos

Configurar el dominio en Caddy y en `prometheus/prometheus.yml`, el servidor,
usuario, remitente y destinatario en `operations/alertmanager.yml`. Crear
`secrets/alertmanager_smtp_password` fuera de Git, legible por el UID 65534 de
Alertmanager, sin conceder lectura a otros usuarios. Por ejemplo, propietario
root, grupo 65534 y modo 0640; verificar el UID de la imagen antes de desplegar.
El archivo contiene solo la contraseña, nunca la URL. SMTP exige TLS y valida
certificados. No usar `.invalid` ni credenciales de ejemplo en un release.

Prometheus, Alertmanager, node-exporter, blackbox-exporter y worker:8001 no
publican puertos. El perfil `observability` es obligatorio en producción. Acceder
a ellos desde la red interna con herramientas administrativas, sin añadir
mapeos públicos. Node-exporter monta `/` en lectura para medir el host, sin
socket Docker ni capacidades de escritura. Limitar su acceso a administradores.

Las reglas están en `prometheus/alerts.yml`. Refresh reuse alerta desde tres
casos/5 min y Push desde cinco fallos/5 min: son umbrales iniciales ajustables
tras la carga N9. REST excluye sondeos de salud del denominador. PostgreSQL usa
una conexión por transacción; se mide su ocupación real respecto a max_connections,
**no se afirma que haya un pool**. Los contadores WS miden frames, incluidos
reintentos; no equivalen a mensajes únicos. Los timeouts cuentan reconciliaciones.

Antes del release, inyectar una alerta sintética en Alertmanager por la red
interna, comprobar recepción y resolución en el buzón operativo y registrar
hora/acuse. Este envío externo corresponde al operador autorizado. Una alerta
sobre el propio canal no sustituye un monitor independiente del host/Prometheus.
La prueba automatizada `scripts/test_alert_delivery.py RUTA/alertmanager` usa
exclusivamente un receptor HTTP loopback y no certifica entrega por SMTP real.

## 2. Backups cada seis horas

Preparar usuario administrativo `chat-ops`, Python con las dependencias de
`python/requirements.lock` en `/opt/chat/.venv`, cliente PostgreSQL 17 compatible
con el servidor y permisos de lectura sobre `secrets/database_url`. El acceso al
socket Docker del operador equivale a administración del host: no se monta en
ningún contenedor de la aplicación. `backup_job.py` descubre la IP privada del
PostgreSQL del proyecto Compose y conecta desde el host; no publica 5432.

Crear `/opt/chat/backups` con propietario chat-ops/modo 0700 y
`/opt/chat/artifacts/operations` con propietario chat-ops/modo 0755. Los `.prom`
solo contienen tiempos/duraciones numéricos y se publican con modo 0644. Configurar
el destino sobre almacenamiento independiente cuando se requiera sobrevivir a
la pérdida del disco/host. Copiar un volumen PostgreSQL o Redis no está permitido.

Provisionar una clave aleatoria de **32 bytes binarios** fuera del checkout,
modo 0600, accesible por chat-ops; custodiar una copia independiente junto al
procedimiento de recuperación. No reutilizar claves de JWT, HMAC, VAPID o E2EE.
Guardar `/etc/chat/backup.env` sin valores secretos, por ejemplo:

```ini
CHAT_BACKUP_KEY_FILE=/etc/chat/backup.key
CHAT_BACKUP_DIR=/opt/chat/backups
CHAT_PG_BIN=/usr/lib/postgresql/17/bin
CHAT_RESTORE_TMP=/run/chat-backup
```

Si cambia el destino, adaptar también `ReadWritePaths` en ambas unidades. `/run`
debe ser tmpfs sin swap/core dumps que conserven el volcado descifrado. Dimensionar
el tmpfs para los metadatos durables; el backup normal nunca crea SQL en disco.
Instalar las cuatro unidades `chat-backup.*` y `chat-restore-drill.*` desde
`operations/systemd/` en `/etc/systemd/system/`, revisar propietarios y después:

```sh
sudo systemctl daemon-reload
sudo systemctl start chat-backup.service
sudo systemctl start chat-restore-drill.service
sudo systemctl enable --now chat-backup.timer chat-restore-drill.timer
systemctl list-timers chat-backup.timer chat-restore-drill.timer
```

Backup a las 00/06/12/18 UTC. Timer persistente recupera una ejecución perdida;
no recrea snapshots de horas en las que el host estuvo apagado. Los fallos no
actualizan la métrica de éxito ni eliminan copias anteriores. Se conserva cada
copia de los últimos siete días y la más reciente de cada una de las cuatro
semanas ISO más recientes con copias (incluida la actual). No se rellena una
semana ausente. Las claves antiguas deben mantenerse mientras existan copias
cifradas con ellas; rotar en un directorio separado y documentar la correspondencia.

`CHATBACKUP1` contiene cabecera autenticada, nonce aleatorio de 96 bits,
AES-256-GCM y tag de 128 bits. El volcado usa un snapshot PostgreSQL compartido
por pre-data, datos y post-data; se incluyen explícitamente citext/pgcrypto.
La lista de datos es: alembic_version, users, user_keys, invitations,
conversations y conversation_members. Todo dato de otra tabla queda fuera,
incluso si se añade una tabla nueva. El esquema public se conserva completo.
Las suscripciones Push también se excluyen por su dependencia de sesiones.
Cambios futuros de esquema/extensiones requieren repetir el ensayo.

## 3. Recuperación y ensayo mensual

El timer del día 1 a las 01:00 UTC restaura la última copia en una **base temporal
con UUID** del mismo cluster, valida la versión, las tablas durables y todas las
tablas excluidas vacías y elimina solo esa base al terminar. Nunca limpia la base
de la aplicación. Usa lock con el backup; un conflicto falla de forma explícita.
Ante un kill del proceso puede quedar una base `chat_restore_<uuid>`: comprobar
que no tenga sesiones activas y que pertenezca a ese ensayo antes de borrarla.
El ensayo usa recursos del cluster: dimensionarlo y programarlo en mantenimiento.

El éxito exige RPO ≤6 h según la fecha del snapshot y RTO ≤2 h; publica timestamp
y duración en textfile de node-exporter. Las métricas ausentes/antiguas alertan.
La prueba local solo mide datos sintéticos; repetir con volumen real y en otro
host para acreditar pérdida completa de infraestructura. No confundir un ensayo
en el mismo cluster con recuperación de un servidor destruido.

Para recuperación real, aprovisionar PostgreSQL y una base vacía, recuperar las
claves/configuración por el canal seguro y crear un archivo de URL destino. Con
el cliente compatible, desde `python/`:

```sh
../.venv/bin/python -m chat.backup restore \
  --database-url-file /ruta/segura/restore_database_url \
  --key-file /ruta/segura/backup.key \
  --source /ruta/copias/SNAPSHOT.chatbak --scratch /dev/shm \
  --pg-bin /usr/lib/postgresql/17/bin
```

Solo se ejecuta SQL tras validar el tag completo. `psql -X`, ON_ERROR_STOP y
una sola transacción evitan restauraciones parciales. Se rechaza una base con
tablas existentes; no hay `--clean` ni borrado de datos de producción. Se elimina
el esquema public vacío dentro de la transacción para recrearlo desde el dump.

Comprobar la versión Alembic y ejecutar las migraciones pendientes con el checkout
compatible antes de iniciar API/worker. El historial temporal se pierde; no hay
sesiones ni refresh válidos. Todos deben autenticarse y registrar Push de nuevo.
Los cambios de metadatos posteriores al snapshot pueden perderse dentro del RPO.
Mantener Caddy detenido hasta comprobar salud, restauración y claves del sitio.

## 4. Logs JSON y catorce días

Cargar `docker-compose.production.yml` junto al archivo base. Sustituye la
rotación por tamaño por journald. Instalar `journald-chat.conf` como
`/etc/systemd/journald.conf.d/chat.conf`, revisar el presupuesto de disco y
reiniciar journald durante la preparación del host. **Esta configuración afecta
al journal de todo el host**, por lo que debe acordarse con su operador.
Instalar y activar `chat-log-retention.timer` como las unidades anteriores.

MaxRetentionSec=14day, rotación máxima de una hora y vacuum horario eliminan
archivos archivados por edad también en períodos sin tráfico. Granularidad de
hasta una hora; el límite de 1 GiB puede reducir la historia disponible antes
de catorce días. Ajustar capacidad tras medir; no prometer catorce días completos
si el disco no alcanza. No duplicar logs en syslog, exports o backups sin aplicar
la misma política de retención. Comprobar fechas de archivos con journalctl.

API/worker emiten campos controlados; nunca path/query/body ni excepciones crudas.
Caddy elimina request, msg, error y cabeceras de respuesta del log. El wrapper de
PostgreSQL/Redis convierte cada línea en un evento JSON con servicio/fecha y
**descarta el texto completo**, porque un error SQL puede contener datos incluso
sin log_statement. Se pierde el detalle del motor intencionadamente; usar
métricas y consultas administrativas acotadas para diagnóstico. Preserva código
de salida y reenvía TERM/INT, incluido el fast shutdown de PostgreSQL.

La configuración se entrega y valida estructuralmente; la retención de journald
y el wrapper con las imágenes exactas deben verificarse en staging N9.

## 5. Despliegue, red y apagado

N9 actualiza Python a 3.13.15 sobre Debian Trixie y PostgreSQL a 17.11 sobre Alpine 3.23,
manteniendo PostgreSQL 17 y Alembic 0007. Actualiza también Prometheus 3.14.0,
Alertmanager 0.34.1 y node-exporter 1.12.1, con digests fijados. blackbox-exporter
0.28.0 se reconstruye desde su commit oficial con Go 1.26.8 y gRPC 1.79.3:
`operations/blackbox/Dockerfile` fija fuente, checksum y bases. Su imagen oficial
todavía incluye las versiones vulnerables de Go/gRPC. PostgreSQL Debian conserva
un libxml2 afectado sin corrección en esa distribución; la variante Alpine evita
esa dependencia vulnerable. El helper gosu 1.19 se recompila desde su fuente
oficial fijada con Go 1.26.8 en `BD/postgresql/Dockerfile`, porque ambas variantes
oficiales incluyen un binario construido con Go vulnerable. El build comprueba
el cambio al UID/GID de PostgreSQL y CI prueba el arranque desde un volumen vacío.

**No conectar directamente un volumen PostgreSQL Debian a la nueva imagen Alpine.**
Las instalaciones existentes deben conservar su release anterior hasta preparar
y ensayar una migración lógica a un volumen nuevo, con plan de rollback, revisión
de locales y conservación de las fechas TTL. No borrar el volumen anterior.
La copia durable de N8 excluye el historial temporal: no usarla para una migración
que pretenda conservar todos los datos. Una transferencia lógica temporal debe
evitar copias persistentes de contenido TTL y respetar la política de retención.
Revisar además versiones de collation libc/ICU en el destino.
Un cambio de biblioteca puede alterar ordenación e índices de texto, incluido
citext. El operador debe reconstruir los objetos afectados antes de actualizar
su versión de collation; no basta con ocultar la advertencia mediante REFRESH.
Consultar [ALTER COLLATION](https://www.postgresql.org/docs/17/sql-altercollation.html).
Este repositorio no ejecuta REINDEX ni modificaciones sobre bases existentes.
Repetir backup/restore y preparar rollback antes del cambio del host de producción.

Readiness pasa a false al recibir SIGTERM; tickets nuevos y sockets nuevos se
rechazan. Los sockets aceptados disponen de hasta quince segundos: ACK y
ready/send de ofertas ya existentes pueden completar; no se inician ofertas ni
replays. Después se envía close 1001. Hasta dos segundos para cierre de socket
y dos para finalizar tareas; stop_grace_period=25s cubre ese margen. Redis TTL
y reconciliación resuelven las entregas restantes. No prometer entrega al matar
un proceso con SIGKILL. La prueba acelera la espera, conservando el handler real.

Para release usar siempre los dos archivos y el perfil. Detener Caddy, API y
worker; construir, migrar una vez y arrancar solo si la migración tiene éxito:

```sh
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile observability config --quiet
docker compose -f docker-compose.yml -f docker-compose.production.yml stop caddy python worker
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile observability build postgresql python caddy blackbox-exporter
docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm migrate
# Solo tras exit code 0:
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile observability up -d --wait
```

Rollback: restaurar checkout, configuración e imagen anteriores compatibles con
0007. No hacer downgrade automático, restaurar un backup encima de datos activos
ni `down -v`. Conservar evidencia de versión antes de modificar el release.

Firewall: solo administración desde redes autorizadas y 80/443 públicos, también
en IPv6. Comprobar desde otra máquina que 5432/6379/8000/8001/9090/9093/9100/9115
no están accesibles. Las reglas de Docker pueden preceder a UFW; revisar
DOCKER-USER o la política equivalente del backend del host y no habilitar
rutas directas a la red interna. No aplicar reglas que corten SSH sin acceso
alternativo de administración.

Sincronizar el host con NTP/chrony; revisar `chronyc tracking` o la herramienta
del servicio instalado. node-exporter mide timex y alerta con deriva >2s o sin
sincronía; también alerta si no puede medirlo. Si el kernel restringe timex,
resolverlo en staging con un exporter administrado por el host; no conceder
SYS_TIME automáticamente. Comprobar disco >80/90%, RAM >85%, TLS <14 días,
readiness, purga, ocupación PostgreSQL y fallos de Redis bajo carga N9.

## 6. Rotación y respuesta a compromiso

Registrar incidente, detener tráfico cuando proceda y rotar por archivo nuevo
más renombre atómico. No imprimir secretos, ponerlos en argv ni subirlos a Git.
Los bind mounts de archivos requieren recrear contenedores para garantizar que
ven el inode nuevo. Validar pares/configuración antes de reabrir tráfico.

| Secreto | Acción y comprobación |
|---|---|
| Chat JWT, rotación ordinaria | Crear Ed25519 y kid nuevos, publicar la pública, cambiar privada/kid activo y recrear API/worker. Mantener la pública anterior durante el máximo access TTL (24 h), luego retirarla. Probar emisión nueva y caducidad anterior. |
| Chat JWT comprometido | Retirar inmediatamente su kid, cambiar par, revocar auth_sessions y auth_refresh_tokens en una transacción y borrar transfer_upload_grants. Recrear servicios y forzar nueva autenticación; no conservar el kid comprometido durante la transición. |
| Bootstrap de main-app | Coordinar nuevo par en main-app, instalar solo su pública en Chat y retirar el kid comprometido. La privada nunca entra en Chat. Comprobar rechazo del bootstrap antiguo. |
| HMAC de invitaciones | Rotar archivo y revocar todas las invitaciones activas; generar nuevas mediante el flujo normal. Comprobar que los códigos anteriores no se pueden canjear. |
| VAPID | Cambiar privada y pública TOML juntas, revocar las suscripciones existentes y pedir registro nuevo desde el navegador. Probar envío con el nuevo par. |
| PostgreSQL | Cambiar contraseña en el motor por canal administrativo seguro (p. ej. `psql` y `\password chat`), actualizar ambos archivos y recrear dependientes. Cambiar solo postgres_password no modifica un volumen existente. |
| Redis | Cambiar redis_password y redis_url coherentemente, recrear Redis/API/worker. Su estado volátil se pierde y los clientes reconectan con ticket nuevo. |
| SMTP/Alertmanager | Rotar en proveedor y archivo correspondiente, recrear consumidores y comprobar correo/alerta de prueba con destinatario autorizado. |
| Backup | Nueva clave independiente; conservar acceso a claves anteriores mientras existan copias válidas. Si hay compromiso, revisar alcance y volver a cifrar las copias conservadas en un entorno controlado. |

Después de una restauración, volver a aplicar las revocaciones de incidentes
posteriores al snapshot; un backup no acredita que una clave siga siendo segura.

## Fuentes técnicas

La especificación del proyecto determina políticas y tiempos. Mecánica contrastada
con [pg_dump 17](https://www.postgresql.org/docs/17/app-pgdump.html),
[GCM y autenticación antes del uso](https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/),
[Alertmanager y SMTP TLS](https://prometheus.io/docs/alerting/latest/configuration/),
[node-exporter en contenedores](https://github.com/prometheus/node_exporter),
[journald y retención](https://www.freedesktop.org/software/systemd/man/252/journald.conf.html),
[driver journald de Docker](https://docs.docker.com/engine/logging/drivers/journald/) y
[firewall de Docker](https://docs.docker.com/engine/network/packet-filtering-firewalls/).
