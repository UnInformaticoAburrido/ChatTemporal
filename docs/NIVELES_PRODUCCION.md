# Niveles de desarrollo y salida a producción

Fuente normativa: especificación maestra v1 adjunta, §§1–31. Las secciones 23–31
prevalecen cuando concretan una regla anterior (§23). La instrucción del usuario
de dar preferencia al documento queda registrada: si una respuesta posterior
contradice sus requisitos, se señalará el conflicto y no se aplicará esa respuesta.
No hay respuestas del usuario que contradigan la especificación en esta entrega.

**Interpretación DEC-00:** «niveles de producción» se organiza como incrementos
de implementación con dependencias y puertas de aceptación. Se preguntó si se
deseaba además el MVP entero; al cerrar esta entrega no se recibió respuesta.
La planificación incluye el MVP completo, mientras el código entrega su base.

| Nivel | Depende de | Entregable | Puerta para avanzar | Estado de esta entrega |
|---|---|---|---|---|
| N0 · Infraestructura | — | Compose, redes, secrets, fuentes montados, healthchecks, cadena de arranque | Arranque en limpio, fallo de dependencia, migración fallida y reinicio sin Compose comprobados en contenedores | Implementado; falta validación real de contenedores |
| N1 · Persistencia | N0 | Alembic, esquema §§17/24.3/28.2, índices, constraints, repositorios y transacciones | BD desde cero y actualización 0001→0002; unicidad y retención | Repositorios base y transacciones implementados; migraciones, concurrencia y retención probadas con servicios locales reales. Pendiente completar repositorios por flujo y validar versiones Compose |
| N2 · Identidad y seguridad | N1 | Registro/verificación/recuperación, sesiones, bootstrap, JWT, rate limiting | Claims, expiración, reuse, revocación y aislamiento probados | REST y persistencia implementadas; pruebas de seguridad con BD/Redis reales. Pendientes proveedor SMTP real, cierre de sockets en N5 y versiones Compose |
| N3 · Claves y contratos | N2 | Pública vigente, DTOs estrictos, errores, cursor, autorización, CORS | Protocolos/bytes correctos; fuzzing y accesos horizontales rechazados | Implementado y probado con servicios reales locales; cliente criptográfico de referencia y lecturas paginadas. Pendientes plataforma cliente final y homologación Compose/staging |
| N4 · Invitaciones y conversaciones | N3 | Códigos, host/guest, pending/active/closed, upgrade/leave | HMAC/layout, privacidad pending, transiciones y locks concurrentes | REST implementado; apertura de voto adelantada de N6. Eventos WS pendientes de N5; homologación Compose/staging pendiente |
| N5 · Mensajería y entrega | N4 | WS ticket, heartbeat, stored, ephemeral, idempotencia, Pub/Sub, reconciliación | Handshake/ACK/TTL/cortes/reintentos y carreras sin pérdidas silenciosas | Pendiente |
| N6 · Gracia y votaciones | N5 | Gracia ≤5, censo congelado, majority_absolute, cierre a 30 s | Concurrencia, bloqueo de envíos y ausencia de voto=NO | Pendiente |
| N7 · Recuperación y Push | N5, N6 | Transferencia, QR cliente, replay y Web Push genérico | Blob/TTL/autorización; replay no persistente; Push real | Pendiente |
| N8 · Operación y seguridad | N0–N7 | Backups/restauración, métricas/alertas completas, logs 14 días, despliegue y rotación | Restauración, fallos, secretos/logs e imagen auditados | Métricas y alertas base; operación completa pendiente |
| N9 · Homologación y release | N0–N8 | CI, integración/E2E, staging equivalente, carga y runbooks | Todos los criterios §22 y checklist §31 cumplidos | Pendiente |

No se estiman fechas sin equipo, hardware ni pico esperado. Cada nivel se cierra
con evidencia reproducible; la existencia de una carpeta o un test simulado no
equivale a una funcionalidad completada.

## N0 · Infraestructura (§§3, 20, 27, 30)

- `python`, `worker` y `migrate` consumen `python/`; scripts SQL desde
  `BD/postgresql/migrations/`; Redis desde `BD/redis/`; Caddy desde `caddy/`.
- PostgreSQL persistente; Redis RAM, sin AOF/RDB, `noeviction`, 256 MiB iniciales.
- Migración one-shot tras disponibilidad real; fallo impide iniciar nueva API.
- Solo Caddy publica 80/443 en producción; red de BD interna; salida para ACME,
  SMTP y Push. `/metrics`, readiness y docs no son rutas públicas.
- Secrets por archivos; comprobar permisos con el UID real del host. Validación
  antes de abrir puertos, sin registrar valores. Ed25519 Chat y bootstrap distintos.
- Verificar en runtime: contenedor detenido, credenciales erróneas, migración
  fallida, Redis caído y recuperación de readiness. Comprobar HTTPS/WSS en staging.

## N1 · Persistencia (§§13–18, 24.3, 28)

- Esquema completo, incluida `auth_refresh_tokens` y `payload_fingerprint`.
  El DDL original se conserva en 0001 y ampliaciones explícitas en 0002.
- `users`, `user_keys`, sesiones, verificaciones, invitaciones, conversaciones,
  miembros, eventos, contenido stored, deliveries, votos, ballots y Push.
- `SELECT ... FOR UPDATE` sobre conversación para send/accept/upgrade/close/leave;
  incremento de gracia e inserción del evento en la misma transacción.
- Repositorios deben excluir contenido expirado inmediatamente, aun antes de la
  purga. Nunca persistir ciphertext ephemeral, replay ni claves privadas.
- Purga al menos cada minuto; objetivo físico <5 min. Historial de refresh hasta
  30 días adicionales tras vencimiento familiar. No destruirlo por cascada prematura.
- Casos: unicidad concurrente de invitación activa/host, duplicados message_id,
  restricciones de crypto_meta, migrations upgrade y fallo transaccional.

### Continuación de N1 · 2026-09-16

Implementado en `python/chat/persistence.py`: unidad de trabajo con commit/rollback,
repositorio básico de usuarios, bloqueo/listado de conversaciones, registro
transaccional de eventos/contenido/entregas y lectura paginada del historial.
Los reintentos comparan el fingerprint y recuperan las entregas existentes sin
incrementar otra vez la gracia; ephemeral no inserta ciphertext en PostgreSQL.
Las consultas aplican pertenencia y caducidad en cada llamada, aunque aún no haya
actuado la purga. Se corrige además la conservación del historial refresh cuando
otra sesión de la familia mantiene vigente el plazo de auditoría.

Son primitivas internas: **no habilitan endpoints de chat**. Los servicios futuros
deben validar autenticación, email, roles y permiso `ready` antes del envío, y
coordinar Redis/publicación después del commit. No deben exponer las entidades
internas directamente como DTOs. La apertura/cierre de votos, transiciones de
conversación, sesiones, invitaciones, claves y Push siguen en sus niveles; sus
repositorios específicos se completarán junto a esos flujos.

Evidencia: 32 pruebas unitarias y 14 de integración pasan con Python 3.14.4,
PostgreSQL 18.6 y Redis 8.0.5 temporales. Incluyen migraciones desde cero y desde
0001, rollback, carreras de UUID/gracia/invitación activa, restricción de host y
crypto_meta, privacidad del historial y retención. Esta alternativa **no cierra
N0 ni sustituye PostgreSQL 17.9/Redis 8.6.6/Python 3.13.12 de Compose**, cuyo daemon
sigue inaccesible. Decisiones no prescritas: DEC-25–DEC-30.

## N2 · Identidad (§§4, 24, 25.2, 27.3)

- Registrar nick/email sin password. BIP-39 inglés de 24 palabras, CSPRNG, NFKD,
  hash Argon2id 64 MiB/t=3/p=1/salt=16/hash=32; frase solo en respuesta inicial.
- JWT EdDSA: kid/sub/sid/iss/aud/iat/nbf/exp/jti; 24 h, skew ≤30 s; comprobar
  estado de sid en cada operación. Recuperación revoca todas las sesiones.
- Refresh opaco de 32 bytes, SHA-256 binario, 30 días; rotación transaccional;
  reuse revoca la familia. No almacenar tokens originales.
- Bootstrap Ed25519 independiente, TTL ≤300 s, `SET NX` para jti; Redis caído=503.
- Email: token 32 bytes, SHA-256, 30 min, un uso. Sin verificar no se permite
  crear/canjear invitaciones, ws-ticket ni mensajería. Cambiar email reverifica.
- Rate limits completos de §27.3; IP real de Caddy solo mediante proxy de confianza.
- Pruebas: sustitución de algoritmo, kid desconocido, token futuro/expirado,
  reuse concurrente, revocación WS, normalización y límites sin filtrado de secretos.

### Continuación N2 · 2026-09-17

Implementados todos los endpoints de identidad de §25.2: registro, perfil propio,
edición/borrado, perfil público autorizado, verificación/reenvío, exchange, recover,
refresh, logout y emisión de ws-ticket. Se completa su repositorio N1 y se añade
la migración 0003_email_hash. Las nuevas dependencias requieren reconstruir Python.

Las pruebas verifican JWT/BIP-39/Argon2id, email de un uso, rotación y reuse
concurrente, revocación durable aun si falla Pub/Sub, aislamiento pending,
cuotas Redis atómicas y fallo cerrado de dependencias. El correo se captura en
un buzón de prueba: no se afirma entrega con un proveedor SMTP real. El emisor
SMTP implementado requiere configurar SMTP_URL; la configuración local de ejemplo
devuelve 503 y revierte el registro, sin generar cuentas a medias.

Todavía no hay `/ws/v1` funcional: N5 consumirá tickets con GETDEL, escuchará
revocaciones y cerrará sockets; emitir un ticket no habilita mensajería. Los límites
de invitaciones/mensajes/replay están configurados para conectarlos a sus futuros
handlers. N2 no se declara homologado para producción. La continuación N3 figura
abajo; se conservan las puertas operativas pendientes.
Decisiones: DEC-31–DEC-44; resultados en VALIDACION.md.

## N3 · Contratos y claves (§§5, 23, 25.1/25.3)

- DTOs rechazan extras con 422 UNKNOWN_FIELD. Errores uniformes, X-Request-ID,
  UTC/RFC3339, UUID canónico, Base64URL sin padding y límites de bytes.
- Guardar una pública X25519 de 32 bytes por usuario; rotación reemplaza fila.
  Consulta solo self o miembro con relación permitida.
- El cliente usa libsodium crypto_box_easy/open_easy; nonce aleatorio único de
  24 bytes, `crypto_meta=nonce||sender_public_key` de 56 bytes, versión separada.
- Límite de texto 256 configurable validado en cliente. El servidor solo comprueba
  bytes cifrados y estructura, sin intentar descifrar ni contar texto.
- Cursor estable `(updated_at,id)` o `(sent_at,id)`; límites 50/100, permisos
  independientes del cursor. Pruebas de errores, bytes, fuzzing y privacidad.

### Continuación N3 · 2026-09-17

Implementadas alta/sustitución, rotación y consulta autorizada de la pública vigente.
Los DTOs validan bytes/versiones y mantienen identidad oculta durante pending,
incluso al consultar claves. La lectura de conversaciones y el historial stored
ya tienen cursor opaco, desempate por UUID, límites 50/100 y autorización en cada
consulta; leave y revocación de sesión impiden continuar. Se excluye contenido
caducado sin esperar al worker. Estos GET adelantan la lectura de N4/N5, sin
añadir creación de conversaciones ni envío REST.

`python/chat_client/crypto.py` implementa el cliente criptográfico de referencia:
Curve25519/X25519, crypto_box_easy/open_easy, nonce de 24 bytes y metadatos de 56.
Su límite de texto es configurable, con 256 puntos de código Unicode por defecto.
La API no importa este módulo ni recibe privadas. El parser estricto de
message.send queda preparado para N5; aún no hay `/ws/v1` funcional ni UI cliente.

Evidencia: **70 pruebas unitarias y 28 de integración (98 total)**, Ruff y mypy
estricto de servidor/cliente. Incluyen interoperabilidad libsodium, alteración de
ciphertext, fuzz determinista de binarios/cursores, JSON ambiguo, CORS, rotación
concurrente, paginación con fechas empatadas y accesos horizontales rechazados.
Servicios temporales PostgreSQL 18.6/Redis 8.0.5; las versiones exactas de Compose,
SMTP real, WS, CI/cobertura y staging siguen pendientes. N3 no certifica producción.

El siguiente desarrollo tras aquella entrega era N4; su implementación figura abajo.
No hay nueva migración ni dependencia de runtime del servidor en N3.
Decisiones no prescritas: DEC-45–DEC-52; detalle reproducible en VALIDACION.md.

## N4 · Invitaciones y conversaciones (§§6–9, 25.4/25.6, 28.1)

- Una identidad activa por usuario; dos códigos estables hasta regeneración.
  Payload binario de 30 bytes big-endian + HMAC-SHA-256 32 bytes; token 83 chars.
- Invitar no expone user_id/nick. `peer=null` para ambos durante pending; guest
  recibe pública del host al canjear para cifrar gracia.
- Host acepta y convierte ephemeral→stored solo en active. Cambio irreversible,
  solo mensajes posteriores; closed no se reactiva; nuevo contacto=nuevo pending.
- Regeneración revoca ambos códigos en una transacción. Reminder semestral solo
  cliente. Leave termina el intercambio 1:1 sin introducir multi-dispositivo normal.
- Tests: HMAC inválido/truncado, uint32, permisos, regeneración, carrera
  close/send, upgrade/send y visibilidad de todos los endpoints.

### Continuación N4 · 2026-09-21

Implementados GET/regenerate/redeem de invitaciones y accept/upgrade/close/leave.
Los códigos son HMAC-SHA-256 con layout normativo, sin identidad pública y estables
hasta regenerar. Se valida email/sesión y se aplican cuotas por usuario/IP.
Las transiciones bloquean la conversación y conservan privacidad pending.
Leave cierra el intercambio 1:1; closed nunca se reactiva. Upgrade no copia
mensajes efímeros anteriores a PostgreSQL.

Accept abre exactamente un voto de 30 s y congela número e identidades de electores.
La migración 0004_vote_electorate conserva esas identidades incluso si un usuario
abandona o elimina su cuenta. Se adelanta esta apertura porque §25.4 exige devolver
VoteSnapshot. Ballots, resolución, purga de gracia y notificaciones siguen en N6;
la fila puede continuar open después del plazo hasta implementar esa resolución.
No se reconstruyen censos de votos creados por herramientas externas antes de N4.

Las pruebas de concurrencia descubrieron y verifican la corrección de un deadlock
entre upgrade y las FK de entregas: las transiciones usan NO KEY UPDATE en users,
compatible con KEY SHARE, y FOR UPDATE en conversations. Regenerar/canjear se
serializa por usuarios y fila de invitación; canjes mutuos bloquean usuarios por UUID.
Evidencia y límites en VALIDACION.md; decisiones DEC-53–DEC-59.

El siguiente desarrollo es **N5: WebSocket y entrega**. Los eventos de conversación
y la cancelación de offers aún no existen; no se afirma un flujo completo de chat.

## N5 · WebSocket (§§10, 15, 26, 28.2/28.4)

- `/ws/v1?ticket=...`, ticket REST one-time de 30 s, hash en Redis + GETDEL.
  Nunca JWT en URL. session.ready y heartbeat ping/pong 25/75/presence90.
- Envelope estricto, frames JSON, máximo 8192 bytes; close codes §26.7.
- Stored: transacción events/messages/deliveries; ACK no borra el contenido;
  offline recupera historial por REST. Aclarar confirmación de aceptación DUD-07.
- Ephemeral: offer sin ciphertext → ready → send → new → ACK → DEL → delivered.
  Offer no crea evento; offer y delivery TTL inicial 60 s, configurables.
- Desconexión antes de ACK elimina payload y marca fallo. Nuevo intento definitivo
  usa nuevo UUID. Guardar fingerprint SHA-256(version_be32||meta||ciphertext).
- Duplicado compatible devuelve estado sin efectos; incompatible=conflict.
  Dedupe dura 30 días; cliente no reutiliza UUID jamás.
- Redis Pub/Sub para sockets/eventos; operaciones fallan cerradas si Redis falla.
  Reconciliar entregas huérfanas tras caída; no inferir modo histórico del modo
  actual de conversación. Resolver estado ready documentado en DUD-06.
- Tests reales: dos clientes, receptor offline, caída de proceso/Redis, ACK
  repetido, timeout, concurrencia upgrade/close y reinicio sin persistencia Redis.

## N6 · Gracia y votación (§8, 28.1)

- Contador ≤5 transaccional/idempotente. Accept abre voto y bloquea offer/send
  durante exactamente 30 s; censo congelado; un ballot por usuario.
- majority_absolute: sí > N/2; sin voto=NO. Repetir choice es idempotente,
  cambiarlo=409. Comprobar deadline aunque scheduler no haya cerrado.
- En stored eliminar gracia rechazada; en ephemeral notificar resultado para
  aplicación local. Emitir vote.opened/updated con estado autorizado por usuario.
- Worker cierra votos y publica resultado; separar reloj lógico exacto de demora
  del scheduler. Tests con N=2/3/5/10, voto tardío y envíos simultáneos.

## N7 · Recuperación y Web Push (§§5.2/5.3, 12, 25.3/25.5)

- Transferencias propiedad del usuario, blob cifrado ≤65536 bytes, una carga,
  TTL total ≤24 h; GET reintentable, DELETE confirma y elimina inmediatamente.
  Secreto de QR jamás llega al servidor.
- Replay ordenado y efímero; nunca `messages` ni duplicación del historial stored.
  Rate limit 100 frames/s por replay. Historial perdido sin copia se considera irrecuperable.
- Push genérico: stored al aceptar send, ephemeral offer si offline. Solo metadatos
  permitidos, sin texto/ciphertext. SMTP y Web Push se prueban con proveedores reales.
- Coexistencia de dos dispositivos solo para transferencia; autenticación de ambos
  y límites de convivencia requieren concretar DUD-08 antes de implementar.

## N8 · Operación (§§27, 30)

- Completar métricas REST/WS/errores/timeouts/memoria/pool/cleanup/reuse/Push y
  alertas §30.4 con receptor operativo real. Prometheus inicial no envía avisos.
- Logs JSON seguros; revisar también errores de proxy/librerías. Docker rota por
  tamaño, **no garantiza 14 días**: configurar colector/retención temporal de 14 días.
- Backups lógicos cifrados cada 6 h; 7 días frecuentes y 4 semanales. Solo esquema,
  migraciones y tablas durables autorizadas. Excluir datos de messages, events,
  deliveries, sessions, refresh, email tokens, votes y ballots. Nada de snapshot
  de volumen PostgreSQL ni Redis como copia de larga duración.
- Restauración mensual: RPO ≤6 h, RTO ≤2 h. Se acepta pérdida de historial stored
  y reautenticación tras pérdida total; no respaldar datos TTL por conveniencia.
- NTP y alerta deriva >2 s; firewall host, disco/RAM, TLS próximo a vencer,
  rotación/compromiso de claves, restore y rollback compatible sin downgrade automático.
- SIGTERM: readiness false, bloquear tickets/sockets nuevos, hasta 15 s de drain,
  cierre1001; probar con entregas en vuelo cuando N5 esté implementado.

## N9 · Calidad y release (§§22, 29, 31)

- pytest completo; cobertura global ≥85 %, auth/invitations/delivery/voting ≥90 %.
- Ruff, mypy estricto dominio/API, auditoría de dependencias y secretos; escaneo de
  imagen sin críticos no exceptuados. Fijar versiones y actualizarlas vía CI.
- BD real desde cero y migración desde versión anterior; suite integración,
  concurrencia, autorización horizontal, fuzzing y ausencia de secretos en logs.
- Ejecutar todos los criterios §22 en staging equivalente. Carga ≥2× pico estimado;
  medir latencia en hardware real, sin duplicados ni pérdidas silenciosas.
- Checklist §31 íntegro, backup y restauración probados, alertas recibidas y
  operador capaz de ejecutar rollback/rotaciones. Solo entonces declarar producción.

Fuera de v1: adjuntos, grupos operativos, multi-dispositivo permanente, nuevas
políticas de voto y lógica de marketplace adicional (§21).
