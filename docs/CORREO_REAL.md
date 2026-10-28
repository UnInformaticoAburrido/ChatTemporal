# Configurar el correo de verificación

La configuración permite enviar códigos a Gmail, Hotmail, Outlook y otros buzones.
Se configura la cuenta que **envía**; cada usuario indica su propio correo al registrarse.
Necesitas un proveedor que admita SMTP con usuario y credencial SMTP o contraseña de aplicación.
El acceso mediante OAuth de Microsoft u otros proveedores no está implementado por este archivo.

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
| `username` | Usuario de acceso SMTP. |
| `password` | Credencial SMTP o contraseña de aplicación. Puedes usar comillas simples TOML para símbolos especiales. |

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
