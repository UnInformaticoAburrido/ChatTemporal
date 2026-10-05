# Instalador de Chat para servidor Linux

Instala una **instancia nueva** con registro y chat, HTTPS automático, PostgreSQL,
Redis volátil, API, worker, observabilidad, arranque con systemd, backups cifrados
cada seis horas y ensayo mensual de restauración. No actualiza bases existentes.

La instalación de paquetes está automatizada para Debian/Ubuntu con systemd.
En otro Linux con systemd, instala Docker Engine/Compose v2, curl, OpenSSL, tar,
Git, coreutils, iproute2 y sincronización NTP y usa `--skip-packages`. El instalador
requiere Compose >=2.24.4, util-linux (`flock`) y acceso root al daemon; no añade usuarios al grupo Docker.
Si hay un motor Docker existente, lo utiliza sin sustituirlo ni eliminar paquetes.

## Antes de ejecutarlo

Necesitas un dominio/subdominio con DNS A/AAAA apuntando al servidor, los puertos
80/443 disponibles y abiertos también en el firewall del proveedor, y un SMTP
real que permita el remitente elegido. El instalador no puede crear cuentas de
correo, registros DNS o reglas del proveedor sin sus credenciales/API. Los bundles
web ya están incluidos: no necesitas instalar Node o Python en el host.

Usa un servidor dedicado a Chat. El comando completo instala Docker si falta,
arranca servicios, permite HTTP/HTTPS si UFW/firewalld ya están activos y aplica
la retención de journald de catorce días **a todo el host**. Conserva las reglas
SSH y no activa un firewall inactivo. Una política nftables personalizada requiere
configurar sus reglas como operador. Solo Caddy publica puertos; PostgreSQL,
Redis, API y métricas permanecen internos. La verificación externa de red sigue
siendo necesaria antes de producción.

## 1. Obtener y generar el paquete

Clona la rama que contiene este incremento (o una release que lo incluya):

```bash
git clone --branch work-in-progress/n7-recuperacion-push \
  https://github.com/UnInformaticoAburrido/ChatTemporal.git ChatTemporal
cd ChatTemporal
bash scripts/preparar_instalador.sh
```

Esto crea `instalador-servidor/instalar.sh` y este README. El directorio completo
está en `.gitignore`; nunca se fuerza su inclusión. Las plantillas y el generador
están versionados para reconstruirlo en cualquier clon. No guarda credenciales
en las plantillas ni modifica la configuración del checkout de desarrollo.

## 2. Ejecutar

```bash
sudo bash instalador-servidor/instalar.sh
```

Pedirá dominio, correo de contacto y remitente. La URL SMTP se introduce con
entrada oculta. Admite `smtp://` con STARTTLS (587) o `smtps://` con TLS implícito
(465). Usuario/contraseña deben estar codificados como componentes de URL, por
ejemplo `@` → `%40`, `:` → `%3A`, `/` → `%2F`, `%` → `%25`. No uses `bash -x`.

También admite ejecución sin preguntas con un archivo privado de URL SMTP:

```bash
install -m 0600 /dev/null /ruta/privada/smtp_url
# Edita ese archivo y escribe la URL; no pegues credenciales en un comando.
sudo bash instalador-servidor/instalar.sh \
  --domain chat.tudominio.com \
  --acme-email operaciones@tudominio.com \
  --email-from chat@tudominio.com \
  --alert-email operaciones@tudominio.com \
  --smtp-url-file /ruta/privada/smtp_url
```

Por defecto instala en `/opt/chat`. `--target /opt/otro-chat` cambia la ubicación;
el destino debe ser nuevo, absoluto y sin espacios. Abortará si existe el destino,
el proyecto Docker `chat-server`, sus volúmenes, unidades systemd previas o los
puertos ocupados. Un lock impide instalaciones simultáneas. No rota
claves, cambia contraseñas de bases existentes ni ejecuta `down -v`.

Genera secretos independientes, configura contactos/dominio/VAPID, adapta permisos
a los UID reales de las imágenes, construye las imágenes fijadas y comprueba
configuración y TLS/autenticación SMTP **sin enviar correo**. Después migra y
arranca, realiza un backup y un ensayo de restauración y activa los timers.
La comprobación HTTPS valida el certificado contra la CA del sistema y conecta
al Caddy local, para no dar por bueno otro servidor que responda por el dominio.

Solo muestra éxito cuando HTTPS responde. Un proveedor puede autenticar y aun así
rechazar remitente/destinatario: el primer registro real verifica la entrega.
No se crean cuentas de ejemplo ni se imprimen contraseñas. Un fallo conserva la
instalación y sus claves para diagnóstico; no vuelvas a ejecutarlo contra ese
destino ni borres volúmenes. Los errores SMTP ocurren antes de publicar la web.

## 3. Registrarte

Abre `https://chat.tudominio.com/`, crea tu cuenta, guarda las 24 palabras,
verifica el código recibido por correo y crea/descarga/abre tu copia `.chatkey`.
La contraseña de esa copia es independiente de las 24 palabras. Para hablar con
otra cuenta, comparte una invitación desde la interfaz. Recargar/cerrar la página
requiere volver a acceder y abrir la copia: las claves y tokens están en RAM.

## Aplicación principal (opcional)

El registro directo no necesita main-app. Sin su pública, se crea un par Ed25519
independiente de reserva: la privada queda en `server-operator/`, sin montar en
ningún contenedor; el acceso automático permanece desactivado. No se conecta
automáticamente ninguna aplicación externa.

Si ya tienes el emisor auténtico, proporciona **solo su pública** y su kid:

```bash
sudo bash instalador-servidor/instalar.sh \
  --bootstrap-public-key /ruta/main-public.pem \
  --bootstrap-kid main-1 --main-origin https://app.tudominio.com
```

También pedirá los otros datos. El controlador autenticado de main-app debe
resolver el UUID de Chat, emitir el JWT y utilizar el adaptador descrito en
`docs/INTEGRACION_MAIN_APP.md` del repositorio. No copiar su privada a Chat.

## Operación y copias

```bash
sudo systemctl status chat-stack.service
sudo systemctl list-timers chat-backup.timer chat-restore-drill.timer chat-log-retention.timer
cd /opt/chat
sudo docker compose ps
sudo docker compose logs --tail=50 python worker
sudo systemctl start chat-backup.service
sudo systemctl start chat-restore-drill.service
sudo systemctl stop chat-stack.service
sudo systemctl start chat-stack.service
```

El `.env` instalado contiene únicamente nombres públicos de Compose. Las
contraseñas viven en `secrets/`, el backup AES-256-GCM en `backups/` y su clave en
`server-operator/backup.key`. Copia esa clave de 32 bytes y las claves/configuración
de servicio a una custodia independiente; copia también los backups cifrados
a otro almacenamiento. El instalador prepara copias locales, no un destino remoto
cuya cuenta no se ha proporcionado. Los backups excluyen historial temporal,
sesiones y Push. Sin la clave independiente no se pueden restaurar.

Los trabajos de backup usan un contenedor dedicado con Python y clientes
PostgreSQL compatibles, red interna, filesystem de solo lectura y tmpfs para
el SQL del ensayo; no montan el socket Docker. Los `.prom` contienen solo métricas.
La falta de backup/ensayo genera alertas. Para recuperación/actualización/rotación,
consulta `operations/README.md`: no se restaura encima de una base activa.

## Preparar sin instalar y ver el plan

```bash
bash instalador-servidor/instalar.sh --plan
bash instalador-servidor/instalar.sh --prepare-only \
  --target "$PWD/instalador-servidor/preparado" \
  --domain chat.tudominio.com --acme-email operaciones@tudominio.com \
  --email-from chat@tudominio.com --smtp-url-file /ruta/privada/smtp_url
```

`--prepare-only` no usa root, Docker, apt, systemd ni red. Genera configuración
y secretos nuevos en ese destino; no es una instalación arrancada ni un ensayo
de SMTP/ACME. No subas ese resultado a Git. Para instalar usa un destino nuevo
con el comando completo; los permisos de contenedores se ajustan en ese modo.

La generación y validación offline están probadas. El arranque completo necesita
un servidor Linux, dominio y SMTP reales; no se declara homologado en staging
solo por generar archivos. Siguen pendientes validación Push/dispositivos,
correo/alertas realmente recibidos, firewall externo, custodia fuera del host,
restauración representativa y capacidad. Ver `docs/N9_HOMOLOGACION_RELEASE.md`.

Fuentes: [Docker Debian](https://docs.docker.com/engine/install/debian/),
[Docker Ubuntu](https://docs.docker.com/engine/install/ubuntu/) y
[SMTP de Alertmanager](https://prometheus.io/docs/alerting/latest/configuration/).
