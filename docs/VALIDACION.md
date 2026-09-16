# Validación de la entrega

## Continuación N1 · 2026-09-16

Se ejecutan **32 pruebas unitarias y 14 de integración**, todas correctas, con
Python 3.14.4 del entorno existente. Ruff y mypy estricto pasan (10 módulos de
`chat`); sintaxis del script local correcta. Persisten los dos avisos de
deprecación del cliente Starlette/httpx/AnyIO, sin fallos.

Las pruebas de integración usan **PostgreSQL 18.6 y Redis 8.0.5 reales**, mediante
binarios oficiales extraídos en `/tmp`, sin instalar servicios del sistema. Cada
ejecución de `scripts/test_local_services.sh` crea un clúster nuevo y un Redis sin
persistencia, usa sockets Unix privados, aplica migraciones y detiene los procesos
al salir. No se han modificado los datos ni los secretos de la aplicación.

Comprobado con esos servicios:

- Alembic desde base vacía y actualización 0001→0002 conservando la conversación
  preexistente y rellenando updated_at desde created_at.
- Commit/rollback de evento, contenido, entregas y gracia; duplicado compatible
  recupera estado delivered incluso tras close; fingerprint incompatible falla.
- Ocho reintentos simultáneos crean un único mensaje; ocho envíos adicionales
  respetan el máximo global de cinco mensajes de gracia.
- UUID concurrente desde conversaciones distintas produce un único ganador;
  creación concurrente de invitación activa respeta el índice único.
- Envío espera el lock de cierre; PostgreSQL muestra la espera en pg_locks y el
  envío se rechaza tras commit del cierre.
- Ephemeral no crea contenido PostgreSQL y un retry tras upgrade tampoco lo crea.
- Historial excluye caducados antes de la purga, aplica pertenencia incluso con
  cursor y pagina correctamente mensajes con el mismo sent_at.
- Constraints reales de crypto_meta, host único, uint32 de generation y nick único.
- Purga separada de contenido/metadatos, cascada de deliveries y conservación del
  historial refresh hasta el vencimiento de toda la familia más 30 días.

**Límite:** Docker vuelve a devolver `permission denied` también con permisos
ampliados. No se acreditan N0, el runtime Python 3.13.12/PostgreSQL 17.9/Redis 8.6.6
de Compose, ni el cierre de N1. Faltan repositorios específicos de flujos futuros y
las puertas de aceptación del despliegue. No hay endpoints nuevos de producto.
No se han repetido auditoría de dependencias, Caddy ni escaneo de imágenes: no
se modificaron dependencias o imágenes en esta continuación.

## Evidencia histórica de infraestructura

Los resultados y limitaciones siguientes corresponden a la entrega anterior;
la ejecución de PostgreSQL/Redis locales descrita arriba amplía esa evidencia.

Diagnóstico ya aportado por el usuario: Caddy se reinicia con ExitCode=255 y
`exec /usr/bin/caddy: operation not permitted`; no alcanza a ejecutar healthchecks.
Se corrige el conflicto entre las file capabilities de la imagen oficial y
`cap_drop: ALL` mediante `caddy/Dockerfile`, retirando la capacidad en el build.
El build comprueba que `getcap` quede vacío y ejecuta `caddy version`. Estas dos
comprobaciones se ejecutarán en el host al construir: Docker sigue inaccesible
desde este entorno, por lo que todavía no se afirma que el contenedor esté sano.
La reproducción anterior usaba el binario descargado sin file capabilities y,
por tanto, no reproducía esta restricción de la imagen oficial.

Diagnóstico posterior de `chat-caddy-1 is unhealthy`: el healthcheck original
respondió correctamente en una reproducción con Caddy 2.11.4. Se verificó en el
registro que la imagen exacta incluye curl. El nuevo script se probó contra ese
Caddy real en puertos temporales: devuelve éxito con proxies inválidos, falla
ante HTTP erróneo y falla cuando el proceso se detiene, conservando diagnóstico.
Compose producción/local, sintaxis sh y las tres pruebas de Compose pasan.
En ese momento faltaban los logs y State del usuario; el diagnóstico y la corrección
posteriores se recogen al principio de este documento. El Docker de ese host sigue
inaccesible desde este entorno.

Corrección posterior al conflicto de puerto: se confirmó un listener en 8080 y
se configuró 127.0.0.1:18080 como publicación local. Las tres variantes de Compose
son válidas y las 24 pruebas vuelven a pasar. Comprobados también un puerto alternativo,
la sincronización CORS en desarrollo, el rechazo de puertos inválidos y que esa
opción no cambie los orígenes de producción. El acceso al socket Docker continúa
denegado en este entorno; el nuevo arranque debe ejecutarse en el host del usuario.

Corrección posterior al error `invalid mount path: 'mode=1777'`: se comprobó
que YAML separaba las opciones de `tmpfs` en dos elementos. Los montajes de
Python/migrate/worker y Caddy usan ahora listas de bloque. Verificadas las tres
combinaciones de Compose, las tres pruebas de Compose (con comprobación de rutas
absolutas añadida), Ruff del archivo modificado y los comandos `make start/stop`
mediante ejecución en seco. No se ha ejecutado un nuevo arranque de contenedores.

Fecha: 2026-09-16. Estos resultados verifican la **base de infraestructura**,
no acreditan las puertas funcionales del MVP.

| Comprobación | Resultado |
|---|---|
| Compose producción | `docker compose config --quiet` correcto |
| Compose local y combinación con test | Configuración combinada correcta |
| Pruebas de configuración, secretos, health, logs, espera y Compose | **24 passed**, 1 prueba de integración excluida |
| Ruff | Sin errores |
| Mypy estricto | Sin errores en 9 archivos de `chat` |
| Compilación Python y sintaxis de scripts sh | Correctas |
| Caddy 2.11.4 oficial | Configuraciones local/producción adaptadas y validadas correctamente |
| SQL PostgreSQL con pglast 8.4 | 35 sentencias en 0001 y 12 en 0002 analizadas correctamente |
| Dependencias runtime (`pip-audit`) | Ninguna vulnerabilidad conocida encontrada en la consulta |
| Imágenes oficiales | Todas las versiones resuelven en el registro; digest fijado |
| DOCX original | Copia íntegra con SHA-256 en `ESPECIFICACION.sha256` |

Las pruebas se ejecutaron con Python3.14.4 del host; el contenedor está fijado a
Python3.13.12 y falta su ejecución real. Dos avisos de deprecación pertenecen al
cliente de pruebas Starlette/httpx/AnyIO; no fueron fallos. El aislamiento inicial
bloqueó los sockets internos del TestClient; las pruebas completas pasaron fuera
de esa restricción. Los procesos de prueba bloqueados se terminaron.

## No comprobado en este entorno

El CLI Docker está instalado, pero el acceso a `/var/run/docker.sock` devolvió
`permission denied`, también al intentar ejecutarlo con permisos ampliados.
Por tanto **no se han construido ni arrancado los contenedores**. En la entrega
inicial tampoco se había ejecutado Alembic contra PostgreSQL real; esa parte se
ha comprobado ahora con los servicios temporales descritos arriba. Sigue pendiente
repetirlo con las versiones exactas de producción.

Quedan pendientes en un host con acceso Docker:

- `sh scripts/test_containers.sh` para migraciones y retención con las imágenes fijadas.
- Arranque limpio, persistencia PostgreSQL, reinicio volátil Redis, migración
  fallida que impida la API, reinicio sin Compose y recuperación de readiness.
- HTTPS/WSS real, certificados, proveedores SMTP/Push, escaneo de imágenes.
- Suite funcional/E2E/concurrencia/cobertura/carga, backups/restauración y alertas,
  conforme se implementen los niveles N2–N9.

Las comprobaciones de Caddy usaron una copia temporal con rutas de importación
ajustadas al host. No se solicitaron certificados ni se publicó ningún servicio.
La auditoría de dependencias es una observación temporal, no sustituye la CI
exigida antes de cada release.
