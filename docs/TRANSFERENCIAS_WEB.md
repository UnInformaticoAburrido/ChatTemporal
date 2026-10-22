# Transferencia de claves en la web · 2026-10-01

Implementa el recorrido N7 sobre los endpoints existentes, sin migraciones nuevas.
La transferencia contiene la clave X25519 actual de la cuenta. El historial se
recupera por REST stored o por replay desde una copia del interlocutor; este
incremento no empaqueta mensajes locales. Preparar el traslado detiene el chat y
borra los mensajes efímeros de la página anterior, explicado antes del botón.

## Recorrido entre dispositivos

1. En el dispositivo anterior, abrir la copia de claves y elegir «Enviar claves
   a otro dispositivo». Mantener esta página abierta: captura un access vigente,
   pausa el chat y prepara una subida sin refresh.
2. En el nuevo, elegir «Acceder desde la aplicación principal» e introducir su
   código bootstrap por el formulario. Nunca se acepta un token desde la URL.
   Exchange sustituye la sesión normal y concede al último dispositivo anterior
   permiso residual exclusivamente para PUT de transferencia a su sucesor.
3. Abrir «Gestionar claves de conversación» → «Recibir claves de otro dispositivo»
   → «Crear solicitud». Mostrar el QR de solicitud al dispositivo anterior o
   copiar su texto/UUID; la solicitud no contiene secreto.
4. El anterior cifra y sube la clave, y muestra un segundo QR privado con el secreto
   de descifrado. Leerlo únicamente desde el nuevo dispositivo. El PUT contiene
   exclusivamente encrypted_blob. La interfaz no copia códigos automáticamente.
5. El nuevo descarga, autentica, comprueba cuenta/pública derivada y coincidencia
   con la pública autoritativa. El usuario crea y descarga una nueva copia .chatkey,
   y debe volver a abrirla. Solo después se ejecuta DELETE para borrar el blob y
   revocar el permiso residual. No se sustituye ni rota ninguna pública.
6. Finalizar en el anterior elimina sus códigos, clave y credenciales locales.
   Cancelar en el destino ejecuta DELETE; abandonar la página limpia memoria y
   deja la limpieza del servidor a su TTL, sin prometer un DELETE durante pagehide.

Entrar en el nuevo mediante las 24 palabras usa recover: revoca las sesiones
previas y **no concede el permiso residual**. Por eso la recepción entre dispositivos
se ofrece únicamente tras exchange. Quien solo tiene su archivo .chatkey puede
abrirlo por el recorrido existente sin usar una transferencia.

La entrada bootstrap manual exige un código válido emitido por la aplicación
principal y sus claves públicas de verificación configuradas en el backend.
También existe [adaptador de apertura automática](INTEGRACION_MAIN_APP.md), apagado
hasta configurar el origen emisor y conectarlo a su controlador autenticado.
La web no fabrica credenciales ni recibe la privada de firma.
Cerrar/recargar el origen antes de preparar y completar el envío pierde el access
capturado; no se renueva una sesión sustituida para prolongar el permiso.

## Formato y custodia

El envelope mantiene el protocolo Python de chat_client.recovery:
XChaCha20-Poly1305, secreto aleatorio de 32 bytes, nonce aleatorio de 24,
AAD = chat-key-transfer-v1 seguido de byte NUL y UUID binario de 16 bytes.
El blob Base64URL contiene nonce || ciphertext/tag, máximo total 65536 bytes.
El QR privado contiene transfer_id, secret y protocol_version=1. La solicitud QR
local contiene format=chat-key-transfer-request, transfer_id y protocol_version=1;
se acepta también UUID manual. QR máximo 1024 caracteres, sin campos repetidos,
extras, versiones desconocidas, Base64 no canónico o UUID distinto del solicitado.

El plaintext de claves es un objeto JSON plano con campos exactos:
format=chat-private-key, version=1, user_id, public_key y private_key. Ambas claves
son Base64URL canónico de 32 bytes. El destino exige derivar la pública de la
privada y comparar con la publicada, además de validar la cuenta autenticada.
Los buffers propios de plaintext, privada y secreto se ponen a cero al terminar;
los strings del QR/JSON se descartan y las vistas/canvas se limpian. Esto no
promete borrado físico de las copias internas del navegador/SO.

Tokens, privadas, QR y copias viven en memoria; no se escriben en storage del
navegador. Solo .chatkey se descarga mediante acción explícita. La copia mantiene
su protección AES-GCM/PBKDF2 independiente del QR y de las 24 palabras.
La credencial capturada por el origen se usa solo para PUT, se descarta al cerrar
el recorrido o la sesión y respeta la fecha original de vencimiento. Un 401 la
invalida sin intentar refresh. La UI retira códigos al vencer ese permiso.

Una subida de respuesta perdida conserva el mismo blob/secreto: el destino puede
comprobarla sin repetir PUT. Un conflicto no se presenta como éxito; el destino
autentica su contenido. DELETE de respuesta perdida puede comprobarse volviendo
a abrir la copia; KEY_TRANSFER_EXPIRED tras validar clave/cuenta confirma que el
blob ya no está disponible y permite continuar. No hay reintentos automáticos de
POST, PUT, exchange ni DELETE.

## QR local y pruebas

Se genera sobre canvas con [qrcode 1.5.4](https://github.com/soldair/node-qrcode)
y se lee localmente con [jsQR 1.4.0](https://github.com/cozmo/jsQR), ambos en un bundle
local versionado con licencias. No hay CDN ni servicio externo de QR. CI exige
`npm run check:qr`; `npm run build:qr` lo regenera desde el lock.

Lectura por imagen PNG/JPEG/WebP (hasta 5 MiB), cámara bajo acción del usuario o
texto manual. El procesamiento de frames se limita a 1024 píxeles por lado; la
cámara se consulta aproximadamente cada 250 ms. Cancelar, salir o resolver un
permiso después de abandonar la pantalla detiene sus tracks y limpia canvas.
CSP añade media-src self/blob para MediaStream; scripts y connect siguen en self.
HTTPS o loopback son necesarios; falta homologar captura óptica y permisos en
los dispositivos finales.

Pruebas Node: interop XChaCha en ambos sentidos con Python, manipulación, UUID/AAD,
límites, cuentas/públicas, QR ambiguos y generación/decodificación de píxeles.
Chromium escritorio/móvil: dos páginas con sesiones distintas, imágenes QR reales,
archivo descargado/restaurado, respuestas perdidas de subida/borrado, cancelación,
cámara denegada y permiso tardío. REST/WS están interceptados y usan datos sintéticos:
no constituyen E2E con emisor bootstrap/SMTP ni ensayo óptico de cámaras físicas.
La integración backend real comprueba las autorizaciones, carga única, TTL y
revocación del permiso residual; evidencia en VALIDACION.md.
