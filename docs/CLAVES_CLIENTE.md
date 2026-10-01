# Claves del cliente web · 2026-10-01

Segundo incremento del cliente. Desde una cuenta verificada, «Gestionar claves
de conversación» permite crear y restaurar claves; todavía no envía mensajes.

## Custodia y recorrido

La privada X25519 se genera localmente con libsodium. Se descarga una copia
cifrada mediante una contraseña independiente de las 24 palabras BIP-39. Antes
de publicar la pública, el usuario debe volver a abrir ese archivo: no basta con
marcar una casilla. El contenido del archivo y su contraseña nunca se envían al
servidor. La API recibe únicamente public_key y protocol_version.

El navegador no guarda privadas, copias ni tokens en localStorage, sessionStorage
o IndexedDB. La privada desbloqueada vive en memoria y se pone a cero al bloquear,
cerrar sesión, cambiar de pantalla o abandonar la página; también se descartan
operaciones criptográficas que terminan después de abandonar su pantalla. Esto
es una limpieza de buffers propios, **no una garantía de borrado físico** de toda
copia interna del navegador, sistema operativo o extensiones.

Conservar archivo y contraseña por separado. Al recargar se requiere acceder a
la cuenta y abrir la copia de nuevo. La pérdida del archivo o contraseña no se
resuelve con las 24 palabras: estas recuperan identidad, no privadas de cifrado.
Este incremento no permite rotar una clave existente ni recuperar su copia desde
el servidor. La transferencia QR/replay de N7 sigue pendiente en la interfaz.

## Formato local v1

Archivo `.chatkey`, JSON canónico en una línea, máximo 4096 bytes. Campos exactos:
format=chat-key-backup, version=1, user_id, public_key, cipher=AES-256-GCM,
kdf=PBKDF2-SHA256, iterations=600000, salt, nonce, ciphertext, en ese orden.
Binarios Base64URL canónicos sin padding; salt de 16 bytes, nonce de 12, pública
de 32 y ciphertext/tag de 48. El plaintext cifrado es exclusivamente la privada
de 32 bytes. Todos los campos excepto ciphertext se autentican como AAD.

WebCrypto deriva AES-256 desde la contraseña literal (sin trim/normalización),
con PBKDF2-HMAC-SHA256 de coste fijo. Contraseña de 12–256 puntos de código;
salt/nonce nuevos en cada exportación. El límite de coste se comprueba antes de
la derivación para rechazar archivos con parámetros abusivos. Se rechazan claves
JSON duplicadas, campos extra, versiones desconocidas, formatos no canónicos,
cuentas distintas, etiqueta GCM inválida y públicas que no derivan de la privada.

La elección de PBKDF2 facilita WebCrypto nativo; el coste usa la referencia de
[OWASP para PBKDF2-HMAC-SHA256](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
El archivo no reemplaza el protocolo de transferencia QR de N7 ni su formato.

## Publicación sin sustituciones accidentales

La web consulta la pública propia. Si existe, solo ofrece abrir su copia y
comprueba coincidencia; no llama a PUT ni al endpoint rotate. Si falta, publica
mediante `PUT /users/me/keys` con `If-None-Match: *` después de verificar la copia.

El backend comprueba ausencia bajo el mismo bloqueo del usuario que serializa
sustituciones y rotaciones. Si ya existe una fila devuelve 412 KEY_ALREADY_EXISTS,
incluso si la pública coincide. Otros valores de esa cabecera devuelven 400
INVALID_PRECONDITION. Las llamadas existentes sin cabecera conservan su contrato.
La cabecera sigue la [precondición HTTP de creación](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-None-Match).

Tras publicar, la web lee la pública autoritativa y solo activa la privada si
coinciden. Si se pierde la respuesta, no repite automáticamente el PUT: al abrir
la copia de nuevo consulta primero el estado. Si otra publicación ganó la carrera,
no sobrescribe su clave y explica el conflicto.

## Dependencias, compatibilidad y pruebas

libsodium-wrappers 0.8.4, esbuild 0.28.2 solo para generar el bundle y dependencias
transitivas en package-lock. Bundle y licencias versionados en web/public/vendor;
`npm run build:crypto` lo genera y `npm run check:crypto` exige coincidencia exacta.
No se usa CDN ni Node en producción. La biblioteca se carga al gestionar claves,
sin bloquear identidad si el navegador no la soporta.

CSP permite `wasm-unsafe-eval` para compilar el WebAssembly de libsodium; no permite
`unsafe-eval` ni scripts inline. Véase [CSP y WebAssembly](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/script-src).
HTTPS o loopback son necesarios para WebCrypto. Se deben homologar los navegadores
finales en staging; las pruebas actuales usan Chromium y viewport móvil.

`LocalKey.encrypt/decrypt` implementa el [crypto_box de libsodium](https://libsodium.gitbook.io/doc/public-key_cryptography/authenticated_encryption)
con nonce de 24 bytes, crypto_meta=nonce||pública de 56, versión 1 y límite de
256 puntos de código. Son primitivas preparadas para el siguiente bloque; aún
no hay transporte de mensajes ni historial en la interfaz.

Pruebas de Node verifican exportación/restauración, autenticación, límites,
bloqueo y cifrado/descifrado en ambos sentidos con chat_client.crypto de Python.
Los recorridos Chromium ejercitan descargas reales y restauración con API
interceptada, publicación concurrente y respuesta perdida. La integración Python
usa PostgreSQL/Redis reales para verificar la precondición atómica. CI comprueba
además las rutas/cabeceras servidas por Caddy HTTPS y audita los locks npm/Python.
