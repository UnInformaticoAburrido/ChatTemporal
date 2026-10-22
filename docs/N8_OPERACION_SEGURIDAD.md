# N8 · Operación y seguridad

Continuación de N7, validada el 2026-09-24. Fuente: §§27 y 30. Implementación
terminada y probada localmente; la aceptación en producción requiere las
comprobaciones de staging N9 indicadas abajo. No hay migración nueva:
`0007_recovery_push` sigue siendo la revisión vigente.

## N8.1 · Copias y recuperación — implementado

Backup lógico cifrado cada seis horas; lista positiva de datos durables,
esquema y versión Alembic. Excluir también las suscripciones Push porque
dependen de sesiones que no se respaldan. Retención de siete días y cuatro
copias semanales. Restauración autenticada en una base vacía, ensayo mensual,
comprobación de exclusiones y medición de RPO/RTO. No copiar volúmenes ni Redis.

`chat.backup` cifra en streaming con AES-256-GCM un volcado lógico de snapshot
único. Solo guarda datos de alembic_version, users, user_keys, invitations,
conversations y conversation_members; tablas futuras quedan excluidas por defecto.
Verifica autenticidad completa antes de ejecutar SQL y rechaza destinos ocupados.
El timer hace backup a las 00/06/12/18 UTC y ensayo el día 1 a las 01:00 UTC.
Los servicios oneshot usan `TimeoutStartSec=2h` para limitar su ejecución.

Evidencia: restauración en PostgreSQL real, claves incorrectas/manipulación
rechazadas, destino intacto ante tag inválido, sesiones/mensajes y tabla TTL futura
vacías después de restaurar, datos durables y versión conservados. Se verifica
retención, modo 0600 y que un dump fallido no publique ni elimine copias previas.
El ensayo sintético tarda menos de 120 s; no acredita RTO con volumen real ni
recuperación tras pérdida del host. La imagen de tests incluye cliente PostgreSQL
17 de la misma imagen fijada que el servidor Compose; su construcción está pendiente.

## N8.2 · Métricas y alertas — implementado

REST, WebSocket, dependencias, capacidad, limpieza, refresh reuse y Push.
Alertas de §30.4, host/NTP/TLS y entrega a receptor configurable. Las etiquetas
no contienen usuarios, UUID, direcciones de correo, tickets ni endpoints Push.

API y worker exponen métricas internas; Prometheus consulta también node-exporter,
blackbox-exporter y Alertmanager. Se mide ocupación de conexiones PostgreSQL,
sin atribuir un pool inexistente a la aplicación. Reglas con pruebas promtool;
receptor SMTP configurable por archivo y contraseña montada como secret.

Evidencia: métricas con PostgreSQL/Redis reales, etiquetas sin path/query aportados
por clientes, reglas promtool correctas y Alertmanager real entregando firing y
resolved a HTTP loopback. Falta confirmar envío/recepción por el SMTP operativo,
TLS del sitio, métricas del host y umbrales bajo carga.

## N8.3 · Logs y retención — implementado

Salida segura y retención temporal de catorce días en producción. Distinguir
la rotación de tamaño del borrado por antigüedad y comprobar ausencia de secretos.

Override Compose con journald, configuración y vacuum horario del host. Caddy
elimina request/msg/error/resp_headers; PostgreSQL/Redis descartan el texto de sus
logs y emiten eventos JSON controlados. El wrapper reenvía señales y conserva
el resultado final del proceso hijo, también durante un cierre limpio por TERM.

Evidencia: Compose fusionado sin opciones de rotación por tamaño, solo Caddy
publica puertos; prueba de texto sensible descartado, exit 7 conservado y cierre
por TERM con exit 0. Retención física, presupuesto de disco y comportamiento con
las imágenes exactas deben comprobarse en staging. La política journald afecta
al host entero y puede retener menos de catorce días si se alcanza el límite de disco.

## N8.4 · Despliegue y apagado — implementado

Readiness y tickets cerrados desde SIGTERM; margen de hasta quince segundos
para entregas pendientes antes de cerrar con 1001. Runbooks de firewall,
rotación/compromiso y rollback compatible, sin downgrade automático.

Evidencia: handler real SIGTERM con API/WS reales; readiness y tickets pasan a
503, stored completa ACK y ephemeral completa ready/send/ACK de una oferta
preexistente durante el drain. Ambos cierran con 1001. La prueba usa una espera
reducida de 1,5 s; producción permite 15 s. Runbooks en
[operations/README.md](../operations/README.md), incluidos restore, firewall,
NTP, rotación de secretos y reaplicación de revocaciones después de restaurar.

## N8.5 · Validación y publicación

Suite base: 115 pruebas unitarias y 88 de integración correctas; se añade una
regresión del cierre del wrapper y se ejecutan las ocho pruebas de operación.
Resultado acumulado: **116 unitarias y 88 de integración**. Ruff y mypy estricto
en 52 módulos correctos, configuración Compose y reglas de alertas verificadas.
Comandos y límites en [VALIDACION.md](VALIDACION.md).

Se continúa sobre `work-in-progress/n7-recuperacion-push`, conservando el trabajo
N8 encontrado en el checkout. Siguiente nivel: N9 (CI/cobertura, auditorías,
contenedores, staging y carga). La interfaz cliente sigue pendiente; esta entrega
no declara completo el MVP ni certifica producción.
