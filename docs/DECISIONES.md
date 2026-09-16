# Decisiones, dudas y respuestas

La especificación original y su SHA-256 se conservan en `docs/`. Los comentarios
`DEC-*`/`DUD-*` junto al código enlazan conceptualmente con este registro.
«No solicitada» o «sin respuesta» nunca significa aprobación del usuario.

## Consulta realizada

- **Pregunta:** interpretar niveles como fases + base ejecutable, o implementar
  además todo el MVP.
- **Respuesta recibida:** ninguna al preparar la entrega.
- **DEC-00:** fases completas y base de infraestructura. No cambia ningún requisito
  del producto; las funcionalidades pendientes se enumeran en los niveles.

## Discrepancias internas

| ID | Referencias y duda | Interpretación / trabajo pendiente | Respuesta del usuario |
|---|---|---|---|
| DUD-01 | §15 sugiere borrar key transfer tras descarga; §25.3 dice GET no borra | Aplicar §25.3: GET reintentable, DELETE confirma; implementar N7 | No solicitada; §23 da prioridad normativa |
| DUD-02 | §18 borra sesiones al expirar y cascada elimina historial; §28.3 exige 30 días extra | Purga conserva sesiones hasta fin de auditoría familiar; implementado en worker | No solicitada; regla específica §28.3 |
| DUD-03 | §23.4 pagina conversations por updated_at, ausente de DDL §17 | Añadir campo e índice; dominio actualizará con actividad (DEC-17) | No solicitada; decisión técnica explícita |
| DUD-04 | generation:uint32 (§7) no cabe entero en INTEGER firmado (§17) | BIGINT limitado a 1..4294967295; conserva formato binario (DEC-18) | No solicitada; decisión técnica explícita |
| DUD-05 | §7/§25.6 detallan de forma diferente anonimato pending | §25.6: peer=null en ambos; clave host en redeem; aplicar N4 | No solicitada; regla específica |
| DUD-06 | §15 elimina offer al ready, pero send posterior exige probar ese ready | Propuesta N5: permiso temporal sin ciphertext en Redis, TTL limitado al intento. No está implementada; comentar nombre/TTL y revisar antes de N5 | No solicitada; pendiente de concretar |
| DUD-07 | §26.5 exige confirmar stored mediante estado, sin evento exacto de aceptación en §26.4 | REST status sí está definido; no inventar `message.accepted`. Aclarar respuesta WS al emisor antes de N5 | No solicitada; pendiente de aclaración |
| DUD-08 | MVP limita sesiones/dispositivos, pero transferencia requiere coexistencia | Concretar flujo de acceso al dispositivo nuevo y revocación del anterior antes de N7, sin introducir uso multidispositivo permanente | No solicitada; pendiente de aclaración |

También se incorpora en 0002 el historial refresh (§24.3) y fingerprint (§28.2),
ausentes del DDL inicial. No son funciones opcionales ni contradicciones: son
ampliaciones normativas. Los valores previos nulos de fingerprint solo conservan
compatibilidad de esquema; todo mensaje nuevo debe incluirlo.

## Decisiones no prescritas literalmente

| ID | Decisión y motivo |
|---|---|
| DEC-01 | Compose base de producción + override local explícito; evita mezclar puertos y TLS de ambos entornos. Fuentes `:ro` por bind mount, como pide el usuario. |
| DEC-02 | Worker independiente comparte `python/`; evita programadores duplicados por proceso HTTP. Solo implementa purga inicial. |
| DEC-03 | Caddy UID1000 y puertos internos8080/8443; init temporal solo prepara volúmenes. Publicación real80/443. |
| DEC-04 | Prometheus mediante perfil observability durante desarrollo; métricas completas y alertas obligatorias para cerrar N8. |
| DEC-05 | HTTP solo local y loopback18080 (configurable mediante DEC-22) para probar sin dominio/certificado; producción sigue HTTPS/WSS. |
| DEC-06 | PostgreSQL17.9, Redis8.6.6, Caddy2.11.4, Python3.13.12; versiones concretas, actualizables tras validación. PostgreSQL50 conexiones/128MiB para base pequeña. |
| DEC-07 | Redis autenticado, configuración temporal y sin slowlog; evita argumentos sensibles y almacenamiento durable. Host debe evitar swap/core dumps de memoria sensible. |
| DEC-08 | Sin access logs completos en proxy/Uvicorn; aplicación emite endpoint lógico sin URI/query/body. Caddy filtra URI y cabeceras de sus logs de runtime; completar auditoría con flujos reales en N8. |
| DEC-09 | Imágenes fijadas por versión y digest comprobado en el registro; dependencias directas y transitivas fijadas en lock. Entorno Python separado de /app para que bind mount no lo oculte. |
| DEC-10 | TOML para configuración pública, extensiones `DATABASE_URL_FILE`, `REDIS_URL_FILE`, `SMTP_URL_FILE` para cumplir secrets sin .env de producción. |
| DEC-11 | Espera de120s con comprobaciones reales cada segundo; purga cada30s, cumpliendo frecuencia ≤1min. |
| DEC-12 | Métricas/alertas iniciales internas; receptores de avisos, exporters y retención temporal de logs pendientes N8. |
| DEC-13 | Advisory lock de migración y comprobación de Alembic head al arrancar; un reinicio directo no debe saltarse el esquema. |
| DEC-14 | Un proceso API inicial; concurrencia asíncrona y futuro escalado tras Pub/Sub y pruebas. |
| DEC-15 | Advisory lock de purga evita solapes durante despliegues. No incluye aún cierre de votos/reconciliación de entregas. |
| DEC-16 | Generador local sin dependencias Python externas, CSPRNG + OpenSSL; no sobrescribe ni imprime secretos, bootstrap privado no se monta. |
| DEC-17 | Añadir updated_at a conversations por el contrato de paginación. |
| DEC-18 | Ampliar generation a BIGINT+CHECK por el uint32 normativo. |
| DEC-19 | Índice compuesto con UUID como desempate para historial ordenado. |
| DEC-20 | Proyecto de integración `chat-tests` y volúmenes distintos; no exponer puertos ni borrar volúmenes automáticamente. |
| DEC-21 | A petición del usuario, `make start` inicia con `up --no-build --wait`: reutiliza imágenes, aplica cambios de configuración y respeta dependencias tras un arranque incompleto. `make stop` conserva contenedores y datos. |
| DEC-22 | Ante el conflicto comunicado y confirmado en el puerto 8080, se publica Caddy local en 127.0.0.1:18080. `CHAT_HTTP_PORT` permite elegir otro puerto, Caddy mantiene el interno 8080 y CORS añade los orígenes loopback correspondientes solo en desarrollo. No se modifica ni detiene el servicio que ocupa 8080. Producción mantiene 80/443 según §30.1. No requiere aclaración del usuario: es una corrección del arranque local. |
| DEC-23 | Tras el aviso `chat-caddy-1 is unhealthy`, el healthcheck usa curl de la imagen oficial, ignora proxies/configuración de usuario, aplica un timeout de 2 s y deja el error visible en Docker inspect. Se añaden 15 s de margen de arranque. La reproducción con Caddy 2.11.4 respondió 200. Posteriormente el usuario aportó logs y State: el proceso falla antes del healthcheck; se corrige mediante DEC-24. |
| DEC-24 | El usuario aportó `exec /usr/bin/caddy: operation not permitted`, State=restarting, ExitCode=255 y Health.Log vacío. La imagen oficial marca el binario con `cap_net_bind_service=ep`, incompatible con la retirada de capacidades observada. Se deriva `chat-caddy:0.1.0` y se retira esa capacidad durante build, ya que usa puertos internos altos. Se conservan UID1000, cap_drop ALL, no-new-privileges y read_only. Se requiere reconstruir Caddy una vez con `sudo make up`; no se modifican secretos ni datos. |
| DEC-25 | Continuación autorizada por el usuario: abordar primero la persistencia pendiente de N1. Repositorios internos con SQL explícito y Psycopg ya instalado, sin otro ORM. Cada unidad de trabajo abre una conexión y una transacción; commit al terminar y rollback al fallar/cancelar. Se eligen connect_timeout=3 s, lock_timeout=5 s y statement_timeout=10 s para acotar esperas. No hay commits dentro de cada repositorio. El pool y los repositorios específicos de identidad/invitaciones/votos se integrarán con sus servicios; esta entrega no da N1 por cerrado. |
| DEC-26 | Cada registro de mensaje usa un savepoint dentro de la transacción de la unidad de trabajo: evita escrituras parciales aunque el servicio capture un conflicto y continúe. Se bloquea primero la conversación, se compara el fingerprint y el INSERT global usa ON CONFLICT DO NOTHING; si compite otro envío desde otra conversación se consulta su resultado tras el commit rival. Los retries compatibles recuperan el estado incluso si la conversación se cerró o cambió de modo después del envío original. El servicio solo podrá producir efectos nuevos con created=True y tras commit; Redis, publicación y reconciliación siguen pendientes N5. |
| DEC-27 | Se toma clock_timestamp() después de adquirir el lock para sent_at; now() representa el inicio de la transacción y puede quedar atrasado durante una espera. Historial usa statement_timestamp() para excluir filas vencidas al comenzar cada consulta, incluso dentro de una transacción larga. El contenido stored usa el máximo permitido de 30 días desde el envío; no se añade una política de retención configurable no solicitada. |
| DEC-28 | Paginación interna por fecha+UUID, con límites 1–100; N3 codificará/validará el cursor público. Las consultas de historial/listado comprueban pertenencia por separado y excluyen miembros left (el intercambio termina para ellos, §25.4). Devuelven solo sender_role en el historial para respetar el anonimato pending. El repositorio devuelve lista vacía si no hay filas autorizadas; el futuro servicio REST deberá distinguir los errores normativos de ausencia/modo/permisos antes de invocarlo. Los registros internos de usuario ocultan email/hash en repr, pero nunca deben serializarse como UserPublic. |
| DEC-29 | La purga comprueba el vencimiento tanto de los refresh como de las sesiones de la misma familia antes de eliminar tokens antiguos. La regla de retención viene de §28.3; esta consulta evita perder evidencia si la sesión conserva un vencimiento posterior al de los tokens disponibles. No cambia el plazo normativo de 30 días adicionales. |
| DEC-30 | Ante Docker inaccesible incluso con permisos ampliados, añadir scripts/test_local_services.sh para pruebas reales con binarios locales. Cada ejecución crea un clúster PostgreSQL y un Redis nuevos en /tmp, exclusivamente con sockets Unix en directorio 0700, sin puertos TCP ni datos del proyecto; los procesos se detienen al salir y se conservan evidencias sintéticas. Los binarios oficiales se extrajeron en /tmp sin instalación del sistema. PostgreSQL 18.6/Redis 8.0.5 son los disponibles para esta comprobación, no una actualización de producción: siguen pendientes las versiones fijadas en Compose. Los tests de Alembic crean/eliminan únicamente bases temporales con nombre aleatorio propio. |

Referencia de DEC-24: el [Dockerfile oficial de Caddy](https://github.com/caddyserver/caddy-docker/blob/master/2.11/alpine/Dockerfile)
ejecuta `setcap cap_net_bind_service=+ep /usr/bin/caddy`. La comprobación de
capacidades al ejecutar un archivo puede devolver EPERM si no se pueden conceder
las capacidades efectivas del archivo ([capabilities(7)](https://man7.org/linux/man-pages/man7/capabilities.7.html)).

Corrección tras el error comunicado por el usuario: las listas inline `tmpfs`
separaban `mode=1777` como si fuera un montaje independiente. Se sustituyen por
listas de bloque que preservan las opciones del montaje en una sola cadena.
No cambia ningún requisito de la especificación. La prueba de Compose comprueba
ahora que todos los destinos tmpfs sean rutas absolutas.

Ningún comentario afirma que el usuario aprobó una decisión que no respondió.
Las dudas de N5/N7 no bloquean esta base; deben resolverse al implementar esos
contratos. Una respuesta que contradiga la especificación se registrará con la
cita correspondiente y se descartará conforme a la instrucción del usuario.
