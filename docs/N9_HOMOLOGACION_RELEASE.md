# N9 · Homologación y release

Continuación de N8, 2026-09-24. Fuentes normativas: §§22, 29 y 31.
**En curso; no autoriza un despliegue de producción.** La interfaz cliente,
staging, proveedores reales y capacidad del host siguen siendo puertas pendientes.

## N9.1 · CI y cobertura

`.github/workflows/quality.yml` ejecuta en push, pull_request y workflow_dispatch:

- Ruff sobre aplicación, pruebas y scripts; mypy estricto sobre los 52 módulos
  de aplicación/cliente; actionlint para el propio workflow.
- Unitarias en Python 3.13.12 y pruebas de reglas Prometheus en la imagen fijada.
- Integración en la imagen de tests con PostgreSQL/Redis de Compose, migración
  inicial y pruebas de actualización desde revisiones previas. API/worker se
  arrancan para smoke después de los fixtures, evitando carreras con la purga.
- Cobertura combinada entre host y contenedor mediante rutas relativas.
- Auditoría de todos los locks Python, secretos del código versionado e imágenes;
  vulnerabilidades críticas de las imágenes propias y todas las dependencias de
  Compose, incluida observabilidad. No se silencian hallazgos ni fallos de red.

El token tiene solo contents:read. Acciones fijadas por SHA completo, sin
persistir credenciales en el checkout. Trivy 0.74.0 y actionlint 1.7.12 se descargan
de releases oficiales con SHA-256 fijado; se extrae únicamente el ejecutable.
No hay despliegue, envío de correo ni publicación de imágenes en este workflow.
Solo se publican informes de cobertura y vulnerabilidades; los informes de
secretos quedan fuera de los artifacts porque pueden contener valores detectados.

La cobertura mide **líneas** de todos los módulos `chat` y `chat_client`, sin
excluir bootstrap, worker, errores o código de operación de la aplicación.
Los scripts del host se verifican mediante lint/pruebas, fuera del denominador.
`scripts/check_coverage.py` rechaza informes incompletos y compara sin redondear:

| Puerta | Mínimo | Alcance |
|---|---:|---|
| Global | 85 % | Todos los módulos de chat y chat_client |
| Auth | 90 % | identity, API/DTO, crypto, Redis, store y auth_dependency |
| Invitations | 90 % | invitation_codes, conversations y API/DTO/store |
| Delivery | 90 % | messaging, delivery_store, realtime_redis, reconciliation, websocket_api, ws_protocol y persistence |
| Voting | 90 % | voting, vote_api y vote_store |

El umbral de dominio es agregado por líneas, no media de porcentajes ni mínimo
por archivo. Pruebas de regresión verifican el límite exacto, un dominio por
debajo, cobertura global insuficiente y módulos omitidos.

## N9.2 · Seguridad y robustez

Se añaden fuzzing determinista de JWT/envelopes y alteración de cada byte de firma;
complementan fuzzing existente de invitaciones, Base64URL y cursores.
Se prueba que fallos de configuración, secretos, dependencias, esquema o migración
impiden arrancar y no imprimen la excepción original. El flujo WS real verifica
que tokens, ciphertext, crypto_meta, texto y clave privada no aparezcan en logs;
las pruebas de identidad ya cubren frase de recuperación y verificación.

La auditoría encontró seis avisos distintos (doce entradas de la base de datos)
en pip 25.1.1. Se actualiza a **26.2.0**, también al construir la imagen runtime.
La auditoría posterior de los locks no encuentra vulnerabilidades conocidas.
La política CI es más estricta que el mínimo de severidad: pip-audit bloquea
cualquier vulnerabilidad conocida; Trivy bloquea cualquier secreto y CVE crítico.

## N9.3 · Matriz de aceptación §22

La evidencia local usa API/WS y servicios reales; no equivale a E2E con UI y
proveedores del despliegue. Los módulos citados están bajo `python/tests/`.

| Criterio | Evidencia automatizada | Pendiente de staging/cliente |
|---|---|---|
| Registro, BIP-39, refresh y bootstrap | test_identity, test_identity_integration | SMTP real y experiencia de recuperación |
| WS autenticado y stored/ephemeral | test_websocket_integration | HTTPS/WSS con Caddy e interfaz |
| TTL, purga y pérdida de estado Redis | test_integration, test_persistence_integration, test_websocket_integration | Lag bajo carga y reinicio de contenedores |
| Invitaciones, host/guest, gracia y cierre | test_invitation_codes, test_conversations_integration | Recorrido E2E desde la UI |
| Votación, abstención y retención de gracia | test_voting, test_voting_integration | Aplicación local del resultado ephemeral |
| Upgrade irreversible e idempotencia | test_conversations_integration, test_websocket_integration | Cortes/reintentos en el cliente final |
| Web Push genérico | test_push_integration y receptor HTTPS local | Recepción en navegador/proveedor real |
| Transferencia cifrada y replay | test_recovery, test_recovery_integration | QR, persistencia segura del cliente y recuperación visual |
| Layout de invitaciones y crypto_meta | test_invitation_codes, test_protocol | Interoperabilidad del cliente final |

## N9.4 · Carga y checklist de release

No inventar pico concurrente ni objetivos de latencia. Antes de ejecutar carga,
registrar hardware, pico previsto, mezcla stored/ephemeral, frecuencia de mensajes,
duración, porcentaje offline y destino de staging. Ejecutar al menos 2× ese pico,
medir latencia/errores/recursos/purga y contrastar UUID enviados con estados e
historial para detectar duplicados, pérdidas silenciosas o corrupción.

Checklist §31 todavía pendiente de evidencia del despliegue:

- Migraciones y revisión de esquema de la release; claves independientes,
  kid activo y secrets reales sin ejemplos.
- Caddy HTTPS/WSS, CORS y firewall IPv4/IPv6; Redis volátil y noeviction.
- Correo, Push, tickets y UI E2E en staging equivalente.
- CI verde del SHA exacto, auditorías de sus imágenes y carga ≥2× pico previsto.
- Ausencia de datos sensibles en logs de los contenedores; métricas, NTP/TLS,
  alertas recibidas y retención temporal aplicada por el host.
- Backup durable, restauración, RPO/RTO y purga medidos con volumen representativo.
- Operador capaz de ejecutar rollback compatible y rotación con los
  [runbooks N8](../operations/README.md).

No se modifican protecciones de rama ni se declara `main` lista para producción.
El administrador debe requerir ambos jobs de calidad antes del merge.
Evidencia y comandos reproducibles en [VALIDACION.md](VALIDACION.md).

Referencias técnicas: [seguridad de GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use),
[Coverage.py](https://coverage.readthedocs.io/),
[Trivy: vulnerabilidades](https://github.com/aquasecurity/trivy/blob/main/docs/guide/scanner/vulnerability.md)
y [Trivy: secretos](https://github.com/aquasecurity/trivy/blob/main/docs/guide/scanner/secret.md).
