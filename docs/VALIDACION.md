# Validación de la entrega

## Instalador Bash para servidor Linux · 2026-10-05

Creado `instalador-servidor/` y excluido por completo de Git. Las plantillas y
`scripts/preparar_instalador.sh` están versionados para reconstruir el paquete
después de clonar. El [README del instalador](../scripts/servidor/README.md)
explica requisitos, comandos, datos externos y límites.

El instalador prepara una instancia nueva: Docker/repositorio oficial en
Debian/Ubuntu, claves independientes, permisos de los UID de cada imagen, dominio
y HTTPS Caddy, SMTP TLS, observabilidad y journald, migración/arranque, systemd,
backup cifrado y ensayo inicial/mensual. No necesita Python/Node del host. Un
destino/proyecto/volumen/unidad existente provoca rechazo; un lock impide otra
instalación simultánea. No sobrescribe claves ni ejecuta `down -v`. Usa solo
archivos aptos para Git del checkout, sin copiar credenciales locales.

Validación:

- **13 pruebas nuevas correctas**: generación sin root/red/Docker; claves aceptadas
  por `validate_secrets` en modo production; configuración SMTP/TLS y contraseña
  fuera de YAML; permisos; backups internos sin socket Docker; origen/pública
  main-app reales; rechazo de privada/URL/puerto inválidos; limpieza del intento
  parcial; conservación de un destino existente y credenciales tratadas como
  datos, sin evaluar sustituciones de comandos. Compose valida el conjunto y
  confirma que solo Caddy publica puertos y cada consumidor usa la configuración
  generada. Las unidades incluyen arranque del stack y timers de operaciones.
- **198 unitarias correctas**, 101 integraciones excluidas. Ruff global, mypy en
  53 módulos, actionlint, sintaxis Bash y `git diff --check` correctos.
- Configuración generada aceptada por amtool 0.34.1. Unidades generadas aceptadas
  por `systemd-analyze verify`; solo se verificaron, no se instalaron en el host.
- **Dos integraciones reales correctas** con PostgreSQL/Redis temporales:
  backup/restauración y exclusión de tablas TTL/futuras, más el trabajo de
  backup/ensayo usando conexión directa y métricas, sin ejecutar Docker. Evidencia
  en `/tmp/chat-persistence-test.Pdafix` y
  `artifacts/n9/installer-backup-integration.log`; servicios detenidos al acabar.
- La imagen `backup` reutiliza runtime y clientes PostgreSQL fijados sin instalar
  dependencias de desarrollo. CI añade su build/escaneo y la sintaxis Bash;
  se conserva el target `test` y el modo operativo host anterior.
- Trivy no detecta secretos en los archivos preparados para Git. Comprobado que
  el índice excluye el paquete generado, credenciales, `.env`, backups y artefactos.

No se ejecutó el instalador completo ni se cambió este host. Necesita servidor
Linux/systemd, dominio/DNS, firewall del proveedor y SMTP reales; esas cuentas
externas no pueden inventarse. La comprobación SMTP del instalador no envía correo
y la comprobación HTTPS solo declara éxito tras validar el certificado local.
La imagen de backup, instalación de paquetes, permisos con contenedores,
certificado ACME, reinicio, correo/alertas y backups representativos quedan para
CI y staging. Los comandos no certifican N9 ni configuran custodia remota sin
un destino proporcionado.

## UI con API/WS y dependencias reales · 2026-10-05

Añadida una suite opt-in, independiente de las pruebas de UI interceptada, que
abre Chromium contra FastAPI y WebSocket reales con PostgreSQL/Redis vacíos.
Comandos, aislamiento y límites en [E2E_WEB.md](E2E_WEB.md).

Validación de este incremento:

- **6 recorridos de navegador correctos**, tres en escritorio y tres con viewport
  Pixel 7: stored (registro/verificación, claves, invitación, ACK, gracia conservada,
  reconexión, historial y purga); ephemeral (offer/ready, ACK sin ciphertext durable,
  abstención/rechazo de gracia y upgrade); dos contextos (bootstrap Ed25519 real,
  sesión anterior revocada, permiso residual, transferencia y borrado tras restaurar).
  No se intercepta REST ni WS. Se espera el plazo normativo de 30 s de votación y
  se mantienen las cuotas autenticadas; no hubo 429 en la ejecución final.
- El inspector comprueba estados persistidos, ciphertext/meta sin los marcadores
  de plaintext y una sola sesión normal. El secreto de transferencia no se envía
  a la API y el blob permanece hasta reabrir la copia descargada. Sin pageerror,
  traces, capturas, vídeos ni snapshots conservados.
- Test Python que coordina la suite: **1 correcto en 173,43 s**. Evidencia local en
  `artifacts/n9/web-e2e-local.log` y `/tmp/chat-persistence-test.FZrj6G`;
  PostgreSQL y Redis detenidos al terminar. Entorno local: Python 3.14.4,
  Node 22.22.1, Playwright 1.63.0, PostgreSQL 18.6 y Redis 8.0.5.
- Suite Python unitaria completa: **185 correctas, 100 de integración excluidas**.
  Seis nuevas comprueban aislamiento del runner: solo dependencias nuevas y
  volátiles, imágenes fijadas, montajes privados/BD y puertos loopback válidos.
- Ruff global, mypy en 53 módulos, actionlint, sintaxis de los dos módulos JS y
  `git diff --check` correctos. Compose valida la configuración generada.
  Añadidos `make test-web-e2e` y job CI `web_e2e`, sin nuevas dependencias o migraciones.

No se ha ejecutado aquí el runner Docker: el usuario no tiene acceso al socket
del daemon. Su ejecución con las imágenes fijadas (PostgreSQL 17.11 y Redis 8.6.6)
y el SHA publicado queda para CI; no se declara ese job verde. La comprobación
funcional local sí usa servicios reales aislados, mediante el runner Unix.

Mailbox sustituye el transporte SMTP y la sesión del emisor principal es una
fixture local. Se adelanta únicamente la caducidad de un mensaje sintético para
comprobar la purga y se vuelve a consultar el historial; el resto del ciclo es
real. Esta suite usa HTTP loopback sin lifespan de producción y entrada manual
de transferencia: Caddy TLS/arranque siguen en sus smokes separados. Main-app
real, SMTP/Push externos, cámara física, dispositivos finales, carga y staging
permanecen pendientes de N9.

## Adaptador de acceso desde main-app · 2026-10-05

Implementados emisor BootstrapIssuer Ed25519, launcher autónomo de main-app y
receptor web entre ventanas con origen/source/nonce/campos comprobados. Apertura
por gesto, token solo en memoria, consumo único sin reintento y formulario manual
ante fallos. Configuración `bootstrap_web_origins` separada de CORS y apagada por
defecto; producción rechaza HTTP. GET público entrega únicamente lista/version.
No se modifica otra aplicación ni se monta su privada en Chat API.

Validación de este incremento:

- Suite Node completa: 50 pruebas correctas, cinco nuevas del protocolo bootstrap.
  Incluyen origen/source incorrectos, nonce, campos/tamaños, timeout, duplicados,
  popups bloqueados y emisión que termina después de cancelar.
- Suite Chromium completa: 54 recorridos correctos en escritorio y viewport móvil.
  Doce nuevos usan dos orígenes locales y ventanas reales, con API/emisor
  interceptados: acceso y separación de opener, otra ventana del mismo origen,
  configuración tardía/cancelación, respuesta perdida, perfil caído y secretos
  fuera de URL/DOM/storage. No constituyen E2E con main-app/SMTP reales.
- Suite Python unitaria completa: 179 correctas, 99 de integración excluidas.
  Dieciséis nuevas validan firma interoperable, jti distintos, TTL/UUID/kid,
  errores sin secretos, origen canónico y rechazo de HTTP en producción.
- Dos integraciones reales correctas con PostgreSQL/Redis aislados: emisor de
  main-app, metadata pública/no-store, exchange, consumo único concurrente/jti y
  revocación del predecesor. Evidencias en `/tmp/chat-persistence-test.QWNCzx`;
  servicios detenidos al terminar.
- Ruff sobre Python/scripts/operations/interop, mypy en 53 módulos y
  `git diff --check` correctos. Sin dependencias o migraciones nuevas.

CI incluye los módulos/pruebas por sus rutas existentes; el smoke Caddy añade
bootstrap.js y verifica que fixtures/adaptador externo no se sirvan como assets.
Ese smoke HTTPS y las imágenes exactas quedan para CI. Pendiente conectar sesión,
CSRF, mapeo de UUID y origen de la aplicación principal real; también homologación
COOP/popups, proveedores/dispositivos/carga y staging. Contrato y pasos en
[INTEGRACION_MAIN_APP.md](INTEGRACION_MAIN_APP.md).

## Notificaciones web · 2026-10-05

Implementados panel de activación/baja voluntaria, permiso por gesto, service
worker local con texto fijo y enfoque sin recargar, limpieza local al cerrar
sesión y conciliación de alta/baja de respuesta perdida. Añadidos GET autenticados
de pública VAPID y suscripciones activas de la sesión, sin secretos de transporte.
No hay nueva migración ni dependencias. Los incrementos anteriores se conservan.

Validación:

- Suite Node completa: 45 pruebas correctas, incluidas 10 de Push, privacidad,
  formato, permiso denegado, cambio de VAPID, alta/baja inciertas y limpieza de
  operaciones que terminan después de abandonar la pantalla.
- Suite Chromium completa: 42 recorridos correctos en escritorio y viewport móvil.
  Ocho de Push registran/activan el worker real bajo CSP y simulan proveedor/REST:
  permiso voluntario/denegado, logout, POST/DELETE perdidos y conflicto de cuenta.
  No se crearon caches ni se guardaron credenciales en storage.
- Siete integraciones Push correctas con PostgreSQL/Redis aislados: propiedad,
  validación, configuración pública/no-store/auth, aislamiento de sesión/usuario,
  reintentos, 404/410, ofertas offline y exclusión del dispositivo sustituido.
  Receptor HTTPS local verifica VAPID y descifra el payload; no hay entregas externas.
  Evidencias en `/tmp/chat-persistence-test.iutE37`; servicios detenidos al terminar.
- Ruff sobre Python/scripts/operations/interop, mypy estricto en 52 módulos y
  `git diff --check` correctos.

El smoke Caddy HTTPS incorpora assets y cabeceras del worker para CI; no se afirma
haberlo ejecutado en este incremento. Proveedores SMTP/Push reales, cámaras/SO
finales, emisor/app principal, staging y carga siguen pendientes. Detalles en
[PUSH_WEB.md](PUSH_WEB.md) y [N9](N9_HOMOLOGACION_RELEASE.md).

## Transferencias de claves web · 2026-10-01

Añadidos acceso bootstrap manual, preparación del dispositivo anterior,
solicitud/QR privado, generación y lectura local de QR, cifrado XChaCha compatible
con Python, y recepción con copia .chatkey obligatoria antes del borrado remoto.
Los cambios de conversaciones descritos abajo se conservan. Sin migraciones nuevas.

Validación de este incremento:

- 35 pruebas Node correctas, incluida interoperabilidad de mensajes y transferencias
  con Python, UUID/AAD, adulteración, límites, cuenta/pública y QR duplicados.
- Suite completa Chromium: 34 recorridos correctos (17 escenarios escritorio/móvil).
  Ocho corresponden a transferencias: dos páginas con access distintos, QR por
  imagen, archivo descargado/abierto, subida/borrado de respuesta perdida, cámara
  denegada, cancelación y permisos de cámara tardíos tras pagehide.
- 15 unitarias del protocolo Python de recuperación correctas.
- Tres integraciones reales de transferencias: permisos del dispositivo sustituido,
  aislamiento/carga única/TTL/DELETE y revocación por nuevo acceso/caducidad.
  PostgreSQL/Redis aislados en `/tmp/chat-persistence-test.yo3mMx`, detenidos al terminar.
- Ruff, bundle sodium/QR contra lock y diff correctos. La instalación npm auditó
  38 paquetes sin vulnerabilidades detectadas. No se repiten auditorías de imágenes.

Las páginas de navegador interceptan REST/WS con datos sintéticos. La lectura de
imágenes QR es real; cámara física, emisor bootstrap, SMTP/Push y E2E integrado
siguen pendientes de homologación. No se enviaron datos a servicios de QR.
El bundle QR, sus licencias y las rutas de assets entran en CI; el smoke Caddy
HTTPS queda para esa ejecución. Alcance en [TRANSFERENCIAS_WEB.md](TRANSFERENCIAS_WEB.md).

## Conversaciones y mensajería web · 2026-10-01

Se completa y revisa el incremento iniciado en `340c73c`: invitaciones, listado,
canje, aceptación, envío cifrado stored/ephemeral, estados y reintentos, reconexión,
historial, voto de gracia, cierre/salida/upgrade y replay local inicial.
Se corrigen paginación reiniciada por polling, resultados atrasados, salida dudosa,
reintroducción de gracia y limpieza tras bloqueo/revocación. El límite de 500
mensajes consultados se muestra en la UI. No requiere nuevas migraciones.

Validación local:

- 29 pruebas Node de sesiones, claves y transporte, con interop Python real.
- 26 recorridos Chromium: 13 escenarios en escritorio y móvil; REST/WebSocket
  interceptados con datos sintéticos. Incluyen cifrado, ACK, corrupción, corte y
  ticket nuevo, mismo payload al reintentar, permisos, votación y limpieza de textos.
- 27 integraciones seleccionadas de recursos/votaciones con PostgreSQL 18.6 y
  Redis locales aislados por sockets Unix: anonimato pending, públicas por
  conversación, pertenencia, voto personalizado, resolución y revocación.
  Evidencia: `/tmp/chat-persistence-test.j6xhMH`; servicios detenidos al terminar.
- 163 unitarias Python correctas; 98 integraciones excluidas de esa ejecución.
  Python local 3.14.4; no sustituye el ensayo de imágenes fijadas en CI.
- Ruff, mypy estricto (52 módulos), comparación del bundle criptográfico con
  el lock y `git diff --check` correctos. Smoke Caddy ampliado con chat.js/chat-ui.js;
  su ejecución HTTPS de esta entrega queda para CI.

Las restricciones iniciales del sandbox bloquearon sockets y procesos auxiliares;
los ensayos se completaron fuera de esas restricciones. No se enviaron correos,
Push ni mensajes a terceros, y no se tocaron bases existentes.
La cobertura combinada, auditorías y CI del commit final no se han repetido aquí.
Transferencia QR, Push web, E2E UI/backend con proveedores reales y capacidad en
staging siguen pendientes. Ver [CLIENTE_WEB.md](CLIENTE_WEB.md).

## Claves del cliente web · 2026-10-01

Base `9ae0d6c`: [CI completa verde](https://github.com/UnInformaticoAburrido/ChatTemporal/actions/runs/36598209657),
279 pruebas entre backend y web; cobertura Python 91,21 %.

Se añade generación X25519 local, copia cifrada portable, apertura obligatoria
antes de publicar y comprobación de coincidencia con la pública autoritativa.
Publicación inicial con If-None-Match: * bajo lock de usuario; existente=412.
El cliente no sustituye ni rota claves existentes.

Validación local: 17 pruebas Node de sesiones/criptografía, 18 recorridos Chromium
en escritorio/móvil y 5 integraciones de recursos con PostgreSQL/Redis reales.
La carrera entre cuatro publicaciones produce una creación y tres rechazos,
sin modificar la ganadora; evidencia `/tmp/chat-persistence-test.LqjAdv`.
Interop crypto_box con Python en ambos sentidos, contraseñas erróneas, alteración
de cabecera/ciphertext, formatos abusivos y bloqueo durante derivación verificados.
UI con API interceptada: descarga/restauración reales, conflicto y respuesta perdida;
no equivale a E2E completo con SMTP. Npm audit, Ruff, mypy y actionlint correctos;
CI añade interop y comparación exacta del bundle con el lock.
Alcance y siguientes tareas en [CLAVES_CLIENTE.md](CLAVES_CLIENTE.md).

## Cliente web de identidad · 2026-09-29

El backend de partida `2120e96` tiene [CI completa en verde](https://github.com/UnInformaticoAburrido/ChatTemporal/actions/runs/36427638875):
163 unitarias, 95 integraciones y 91,21 % de cobertura global.

Primer cliente web servido por Caddy: registro, presentación y confirmación de
frase, verificación/reenvío, recuperación y cierre. Tokens solo en memoria,
refresh único bajo concurrencia y sin reintentar resultados dudosos. No genera
ni reemplaza claves de cifrado. Sesión perdida al recargar/cerrar, indicado en UI.

Validación local: **11 pruebas de sesión/contratos y 10 recorridos de navegador**
correctos (cinco escenarios en escritorio y móvil). Chromium con API interceptada
y datos sintéticos; no equivalen a SMTP real ni E2E del chat completo. Capturas
de la pantalla inicial inspeccionadas localmente, sin datos de cuentas.
`npm audit` sin vulnerabilidades detectadas; Ruff, actionlint y Compose correctos.
CI añade job web con versiones fijadas y amplía el smoke Caddy para exigir assets,
CSP, no-store y rutas no públicas inaccesibles. Los siguientes bloques se detallan
en [CLIENTE_WEB.md](CLIENTE_WEB.md); N9 sigue en curso.

## Carga con desconexión efímera · 2026-09-28

El commit anterior `e2bdebb` tiene [CI completa en verde](https://github.com/UnInformaticoAburrido/ChatTemporal/actions/runs/36410291565):
157 pruebas unitarias y 93 de integración; cobertura global 91,18 % y auditorías
de dependencias, secretos e imágenes correctas.

Se añade `ephemeral-disconnect`: cierre del receptor tras descifrar y antes del
ACK, fallo RECIPIENT_DISCONNECTED contrastado con REST, reconexión y rechazo del
reenvío del mismo ID. Se exige después una entrega con UUID nuevo, incluso si la
pausa superó la duración. Los fallos provocados se cuentan aparte de confirmaciones.

Validación local: **16 pruebas de la herramienta y 6 de integración correctas**.
PostgreSQL confirma ausencia de contenido persistido para entregas y fallos;
Redis conserva los metadatos terminales sin payload. Se rechazan corrupción,
códigos de fallo distintos, confirmaciones inesperadas y replay de payload tras
reconectar. Evidencia de integración: `/tmp/chat-persistence-test.B4o3yp`.
Ruff y diff correctos. CI repite los escenarios con las imágenes fijadas.
Son cierres ordenados con dos sockets; staging, carga representativa, cortes
abruptos e interfaz siguen pendientes para completar N9.

## Carga con reconexiones · 2026-09-28

El commit anterior `d1f8bfd`, incluidas las protecciones de aislamiento del ensayo
de arranque, tiene [CI completa en verde](https://github.com/UnInformaticoAburrido/ChatTemporal/actions/runs/36341982999).

Se añade el perfil `stored-reconnect` al generador de carga: receptor offline,
confirmación REST de persistencia pending, reconexión con tickets nuevos,
reenvíos del mismo ID/payload antes y después del ACK y recuperación/descifrado
del historial. Comprueba un único registro por mensaje y correlaciona el recibo
del último reenvío por request_id. Los recibos repetidos no suman entregas.

Validación local: **10 pruebas de la herramienta y 4 de integración correctas**,
con PostgreSQL/Redis temporales y sockets reales. Incluye los perfiles online
stored/ephemeral, reconexión stored y rechazo de contenido corrupto antes del ACK
(la entrega permanece pending). No se ocultan 429/503 ni IDs desconocidos.
Evidencia: `/tmp/chat-persistence-test.4tFFlV`; Ruff/actionlint/diff correctos.
CI repite estos escenarios con las imágenes fijadas. La prueba local no acredita
capacidad: pico, hardware, duración sostenida y staging siguen pendientes.

## Puertas de arranque · 2026-09-27

Se añade `test_startup_gates_integration.py`: una migración crea una tabla y luego
falla con un error SQL; la tabla se revierte y la versión Alembic permanece en
`0007_recovery_push`. Los procesos reales de bootstrap de API/worker terminan
con exit 1 ante esa revisión pendiente y no imprimen el dato del error.
**La nueva prueba pasa localmente** con PostgreSQL/Redis temporales, en cinco
segundos; evidencia: `/tmp/chat-persistence-test.SEWi6e`. Ruff, actionlint,
configuración Compose combinada y `git diff --check` correctos.
Otras nueve pruebas unitarias verifican que el ensayo solo acepte las direcciones
internas de Compose y rechace DSN externos, puertos distintos y host sobrescrito
por parámetros, antes de ejecutar una migración.

CI añade el ensayo `scripts/test_startup_containers.py` con las imágenes fijadas,
en un proyecto aleatorio aislado: bloqueo de dependencias por migración fallida,
arranque directo que rechaza el esquema pendiente, espera sin puertos abiertos
y recuperación sin otro reinicio al volver PostgreSQL/Redis. Conserva únicamente
el resumen `startup-gates.json` y elimina sus volúmenes temporales. El reinicio
completo del daemon o del host se reserva para staging. Comprobar el resultado
de este paso en el SHA publicado antes de homologar la entrega.

**Ensayo de contenedores superado** en `afa5dd5`:
[CI completa en verde](https://github.com/UnInformaticoAburrido/ChatTemporal/actions/runs/36341591729).
El informe acredita rollback, bloqueo por migración fallida, rechazo del esquema
pendiente con arranque directo y recuperación de los mismos procesos al volver
las dependencias. El job de seguridad también pasa.

## Continuación N9 · verificada 2026-09-27

CI de GitHub completa en verde para `1e1478aba35edacfd59c33b03b106f115a575a43`:
[ejecución y artifacts](https://github.com/UnInformaticoAburrido/ChatTemporal/actions/runs/36142194455).
Python 3.13.15, PostgreSQL 17.11 y Redis 8.6.6 en las imágenes fijadas del repositorio.
**136 unitarias y 90 pruebas de integración**; Ruff, mypy, actionlint y reglas
Prometheus correctos. Alertmanager 0.34.1 entrega y resuelve alertas HTTP locales.

| Ámbito | Líneas cubiertas/totales | Cobertura | Mínimo |
|---|---:|---:|---:|
| Global | 3174/3481 | 91,18 % | 85 % |
| Auth | 542/569 | 95,25 % | 90 % |
| Invitations | 302/308 | 98,05 % | 90 % |
| Delivery | 813/880 | 92,39 % | 90 % |
| Voting | 143/150 | 95,33 % | 90 % |

La cobertura se combina entre unitarias e integración; los ensayos externos
contra API/worker no aumentan ese numerador. Las diferencias frente al informe
local anterior corresponden a los intérpretes usados (3.13 en CI, 3.14 local).

Evidencia adicional en los artifacts de esa ejecución:

- `lifecycle.json`: parada y arranque reales de PostgreSQL/Redis; cambia el
  identificador de ambos procesos, PostgreSQL conserva el dato sintético y Redis
  pierde una clave sin TTL. Liveness sigue en 200, readiness pasa a 503 durante
  la caída y API/worker se recuperan sin reiniciarlos.
- `proxy-smoke.json`: TLS con CA interna verificada, redirección HTTP, rutas
  privadas ocultas, CORS, invitaciones/aceptación y ventana de voto real de 30 s.
  Mensajería cifrada WSS en stored/ephemeral: cuatro sockets, siete mensajes
  enviados/confirmados y dos historiales verificados. Es un ensayo funcional;
  no demuestra capacidad para el pico de producción.
- Nueve imágenes, incluida observabilidad: **cero vulnerabilidades críticas
  detectadas**. Pip-audit y escaneos de secretos del código/imágenes correctos.
  Se recompilan gosu y blackbox con Go corregido; no se excluyen CVE.

No se ha desplegado ni modificado ningún volumen del usuario. La migración de
PostgreSQL Debian a Alpine requiere el procedimiento y revisión de collations
indicados en [operación](../operations/README.md#5-despliegue-red-y-apagado).
Siguen pendientes interfaz cliente, SMTP/Push reales, staging, configuración del
host (journald/firewall/NTP), capacidad ≥2× del pico definido por el operador y
recuperación con volumen representativo. N9 permanece **en curso**.

Validación posterior del filtro de logs: CI completa en verde para `21c1358`
([ejecución](https://github.com/UnInformaticoAburrido/ChatTemporal/actions/runs/36339011782)).
**143 unitarias y 90 de integración**. El Compose de pruebas usa los mismos
wrappers/volúmenes de logs que producción, con comprobación contra deriva de
configuración. Se fuerza un error SQL con contenido sintético y se verifica que
los logs de ambas dependencias solo tengan timestamp, level, service y event_type.
`dependency-logs-check.json` publica únicamente resultado y conteos. Los logs
originales no se suben. Las ocho pruebas locales del verificador incluyen rechazo
de claves JSON duplicadas y diagnóstico de fallos sin repetir datos de entrada.

## Continuación N9 · 2026-09-24

Se mantiene la suite N8 (116 unitarias + 88 de integración) y se añaden catorce
pruebas unitarias de puertas de cobertura, arranque y fuzzing: **130 unitarias y
88 de integración**. Se repite el flujo WS real modificado para verificar logs
sin tokens/ciphertext/crypto_meta/texto/privada. Servicios locales: los mismos
Python 3.14.4/PostgreSQL 18.6/Redis 8.0.5 documentados en N8.
Evidencia de integración completa: `/tmp/chat-persistence-test.Th18S3`; flujo WS
con comprobación de logs: `/tmp/chat-persistence-test.dsGGRs`.

Coverage.py 7.10.7 mide todos los módulos `chat`/`chat_client`, sin omisiones:

| Ámbito | Líneas cubiertas/totales | Cobertura | Mínimo |
|---|---:|---:|---:|
| Global | 2973/3280 | 90,64 % | 85 % |
| Auth | 510/537 | 94,97 % | 90 % |
| Invitations | 278/284 | 97,89 % | 90 % |
| Delivery | 745/812 | 91,75 % | 90 % |
| Voting | 142/149 | 95,30 % | 90 % |

Ruff, mypy (52 módulos), actionlint 1.7.12, sintaxis shell y Compose de tests
correctos. Trivy 0.74.0 no detecta secretos en el árbol de código local (excluidos
`.git`, `.venv`, `secrets` y `artifacts`, que no forman parte del código a publicar).
La auditoría inicial reportó doce entradas de seis avisos únicos para pip 25.1.1;
tras fijar pip 26.2.0, pip-audit no detecta vulnerabilidades conocidas en los locks.
Informes locales: `artifacts/n9/coverage.json`, `pip-audit.json` y `secrets-local.json`.

Se corrige la infraestructura de tests: `requirements-client.lock` entra en el
contexto Docker; el repositorio se monta read-only en `/workspace` para fixtures;
los informes tienen montaje separado escribible; el worker arranca después de
las pruebas para no competir con migraciones/expiraciones forzadas. El runner
siempre ejecuta down del proyecto `chat-tests`, conservando sus volúmenes.

Para reproducir cobertura local, desde `python/`: `python -m coverage run -m
pytest -q -m 'not integration'`. Después ejecutar el runner local documentado en
N8 con `CHAT_TEST_COVERAGE=1` para añadir integración, y desde `python/`:

```sh
../.venv/bin/python -m coverage json -o ../artifacts/n9/coverage.json
../.venv/bin/python ../scripts/check_coverage.py ../artifacts/n9/coverage.json
```

CI usa Python 3.13.12 y las imágenes fijadas de Compose. Su resultado remoto y
el escaneo de imágenes deben comprobarse en el SHA publicado; la validación
local no los sustituye. No hay acceso local al daemon Docker. Staging, SMTP/Push,
HTTPS/WSS, journald, firewall, carga y UI E2E siguen pendientes; ver
[N9_HOMOLOGACION_RELEASE.md](N9_HOMOLOGACION_RELEASE.md).

## Continuación N8 · 2026-09-24

Resultado: **116 pruebas unitarias y 88 de integración correctas (204 total)**.
La suite completa inicial pasó con 115 unitarias/88 de integración; después de
corregir el estado de salida del wrapper se añadieron y ejecutaron sus pruebas
de regresión, con las ocho pruebas de operación correctas. Ruff correcto con
`python/pyproject.toml`, incluidos los scripts nuevos; mypy estricto sin errores
en 52 módulos. Persisten las advertencias de deprecación documentadas en N7.

Se reutilizaron `.venv` y `artifacts/n7/runtime`: Python 3.14.4, PostgreSQL 18.6
y Redis 8.0.5, datos sintéticos en sockets privados y WS loopback. Evidencia:
`/tmp/chat-persistence-test.ymnDRv`; los servicios se detuvieron al terminar.
El sandbox bloquea sockets locales, por lo que las ejecuciones válidas de pytest
y Alertmanager se hicieron fuera de él. No se usaron las bases del proyecto.

Comprobaciones nuevas:

- Backup cifrado por streaming, retención 7 días/4 semanas y modo 0600; un fallo
  no publica archivos parciales ni elimina copias previas.
- Restauración real de esquema/datos durables y Alembic, con tablas TTL actuales
  y futuras vacías. Copia alterada rechazada antes de SQL; destino ocupado rechazado.
- Métricas de dependencias con servicios reales y etiquetas sin path/query secretos.
- SIGTERM real en el handler: readiness y nuevos tickets 503; stored ACK y
  ephemeral ready/send/ACK en vuelo completan y los sockets cierran con 1001.
- Logs de dependencias descartan el texto completo, conservan exit 7 ante fallo
  y exit 0 tras un cierre limpio por TERM. Se corrigió el estado del wait interrumpido.
- Compose local, producción/observability y tests válidos estructuralmente;
  override journald sin opciones de rotación por tamaño, sin puertos internos publicados.
- `promtool` 3.5.0: pruebas de reglas correctas; `amtool` 0.28.1: configuración
  aceptada; Alertmanager 0.28.1 real entrega firing y resolved a HTTP loopback.
- Servicios systemd corregidos a `TimeoutStartSec=2h`: RuntimeMaxSec no limita
  servicios oneshot. La instalación y ejecución en el host siguen pendientes.

Reproducción con herramientas locales ya preparadas:

```bash
cd python
../.venv/bin/python -m pytest -q -m 'not integration'
../.venv/bin/ruff check .
../.venv/bin/mypy chat chat_client
cd ..
.venv/bin/ruff check --config python/pyproject.toml operations scripts/test_alert_delivery.py
LD_LIBRARY_PATH="$PWD/artifacts/n7/runtime/usr/lib/x86_64-linux-gnu" \
CHAT_TEST_PG_BIN="$PWD/artifacts/n7/runtime/usr/lib/postgresql/18/bin" \
CHAT_TEST_REDIS_SERVER="$PWD/artifacts/n7/runtime/usr/bin/redis-server" \
CHAT_TEST_PYTHON="$PWD/.venv/bin/python" sh scripts/test_local_services.sh
artifacts/n8/tools/prometheus-3.5.0.linux-amd64/promtool test rules prometheus/alerts.test.yml
artifacts/n8/tools/alertmanager-0.28.1.linux-amd64/amtool check-config operations/alertmanager.yml
.venv/bin/python scripts/test_alert_delivery.py artifacts/n8/tools/alertmanager-0.28.1.linux-amd64/alertmanager
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile observability config --quiet
git diff --check
```

Límites: Docker deniega acceso al socket del daemon, también fuera del sandbox.
No se construyó la imagen de tests actualizada con cliente PostgreSQL 17 ni se
ejecutaron las imágenes exactas de Compose. No se instalaron unidades del host,
se cambió el firewall ni se enviaron correos externos. Faltan comprobaciones reales
de SMTP, journald, NTP/TLS, recuperación a escala, auditorías de imágenes/dependencias,
CI/cobertura, carga y staging N9. La interfaz cliente permanece pendiente.

## Continuación N7 · 2026-09-23

Resultado final: **108 pruebas unitarias y 84 de integración correctas (192 total)**.
Ruff y `git diff --check` correctos; mypy estricto sin errores en 50 módulos.
No hay nuevas dependencias. Persisten las advertencias de deprecación de
Starlette/httpx/AnyIO y Uvicorn/websockets ya registradas; no se ocultan.

Servicios reales aislados: Python 3.14.4, PostgreSQL 18.6 y Redis 8.0.5. El entorno
de `/tmp` desapareció durante la continuación; se reconstruyó en `.venv` y
`artifacts/n7/runtime` con los locks existentes y paquetes extraídos, sin instalar
servicios del sistema. PostgreSQL/Redis usan sockets Unix privados, WS usa puertos
loopback y Push un servidor HTTPS local con certificado verificado. Los servicios
se detienen al terminar. Evidencia del último runner: `/tmp/chat-persistence-test.Xs63k7`.
Resúmenes de pruebas conservados en `artifacts/n7/unit.log` e `integration.log`,
ignorados por Git. Evidencia por apartado en [N7_RECUPERACION_PUSH.md](N7_RECUPERACION_PUSH.md).

Comprobaciones nuevas:

- Transferencia entre dispositivos usando bootstrap/exchange real, una sola sesión
  normal y permiso residual del anterior limitado a PUT para su sucesor. Se prueban
  bloqueo de REST/WS/POST, caducidad, otro login, DELETE y logout del destino.
- Blob máximo de 65536 bytes, 413 por exceso, carga concurrente única, campos
  estrictos, propietario/sid, GET repetido, TTL no renovado y eliminación inmediata.
  El secreto QR no forma parte del PUT ni del repr; ciphertext alterado y UUID/QR
  incompatibles no se descifran.
- Cliente que recifra historial local para una clave nueva, preservando orden y
  metadatos opcionales. Frames compatibles con el parser; descifrado real en cliente.
- Replay en dos instancias ASGI: begin/item/end, sequence e item_count exactos,
  finalización, conexiones vinculadas, receptor offline, rechazo de terceros y
  límite por replay. No se crean eventos/mensajes/deliveries normales ni jobs Push;
  Redis conserva solo metadata del replay con TTL.
- Suscripciones Push: upsert propio, conflicto de endpoint ajeno, revocación,
  claves inválidas y destinos locales/privados rechazados. DNS mixto público/privado
  falla cerrado. No se notifica a una suscripción de sesión sustituida.
- Stored offline genera cola al aceptar el mensaje; retry no duplica jobs. Recibos
  por suscripción evitan repetir éxitos al reintentar fallos. 404/410 revocan.
  Ephemeral crea Push solo para offers offline; no se crea message_event por offer.
- Vector conocido RFC 8291 exacto y petición HTTPS real a receptor local: TLS/SNI,
  VAPID ES256, payload genérico cifrado y descifrado independiente con HMAC/AES-GCM.
  Las IP públicas se enrutan a loopback exclusivamente dentro del test; producción
  mantiene la validación de destinos y certificados.
- Migraciones desde cero y desde cada revisión 0001–0006 hasta 0007_recovery_push.
  Se conserva la suite previa de identidad, mensajería, votaciones y retención.

Reproducción desde la raíz, con el entorno local preparado:

```bash
cd python
../.venv/bin/python -m pytest -q -m 'not integration' -p no:cacheprovider
../.venv/bin/ruff check .
../.venv/bin/mypy chat chat_client
cd ..
LD_LIBRARY_PATH="$PWD/artifacts/n7/runtime/usr/lib/x86_64-linux-gnu" \
CHAT_TEST_PG_BIN="$PWD/artifacts/n7/runtime/usr/lib/postgresql/18/bin" \
CHAT_TEST_REDIS_SERVER="$PWD/artifacts/n7/runtime/usr/bin/redis-server" \
CHAT_TEST_PYTHON="$PWD/.venv/bin/python" sh scripts/test_local_services.sh
git diff --check
```

Límites: no se dispone de suscripción de navegador/proveedor Push real ni de
proveedor SMTP real para homologación. Los tests HTTPS prueban protocolo y cifrado,
no recepción en un navegador de producción. UI final, versiones Compose exactas,
HTTPS/WSS de despliegue, staging y carga siguen pendientes. No se repite auditoría
de dependencias ni se certifica producción. N8/N9 son los siguientes niveles.

## Continuación N6 · 2026-09-23

Resultado: **93 pruebas unitarias y 71 de integración correctas (164 total)**.
Ruff de Python y de la migración nueva, `git diff --check` y mypy estricto de
42 módulos correctos. No hay nuevas dependencias. Se mantienen los avisos de
deprecación ya existentes de Starlette/httpx/AnyIO y Uvicorn/websockets.

El entorno temporal de N5 desapareció al reiniciar; se reconstruyó con los locks
del repositorio y paquetes Ubuntu extraídos en `/tmp`, sin instalar servicios en
el sistema. Python 3.14.4, PostgreSQL 18.6 y Redis 8.0.5. PostgreSQL/Redis usan
sockets Unix privados; las pruebas WebSocket usan puertos loopback temporales.
La suite arranca también el worker real como subproceso y lo detiene al finalizar.
Evidencia final: `/tmp/chat-persistence-test.g1gFTp` (temporal, no se versiona).

Comprobaciones N6, documentadas por apartado en [N6_VOTACIONES.md](N6_VOTACIONES.md):

- REST de votos, DTO estricto, sesión revocada, ocultación de votos ajenos y
  pérdida de acceso tras leave/borrado. Un primer ballot tardío devuelve el
  **410 VOTE_EXPIRED** normativo; cambiar uno existente devuelve 409.
- Ocho ballots simultáneos con elecciones opuestas producen una sola elección.
  Una petición que espera un lock comprueba el plazo al adquirirlo; no se usa
  el inicio de transacción como reloj. Reintentos compatibles funcionan tras cierre.
- Mayoría absoluta y abstención=NO con censos sintéticos 2/3/5/10; no se habilitan
  grupos en la API. No se cierra antes del plazo aunque todos voten a favor.
- Tres resolutores concurrentes cierran una sola vez. Rollback revierte juntos
  el resultado y la eliminación de gracia. Un proceso worker cierra sin lecturas REST.
- Gracia stored aprobada conservada; rechazada oculta desde deadline y eliminada
  físicamente al resolver. Permanecen mensajes posteriores, eventos, entregas,
  fingerprints y contador; reintentar no resucita contenido eliminado.
- Ballots conservados al abandonar/eliminar el elector; censo y resultado no
  cambian. La nueva FK rechaza un ballot de alguien ajeno al censo.
- WS entre dos instancias: gracia real stored/ephemeral, accept, ballots,
  `vote.opened/updated` con `my_vote` propio, bloqueo send/offer y envío tras cierre.
  Upgrade no convierte el contenido efímero previo en stored.
- Fallo Pub/Sub inyectado: el ballot mantiene commit aunque REST devuelve 503;
  la resolución de otros votos continúa y GET permite recuperar el resultado.
- Migraciones desde cero y desde 0001/0002/0003/0004/**0005** hasta 0006.

Comandos usados (rutas temporales sustituibles por binarios equivalentes):

```bash
cd python
/tmp/chat-n6-venv/bin/python -m pytest -q -m 'not integration' -p no:cacheprovider
/tmp/chat-n6-venv/bin/ruff check . ../BD/postgresql/migrations/versions/0006_ballot_electorate.py
/tmp/chat-n6-venv/bin/mypy --cache-dir /tmp/chat-n6-mypy chat chat_client
cd ..
LD_LIBRARY_PATH=/tmp/chat-n6-runtime/usr/lib/x86_64-linux-gnu \
CHAT_TEST_PG_BIN=/tmp/chat-n6-runtime/usr/lib/postgresql/18/bin \
CHAT_TEST_REDIS_SERVER=/tmp/chat-n6-runtime/usr/bin/redis-server \
CHAT_TEST_PYTHON=/tmp/chat-n6-venv/bin/python sh scripts/test_local_services.sh
git diff --check
```

Las pruebas temporales requieren permiso para sockets locales. La primera pasada
detectó expectativas de migración antiguas y dos fixtures de prueba incorrectos;
se corrigieron antes de la ejecución final. El caso de locks limpia el snapshot
de estadísticas de PostgreSQL al observar la espera para no consultar una vista
congelada dentro de la transacción. La comparación con §23 corrigió el HTTP de
voto vencido a 410. No se alteraron los requisitos para hacer pasar pruebas.

Límites: Docker sigue inaccesible, sin homologación de versiones Compose/HTTPS/WSS,
staging, carga ni proveedores SMTP/Push. Pub/Sub no es durable ni ordena commits
concurrentes; los clientes recuperan estado por GET. La aplicación local del
resultado ephemeral corresponde a la interfaz cliente pendiente. N7–N9 continúan
pendientes; no se repite auditoría de dependencias ni se certifica producción.

## Continuación N5 · 2026-09-22

Resultado final: **89 pruebas unitarias y 50 de integración correctas (139 total)**.
Ruff y `git diff --check` correctos; mypy estricto sin errores en 39 módulos.
No hay nuevas dependencias. Persisten avisos de deprecación de Starlette/httpx/AnyIO
y del backend Uvicorn/websockets; no se presentan como fallos ni se ocultan.

El entorno temporal anterior desapareció tras reiniciar. Se reconstruyó con los
locks existentes y binarios oficiales extraídos en `/tmp`, sin instalar servicios
del sistema. Python 3.14.4, PostgreSQL 18.6 y Redis 8.0.5. PostgreSQL/Redis usan
sockets Unix privados; las pruebas WebSocket levantan Uvicorn en puertos loopback
asignados por el sistema. Evidencia final: `/tmp/chat-persistence-test.2WyFw7`.
Todos los servicios temporales se detienen al terminar.

Comprobaciones nuevas:

- Tickets concurrentes de un solo uso, expiración, Origin, sesión y hash compatible
  con la emisión N2. Se corrigió el hash del handshake inicial: debe usar bytes
  del token, no su representación Base64.
- Sockets reales en dos instancias ASGI, envío stored, cifrado/descifrado del cliente
  de referencia, estado REST, historial, ACK repetido y fingerprint incompatible.
  Las fechas de envío, ACK, cierre y upgrade se verifican en UTC incluso cuando
  PostgreSQL devuelve su zona local.
- Offer no crea evento ni contiene ciphertext; ready vinculado a conexión;
  entrega ephemeral, ACK desde conexión ajena rechazado, ACK tardío, desconexión
  y borrado del payload. PostgreSQL no recibe el contenido efímero.
- ACK de una entrega previa sigue funcionando tras accept/upgrade/close; una
  oferta pendiente cancelada no se convierte en stored por un send tardío.
- Stored offline y reintento tras reconexión; receptor que conecta recupera offers
  vigentes. No se atribuye esta prueba a un proveedor Push.
- Pérdida total de Redis mediante FLUSHDB exclusivamente en la instancia sintética,
  cierre de sockets, nueva autenticación y reconciliación de entregas huérfanas.
- Revocación publicada y revocación sin aviso Pub/Sub; draining cierra 1001 y
  rechaza tickets nuevos en la misma instancia.
- Frames binarios 1003, máximo 1009, cuota persistente 4429, rechazo de accesos
  horizontales y bloqueo por voto/cierre. Offer+send consume una sola cuota lógica.
- Heartbeat nativo con cliente que recibe ping y deliberadamente no responde:
  el servidor cierra al vencer el timeout, sin mensajes JSON de heartbeat.
- Migraciones desde cero y desde 0001/0002/0003/**0004** hasta 0005_delivery_mode.

La repetición final detectó una prueba N2 dependiente del cambio de segundo:
calculaba exp y el helper calculaba iat en instantes distintos, convirtiendo a
veces el TTL inválido de 301 s en uno válido de 300 s. Se fijan ambos extremos
en la prueba; la validación de producción no cambia. La suite unitaria posterior pasa.

Límites: no hay prueba de Docker/HTTPS/WSS/staging ni carga o caída forzada de un
proceso de producción. El estado huérfano se reproduce de forma controlada y
Redis perdido se prueba con datos temporales. Pub/Sub no es una cola durable;
el cliente debe consultar estado/historial tras cortes. Ballots/resolución N6,
transferencia/Push N7, interfaz cliente y operación/homologación N8–N9 pendientes.
No se ha repetido una auditoría de dependencias ni se certifica producción.

## Continuación N4 · 2026-09-21

Resultado: **79 pruebas unitarias y 37 de integración correctas (116 total)**.
Ruff sin errores y mypy estricto sin errores en 33 módulos de servidor/cliente.
Se mantienen los dos avisos de deprecación del cliente Starlette/httpx/AnyIO.
No hay dependencias nuevas. No se repite la auditoría histórica de dependencias
ni se presenta como una comprobación de vulnerabilidades actual.

Servicios temporales PostgreSQL 18.6 y Redis 8.0.5, Python 3.14.4; sin puertos TCP
ni acceso a los datos del proyecto. Evidencia: `/tmp/chat-persistence-test.JVljSA`.
El runner aplica migraciones y detiene los servicios al terminar. Se comprueba:

- Layout exacto de 30 bytes + HMAC de 32, uint32 máximo, 83 caracteres,
  MAC alterado en cada byte, truncamientos, versión/modo inválidos y fuzz determinista.
- Creación concurrente de una única identidad, códigos estables, regeneraciones
  serializadas, revocación de ambas variantes y rollback al agotar generation.
- Canje frente a regeneración y dos usuarios canjeándose mutuamente, sin deadlocks.
- Autenticación, verificación, cuotas por usuario/IP, autocanje, pública inexistente,
  sesión revocada, campos extra y códigos no utilizables.
- Flujo REST real hasta pending/accept/upgrade/close/leave, sin fixtures de conversación;
  aislamiento de terceros y peer=null incluso tras cerrar sin aceptar.
- Accept concurrente crea un solo voto; el plazo es exactamente 30 s y leave no
  modifica el censo. Borrar al invitado antes de aceptar impide una aceptación parcial.
- Upgrade/send y close/send se serializan. El contenido previo efímero no aparece
  en messages tras upgrade; un reintento de mensaje persistido tras close no duplica.
- Migración desde cero y desde 0001, 0002 y **0003** hasta 0004_vote_electorate,
  conservando los hashes y datos anteriores.

La primera ejecución detectó un deadlock real entre el lock de usuario de upgrade
y la FK recipient_user_id de una entrega. Se corrigió con FOR NO KEY UPDATE en
el usuario para las transiciones, manteniendo FOR UPDATE en la conversación;
la suite completa posterior pasa. No se oculta el fallo inicial como éxito.

Límites: se adelanta solo la apertura del voto y su censo. Ballots, resolución y
notificaciones siguen pendientes de N6; el estado open puede persistir tras el plazo.
No hay servidor WS, eventos/cancelación de offers ni interfaz cliente. Siguen
pendientes SMTP real, versiones exactas de Compose, HTTPS/WSS, cobertura/CI,
staging y pruebas de operación. N4 no certifica producción.

## Continuación N2 · 2026-09-17

Resultado: **50 pruebas unitarias y 24 de integración correctas (74 total)**.
Ruff sin errores, mypy estricto sin errores en 19 módulos, compilación Python y
`git diff --check` correctos. `pip check` no detecta dependencias incompatibles y
`pip-audit -r python/requirements.lock --no-deps --disable-pip` no encuentra
vulnerabilidades conocidas en esta consulta. Continúan dos avisos de deprecación
del cliente de pruebas Starlette/httpx/AnyIO, sin fallos.

PostgreSQL 18.6 y Redis 8.0.5 se ejecutaron temporalmente con el runner local,
sin puertos TCP, como en N1. Se recrearon los binarios/entorno de `/tmp` tras el
reinicio del equipo. Evidencia final local: `/tmp/chat-persistence-test.ZTvk0j`.
Los procesos se detienen al terminar. Se probaron:

- BIP-39 inglés de 24 palabras, entropía de 256 bits, NFKD y parámetros exactos
  Argon2id con salts distintos; hashes excluidos de respuestas y frase solo en registro.
- JWT EdDSA, claims, tiempos, issuer/audience, sustitución de algoritmo, kid
  desconocido/ruta inválida y retirada inmediata de una pública.
- Registro, perfil propio, email de un uso, reenvío, vencimiento, cambio de
  email y borrado de cuenta; restricciones case-insensitive de nick/email.
- Refresh concurrente: un ganador, detección de reuse del otro y revocación
  durable del reemplazo, comprobada también con otra instancia del servicio.
- Recuperación y bootstrap concurrente de un uso; sustitución de sesión, logout,
  sesión expirada y publicación real de su revocación por Redis Pub/Sub.
- Si falla la publicación de la revocación, se conserva el commit PostgreSQL.
- Emisión de tickets solo con email verificado, hash en Redis y TTL ≤30 s.
- Privacidad de perfiles en pending y closed sin aceptación previa.
- Rate limiting concurrente real (sliding window/token bucket), 429 HTTP, 503 al
  faltar Redis/PostgreSQL, y rollback completo si falla el adaptador de correo.
- Cuerpo HTTP máximo, extras/JSON inválido, errores correlacionados y no-store;
  cabeceras de cliente ignoradas si el peer no es el proxy de confianza.
- Migraciones desde cero, desde 0001 y desde 0002 hasta 0003, conservando hashes
  email preexistentes al convertirlos de hexadecimal a 32 bytes binarios.

Límites de esta evidencia: ASGITransport prueba los endpoints sin ejecutar su
lifespan; la validación de secretos/arranque tiene pruebas separadas. El correo
se captura en un buzón de prueba y SMTP/STARTTLS se verifica con un doble: **no
hay prueba con un proveedor de correo real**. Tampoco hay todavía servidor WS
para consumir tickets/cerrar sockets revocados. Quedan pendientes versiones
exactas de Compose, HTTPS/WSS real, cobertura porcentual/CI y puertas N0/N8/N9.
La auditoría de dependencias no equivale a una auditoría de imagen o de aplicación.

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
