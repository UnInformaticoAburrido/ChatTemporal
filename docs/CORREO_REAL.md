# Configurar el correo de verificación

La configuración permite enviar códigos a Gmail, Hotmail, Outlook y otros buzones.
Se configura la cuenta que **envía**; cada usuario indica su propio correo al registrarse.
Puedes usar OAuth2 de Gmail o un proveedor SMTP con usuario y contraseña de aplicación.
OAuth2 de otros proveedores no está implementado.

## Archivo de ejemplo y archivo privado

- `python/config/mail.example.toml`: plantilla genérica que se guarda en Git. No pongas credenciales aquí.
- `python/config/mail.local.toml`: configuración de esta instalación. Está excluida de Git desde el principio.

Crea la copia si todavía no existe:

```bash
(umask 077; cp -n python/config/mail.example.toml python/config/mail.local.toml)
```

Abre **mail.local.toml** en tu editor y completa:

| Campo | Qué debes poner |
| --- | --- |
| `enabled` | `true` para enviar correo real; `false` conserva la configuración anterior. |
| `host` | Servidor SMTP indicado por el proveedor. |
| `port` | Puerto indicado por el proveedor, normalmente 587 o 465. |
| `security` | `"starttls"` para STARTTLS, o `"tls"` para TLS directo. No se permite conexión sin cifrar. |
| `sender` | Dirección remitente autorizada por el proveedor. |
| `auth_method` | `"oauth2"` para Gmail o `"password"` para autenticación con contraseña. Si se omite, se mantiene el modo con contraseña. |
| `client_id`, `client_secret`, `refresh_token` | Datos OAuth2 de Gmail; solo para `auth_method = "oauth2"`. |
| `username` | Usuario SMTP en modo contraseña. Con OAuth2 se usa `sender` si no lo indicas. |
| `password` | Credencial SMTP o contraseña de aplicación. Puedes usar comillas simples TOML para símbolos especiales. |

## Gmail con OAuth2

La plantilla contiene el formato OAuth2. Completa los tres campos OAuth2 y el remitente
y cambia `enabled` a `true`. El servidor debe ser `smtp.gmail.com`, con STARTTLS en 587
o TLS directo en 465. No añadas una contraseña SMTP en este modo.

Para obtener los datos iniciales:

1. En tu proyecto de Google Cloud, configura la pantalla de consentimiento OAuth y crea
   un cliente OAuth2. Guarda su identificador y secreto únicamente en el archivo privado.
2. Autoriza **la cuenta remitente** con el permiso `https://mail.google.com/` y acceso
   sin conexión (`access_type=offline`). Para SMTP no basta con el permiso de envío de la Gmail API.
3. Intercambia el código de autorización siguiendo el flujo de Google y guarda el
   `refresh_token` obtenido. Si utilizas OAuth 2.0 Playground, configura tus propias
   credenciales OAuth y el URI de redirección que indique esa herramienta.
4. Copia el token de renovación en `refresh_token`. No lo pegues en chats, capturas ni archivos versionados.

El proyecto, sus permisos, usuarios de prueba y posibles verificaciones se gestionan
con Google. El token puede caducar o ser revocado; si deja de funcionar, vuelve a autorizar
la cuenta y sustituye el token. Nexo no incorpora una pantalla para esa autorización inicial.

Al enviar cada código, Nexo obtiene un token de acceso nuevo por HTTPS y se autentica
con XOAUTH2. No guarda el token de acceso en disco ni cambia el archivo de configuración.
Si Google rechaza la renovación o la autenticación, el envío falla sin recurrir a contraseñas
ni al buzón de pruebas. `client_secret` y `refresh_token` deben permanecer privados.

Referencias oficiales: [renovación de tokens de Google](https://developers.google.com/identity/protocols/oauth2/web-server#offline)
y [autenticación XOAUTH2 de Gmail](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol).

## SMTP con contraseña

Usa `auth_method = "password"`, completa `username` y `password` y elimina los campos
OAuth2 o déjalos vacíos. La configuración anterior sin `auth_method` sigue siendo válida.
No es necesario codificar los símbolos de la contraseña como una URL.
No uses las contraseñas de los destinatarios. Tampoco cambies la plantilla pública.

## Aplicar los cambios

Desde la carpeta del proyecto, con la instalación local ya preparada:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build --force-recreate python worker
```

La primera aplicación necesita recrear los servicios para incorporar la ruta del archivo.
Después de editarlo, reinicia ambos servicios para recargar también el remitente:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml restart python worker
```

Con el archivo activado, los códigos se envían al proveedor real y dejan de ir al buzón de pruebas.
En Mi cuenta, pulsa «Enviar otro código» y comprueba tu buzón y la carpeta de correo no deseado.
Un fallo del proveedor se muestra como un problema de envío; no se sustituye por un envío ficticio.

Si el archivo no existe o está desactivado, se mantiene el comportamiento anterior
(`secrets/smtp_url`; en la instalación de pruebas, Mailpit). Si está activo pero contiene
errores, se rechaza la configuración: no se vuelve silenciosamente al buzón de pruebas.

Esta ruta ya está conectada en Compose local. Para otras instalaciones, monta el archivo
privado de solo lectura y configura `CHAT_MAIL_CONFIG_FILE` con su ruta dentro del contenedor,
tanto para la API como para el worker. No copies credenciales a una imagen Docker.

## Comprobar que no se subirá

```bash
git check-ignore python/config/mail.local.toml
git ls-files -- python/config/mail.local.toml
```

El primero debe mostrar la ruta; el segundo no debe mostrar nada. Un archivo ya
versionado sigue siendo versionado aunque se añada a `.gitignore`: por eso la plantilla
y la copia privada tienen nombres diferentes. No uses `git add -f` con la copia privada.
