# N6 · Votaciones y mensajes de gracia

Alcance: completar §§8, 25.5, 26.4 y 28.1 de la especificación maestra,
aprovechando la apertura y el censo de N4 y el transporte de N5.
Cada subapartado registra su implementación y su evidencia al terminar.
Finalizado el 2026-09-23: **93 pruebas unitarias y 71 de integración correctas**,
Ruff y mypy estricto correctos. API 0.6.0, migración 0006_ballot_electorate.

## N6.1 · Contratos y autorización — completado

GET `/api/v1/votes/{id}` y POST `/api/v1/votes/{id}/ballots` implementados en
`python/chat/vote_api.py`, con `{choice:boolean}` estricto y respuesta `VoteSnapshot`.
Revalidan sesión, pertenencia actual y censo congelado; ocultan votos ajenos con 404.

Evidencia: `test_ballot_contract_permissions_idempotency_and_expiry` comprueba
booleanos estrictos, campos extra, UUID, accesos ajenos y revocación de sesión;
`test_departure_preserves_electorate_and_ballots` verifica pérdida de acceso
tras leave o borrado de cuenta. Se reutiliza la cuota REST autenticada de N2.

## N6.2 · Ballots, concurrencia y plazo — completado

Una elección por elector, repetición idempotente y cambio rechazado con 409.
Se serializa con aceptación, envíos y transiciones; se comprueba el reloj después
de esperar los locks. Se mantienen los 30 s incluso si todos votan antes.

`vote_api.py`, `voting.py` y `vote_store.py` separan contrato, autorización y SQL.
El orden de locks es usuario (`NO KEY UPDATE`), conversación y voto. El INSERT
comprueba el plazo con `clock_timestamp()` en la misma sentencia. Un reintento
del mismo ballot devuelve el snapshot actual incluso después del cierre; un
cambio devuelve 409 `VOTE_CONFLICT`. Un primer voto tardío devuelve 410 `VOTE_EXPIRED`.
El censo y los ballots permanecen tras leave o borrado del usuario; 0006 cambia
la FK del ballot para ligarla al censo, manteniendo la cascada al borrar el voto.

Evidencia: `test_concurrent_ballots_keep_first_choice` enfrenta ocho peticiones
con elecciones opuestas; `test_deadline_checked_after_waiting_for_conversation_lock`
observa el bloqueo real en PostgreSQL y hace vencer el plazo antes de liberar el lock.
Las pruebas N4/N5 previas conservan aceptación única, límite global de cinco,
idempotencia de mensajes y carreras con envíos/cambios de estado.

## N6.3 · Resolución y conservación/eliminación — completado

Se resuelve `yes_votes * 2 > eligible_members`; abstención = NO. Se conserva
ciphertext stored aprobado y se elimina la gracia rechazada sin destruir metadatos
de deduplicación/entrega. Se impiden lecturas de gracia rechazada si se retrasa el worker.

El cierre y DELETE de `messages.is_grace_message` son una sola transacción.
`closed_at=expires_at` representa el cierre lógico. Una consulta GET, un POST
tardío o un reintento de accept también resuelven el voto vencido. `no_votes`
muestra NO explícitos mientras está abierto y `N - yes_votes` al finalizar;
la abstención conserva `my_vote=null`. El historial filtra desde el deadline
la gracia sin mayoría aunque el cierre físico aún no se haya ejecutado.

Evidencia: `test_absolute_majority` y `test_generic_electorates_and_implicit_no`
verifican umbrales con 2/3/5/10 electores. Los grupos son censos sintéticos de prueba;
los endpoints siguen siendo 1:1. `test_stored_grace_history_purge_and_duplicate_without_resurrection`
cubre aprobación/rechazo, tres resolutores concurrentes, historial antes de la
purga, preservación de mensajes posteriores y reintentos sin resucitar contenido.
`test_resolution_rollback_and_census_constraints` provoca rollback tras resolver
y borrar: ambos efectos se revierten juntos; además verifica la FK del censo.

## N6.4 · Worker y eventos por destinatario — completado

Cierre automático idempotente y publicación tras commit. Cada destinatario recibe
su propio `my_vote`; GET recupera el resultado si se pierde Pub/Sub. Ephemeral
comunica el resultado para que el cliente conserve o elimine su copia local.

El worker intenta resolver lotes de hasta 1000 votos entre purgas, con pausa
objetivo de 1 s. Cada voto hace commit por separado y un fallo de publicación
se registra sin impedir el resto del lote. `vote.opened` y `vote.updated` se
construyen para cada elector que conserva acceso, sin revelar ballots ajenos.
No se adelanta ni cancela el resultado por close/leave/upgrade.

Evidencia: `test_worker_process_closes_without_api_reads` ejecuta `python -m chat.worker`
como proceso y observa el cierre en BD sin consultar GET. Las pruebas de WebSocket
usan dos instancias y mensajes de gracia reales en stored/ephemeral, comprueban
snapshots privados, bloqueo de send/offer y reanudación al finalizar. También
comprueban upgrade sin copiar contenido efímero. El fallo de Pub/Sub se inyecta
en `test_pubsub_failure_does_not_rollback_or_starve_other_votes`: el commit
permanece, REST devuelve 503 y el worker sigue resolviendo otros votos.

## N6.5 · Validación y documentación operativa — completado

Actualizados README, niveles, DEC-70–DEC-75, operación y validación. Migraciones
probadas desde cero y desde cada revisión 0001–0005 hasta 0006. No hay nuevas
dependencias. Servicios temporales: PostgreSQL 18.6, Redis 8.0.5 y Python 3.14.4.
Evidencia: `/tmp/chat-persistence-test.g1gFTp`; los servicios se detienen al terminar.
Comandos reproducibles y detalle en [VALIDACION.md](VALIDACION.md).

Los plazos se avanzan en BD en pruebas específicas para verificar el borde sin
esperas de 30 s; las pruebas de apertura comprueban duración exacta de 30 s.
Pub/Sub sigue sin entrega durable ni orden global: consultar GET tras cortes
y al vencer el plazo; un aviso atrasado no debe reabrir un estado terminal.
La interfaz cliente aplica la decisión ephemeral y continúa fuera de esta entrega.
Quedan las puertas generales de Compose/staging, proveedores y carga de N8–N9;
esta validación local no certifica producción. N7 es el siguiente nivel funcional.
