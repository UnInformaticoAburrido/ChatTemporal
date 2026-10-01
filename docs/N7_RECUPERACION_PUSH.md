# N7 · Recuperación y Web Push

Continuación de N6. Se documenta cada subapartado antes de implementarlo y se
completa su evidencia al validarlo. Fuente: §§5.2/5.3, 12, 25.3/25.5, 26 y 28.
Implementación finalizada: API 0.7.0, migración 0007_recovery_push, **108 pruebas
unitarias y 84 de integración correctas (192 total)**, Ruff y mypy estricto
en 50 módulos. No hay nuevas dependencias. Se mantiene pendiente homologar
con navegador/proveedor Push real y el entorno exacto de producción.

## N7.1 · Acceso temporal entre dispositivos — completado

Resolver DUD-08 conservando una sola sesión normal. Exchange revoca como antes
las sesiones previas y concede al último dispositivo una autorización residual
exclusiva para PUT del blob hacia su sucesor, máximo 24 h y sin renovar access.
No se comparte un token entre dispositivos, ni se permite al anterior usar
REST general, WebSocket o refresh. Recover no concede esta autorización.
Es una decisión de implementación; no se atribuye una aprobación al usuario.

Evidencia: `test_real_device_handoff_keeps_old_session_upload_only` ejecuta
exchange con un bootstrap real, deniega REST/WS/POST al dispositivo anterior,
permite su PUT al sucesor y verifica que solo queda una sesión normal. La prueba
`test_new_login_and_expiration_invalidate_old_upload_grant` cubre caducidad,
nuevo reemplazo, denegación de acceso al blob del destino anterior y logout.

## N7.2 · Transferencias y cliente QR — completado

POST/PUT/GET/DELETE normativos; propiedad y sesión destino verificadas, una carga
atómica, máximo 65536 bytes, TTL desde creación sin prolongaciones. Redis guarda
blob cifrado y metadatos, nunca secreto QR. Cliente de referencia cifra/descifra
y genera/valida el contenido del QR localmente, sin biblioteca de interfaz.

Redis separa blob (`key_transfer:{id}`) y metadatos; Lua comprueba propietario,
sesión destino y carga única sin carreras. GET no elimina. DELETE revoca además
el permiso residual del predecesor. Un login posterior invalida permisos anteriores.
El codec usa XChaCha20-Poly1305 de libsodium y autentica UUID/versión como AAD;
el PUT solo contiene encrypted_blob. Renderizar/escanear QR corresponde a la UI.

Evidencia: carga concurrente con un solo 204 y tres 409; blob de 65536 bytes
aceptado y exceso rechazado con 413; aislamiento de propietario, GET reintentable,
TTL restante sin ampliación y DELETE inmediato en Redis. Las pruebas locales
comprueban roundtrip, alteración del ciphertext, transfer_id incorrecto, QR
ambiguo/versión inválida y ausencia del secreto en el PUT y repr.

## N7.3 · Replay efímero — completado

Frames begin/item/end estrictos y ordenados, autorizados por conversación,
ligados a conexiones y limitados a 100 items/s por replay. No escriben mensajes,
eventos, entregas ni historial. Desconexión/pérdida de Redis exige reiniciar.

El cliente `replay_history` vuelve a cifrar una copia local con la pública nueva,
genera begin/items/end y preserva identificadores/fechas opcionales. Sequence
empieza en cero y end exige el número exacto de items. Redis solo conserva
metadatos durante 15 min; Lua publica y avanza el contador atómicamente. Se exige
conversación active, sin voto bloqueante, y ambos participantes conectados.

Evidencia: sockets reales en dos instancias, secuencia y conteo inválidos,
end correcto, rechazo posterior al end, metadatos sin ciphertext en Redis,
ausencia de message_events/push_jobs, receptor offline, aislamiento de usuario
y conexión y RATE_LIMITED sin consumir la secuencia. El codec de cliente genera
frames que el parser acepta y cuyo contenido descifra la nueva clave receptora.

## N7.4 · Suscripciones y envío Web Push — completado localmente

Upsert por endpoint del mismo usuario, revocación propia y notificaciones
genéricas al aceptar stored o emitir offer con receptor offline. Envío cifrado
con VAPID, sin texto, ciphertext de chat, claves privadas ni secretos de QR.
Timeouts, endpoints caducados y reintentos no deben revertir mensajes aceptados.

El worker consume una cola durable de UUID/event_type/plazos/reintentos, sin
texto ni ciphertext del chat. Stored encola junto al commit, ephemeral al crear
offer offline. Revalida pertenencia, sesión y vigencia de la oferta antes de enviar.
Los recibos por suscripción evitan reenviar los éxitos cuando otra falla. Hay hasta
cinco intentos dentro del TTL; 404/410 revocan la suscripción. La sesión anterior
deja de recibir tras exchange/recover. Una caída después del envío y antes del
commit puede duplicar un aviso: el receptor debe deduplicarlo.

El transporte implementa los RFC 8291/8292 con primitivas ya disponibles. Valida
HTTPS/443, claves, DNS e IP públicas; fija la conexión a una dirección comprobada,
verifica TLS/SNI y no utiliza proxies ni sigue redirects. Se usa una clave ECDH
efímera distinta de la VAPID, salt aleatorio, HKDF y AES-GCM.

Evidencia: vector público RFC 8291 exacto; servidor HTTPS local con certificado
verificado, validación de VAPID y descifrado independiente del payload; rechazo
de direcciones locales/privadas y DNS mixto; upsert/propiedad/revocación; stored
offline, deduplicación, reintentos con recibos, 404/410 y offers solo offline.
Estas pruebas no se presentan como recepción en FCM/Firefox/Apple ni UI de navegador.

## N7.5 · Validación, operación y publicación — completado

Suite completa de 192 pruebas correcta con Python 3.14.4, PostgreSQL 18.6 y
Redis 8.0.5, más Ruff y mypy. Migraciones desde cero y desde 0001–0006 hasta 0007.
Logs locales reproducibles en `artifacts/n7/{unit,integration}.log` (ignorados
por Git); servicios aislados detenidos al terminar. Tras perderse el entorno
temporal se reconstruyó en `.venv` y `artifacts/n7`, sin instalar servicios del host.

Actualizados README, niveles, DEC-76–DEC-85, operación y validación. Publicación
en rama N7 sobre N6. Pendientes generales: interfaz cliente (incluido escaneo QR),
homologación con proveedor/navegador Push y SMTP reales, versiones Compose, staging
y carga. N8 es el siguiente punto; N9 conserva la homologación global.
