#!/usr/bin/env bash
# Instalación NUEVA. No actualiza bases ni sobrescribe claves de una instalación.
set -Eeuo pipefail
set +x
umask 077
export LC_ALL=C
TASK_STAGE= TASK_INPUT= TASK_PREPARE=0 TASK_SKIP_PACKAGES=0
TASK_TARGET=/opt/chat TASK_DOMAIN= TASK_CONTACT= TASK_FROM= TASK_ALERT=
TASK_SMTP_FILE= TASK_BOOTSTRAP_FILE= TASK_BOOTSTRAP_KID=main-server-1 TASK_MAIN_ORIGIN=
TASK_SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

fail() { printf 'Error: %s\n' "$1" >&2; exit 1; }
cleanup() {
    [[ -z "$TASK_STAGE" ]] || rm -rf -- "$TASK_STAGE"
    [[ -z "$TASK_INPUT" ]] || rm -rf -- "$TASK_INPUT"
}
trap cleanup EXIT
trap 'echo "Instalación interrumpida. Se conservan los datos ya instalados." >&2; exit 130' INT TERM
trap 'echo "La instalación no terminó. No se eliminan volúmenes ni claves existentes." >&2' ERR

usage() {
    cat <<'HELP'
Uso: sudo bash instalador-servidor/instalar.sh [opciones]
  --source DIR                   Checkout de Chat (por defecto, padre del paquete)
  --target DIR                   Instalación nueva, por defecto /opt/chat
  --domain chat.tudominio.com     Dominio público con DNS A/AAAA al servidor
  --acme-email EMAIL             Contacto de certificados y VAPID
  --email-from EMAIL             Remitente autorizado por tu proveedor SMTP
  --alert-email EMAIL            Destinatario de alertas (por defecto, contacto)
  --smtp-url-file FILE           Archivo privado con smtp:// o smtps://
  --bootstrap-public-key FILE    Pública Ed25519 de main-app (opcional)
  --bootstrap-kid KID            kid de esa pública, por defecto main-server-1
  --main-origin https://HOST     Origen de main-app; exige su pública real
  --prepare-only                Solo genera archivos; no requiere root/red/Docker
  --skip-packages               Dependencias y Docker ya instalados
  --plan                        Muestra pasos sin modificar nada
  --help                        Esta ayuda
Sin las opciones de dominio/correo/SMTP se solicitan por terminal.
No se aceptan contraseñas en argumentos ni se ejecutan archivos .env como Bash.
HELP
}
while (($#)); do
    case "$1" in
        --help) usage; exit 0 ;;
        --plan)
            echo '1. Recoger dominio/SMTP; comprobar instalación nueva y Linux/systemd.'
            echo '2. Instalar dependencias y Docker desde el repositorio oficial (Debian/Ubuntu).'
            echo '3. Generar claves, secretos y configuración de HTTPS/SMTP/observabilidad.'
            echo '4. Construir imágenes, validar TLS/auth SMTP sin enviar correo, migrar y arrancar.'
            echo '5. Instalar arranque systemd, backups/ensayo y retención de logs; verificar HTTPS.'
            exit 0 ;;
        --prepare-only) TASK_PREPARE=1; shift ;;
        --skip-packages) TASK_SKIP_PACKAGES=1; shift ;;
        --source|--target|--domain|--acme-email|--email-from|--alert-email|--smtp-url-file|--bootstrap-public-key|--bootstrap-kid|--main-origin)
            (($# >= 2)) && [[ -n "$2" ]] || fail 'Falta el valor de una opción.'
            case "$1" in
                --source) TASK_SOURCE=$2 ;; --target) TASK_TARGET=$2 ;;
                --domain) TASK_DOMAIN=$2 ;; --acme-email) TASK_CONTACT=$2 ;;
                --email-from) TASK_FROM=$2 ;; --alert-email) TASK_ALERT=$2 ;;
                --smtp-url-file) TASK_SMTP_FILE=$2 ;; --bootstrap-public-key) TASK_BOOTSTRAP_FILE=$2 ;;
                --bootstrap-kid) TASK_BOOTSTRAP_KID=$2 ;; --main-origin) TASK_MAIN_ORIGIN=$2 ;;
            esac
            shift 2 ;;
        *) fail 'Opción desconocida; consulta --help.' ;;
    esac
done
[[ $(uname -s) == Linux ]] || fail 'Se requiere Linux.'
if (( ! TASK_PREPARE )); then
    (( EUID == 0 )) || fail 'Ejecuta la instalación completa con sudo bash.'
    [[ -d /run/systemd/system ]] || fail 'La instalación completa necesita systemd.'
    command -v flock >/dev/null || fail 'Instala util-linux (flock).'
    exec 9>/run/lock/chat-server-install.lock
    flock -n 9 || fail 'Ya hay otra instalación de Chat ejecutándose.'
    for task_unit in chat-stack.service chat-backup.service chat-backup.timer chat-restore-drill.service chat-restore-drill.timer chat-log-retention.service chat-log-retention.timer; do
        [[ ! -e "/etc/systemd/system/$task_unit" && ! -L "/etc/systemd/system/$task_unit" ]] || fail 'Ya existen unidades Chat; no se sobrescribirán.'
    done
fi
TASK_SOURCE=$(cd -- "$TASK_SOURCE" && pwd)
[[ -f "$TASK_SOURCE/docker-compose.yml" && -f "$TASK_SOURCE/python/chat/config.py" ]] || fail 'Indica el checkout mediante --source.'
[[ "$TASK_TARGET" =~ ^/[A-Za-z0-9_/-]+$ && "$TASK_TARGET" != / && "$TASK_TARGET" != */../* && "$TASK_TARGET" != */ ]] || fail 'Destino absoluto sin espacios ni .. requerido.'
[[ ! -e "$TASK_TARGET" && ! -L "$TASK_TARGET" ]] || fail 'El destino ya existe. Usa otro directorio; para actualizar consulta operations/README.md.'

prompt() {
    local name=$1 label=$2 value
    [[ -n ${!name} ]] && return 0
    [[ -t 0 ]] || fail 'Faltan datos; usa las opciones de --help.'
    read -r -p "$label: " value
    printf -v "$name" '%s' "$value"
}
prompt TASK_DOMAIN 'Dominio público del chat'
prompt TASK_CONTACT 'Correo de contacto (ACME/VAPID)'
prompt TASK_FROM 'Remitente SMTP autorizado'
TASK_ALERT=${TASK_ALERT:-$TASK_CONTACT}
domain_valid() {
    local value=$1 label
    [[ ${#value} -le 253 && "$value" == *.* && "$value" != *.invalid && "$value" != *.example && "$value" != localhost ]] || return 1
    [[ "$value" =~ ^[a-z0-9.-]+$ && "$value" != *..* && "$value" != .* && "$value" != *. ]] || return 1
    local -a labels
    IFS=. read -r -a labels <<< "$value"
    for label in "${labels[@]}"; do
        [[ "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#label} -le 63 ]] || return 1
    done
}
email_valid() {
    [[ "$1" =~ ^[A-Za-z0-9._+%-]+@([a-z0-9.-]+)$ ]] && domain_valid "${BASH_REMATCH[1]}"
}
domain_valid "$TASK_DOMAIN" || fail 'Dominio DNS público inválido.'
for task_email in "$TASK_CONTACT" "$TASK_FROM" "$TASK_ALERT"; do email_valid "$task_email" || fail 'Correo inválido.'; done
[[ "$TASK_BOOTSTRAP_KID" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || fail 'kid inválido.'
if [[ -n "$TASK_MAIN_ORIGIN" ]]; then
    [[ "$TASK_MAIN_ORIGIN" == https://* && -n "$TASK_BOOTSTRAP_FILE" ]] || fail 'main-origin exige HTTPS y la pública real de main-app.'
    domain_valid "${TASK_MAIN_ORIGIN#https://}" || fail 'main-origin debe ser un origen HTTPS sin ruta/puerto.'
fi
if [[ -z "$TASK_SMTP_FILE" ]]; then
    [[ -t 0 ]] || fail 'Falta --smtp-url-file.'
    TASK_INPUT=$(mktemp -d)
    TASK_SMTP_FILE="$TASK_INPUT/smtp_url"
    read -r -s -p 'URL SMTP con credenciales (entrada oculta): ' task_smtp
    printf '\n' >&2
    printf '%s' "$task_smtp" > "$TASK_SMTP_FILE"
    unset task_smtp
fi
[[ -f "$TASK_SMTP_FILE" && ! -L "$TASK_SMTP_FILE" && $(stat -c %s -- "$TASK_SMTP_FILE") -le 8192 ]] || fail 'Archivo SMTP inválido.'
TASK_SMTP=$(<"$TASK_SMTP_FILE")
[[ ! "$TASK_SMTP" =~ [[:space:][:cntrl:]] && "$TASK_SMTP" =~ ^smtps?://([^/@]+@)?[a-z0-9.-]+(:[0-9]+)?/?$ ]] || fail 'URL SMTP inválida; codifica usuario/contraseña como URL.'
task_authority=${TASK_SMTP#*://}; task_authority=${task_authority%/}
task_hostport=${task_authority##*@}; task_smtp_host=${task_hostport%%:*}
domain_valid "$task_smtp_host" || fail 'Configura un proveedor SMTP real.'
if [[ "$task_hostport" == *:* ]]; then
    task_smtp_port=${task_hostport##*:}
    [[ ${#task_smtp_port} -le 5 ]] && (( 10#$task_smtp_port >= 1 && 10#$task_smtp_port <= 65535 )) || fail 'Puerto SMTP inválido.'
fi
TASK_SMTP_USERNAME= TASK_SMTP_PASSWORD=
decode_component() {
    local rest=$1 value= char hex
    while [[ -n "$rest" ]]; do
        if [[ "$rest" == %* ]]; then
            hex=${rest:1:2}; [[ "$hex" =~ ^[A-Fa-f0-9]{2}$ && "$hex" != 00 ]] || fail 'Codificación SMTP inválida.'
            printf -v char '%b' "\\x$hex"; rest=${rest:3}
        else char=${rest:0:1}; rest=${rest:1}; fi
        value+=$char
    done
    [[ ! "$value" =~ [[:cntrl:]] ]] || fail 'Caracteres de control en SMTP.'
    printf -v "$2" '%s' "$value"
}
if [[ "$task_authority" == *@* ]]; then
    task_userinfo=${task_authority%@*}
    [[ "$task_userinfo" == *:* ]] || fail 'SMTP con autenticación necesita usuario y contraseña.'
    decode_component "${task_userinfo%%:*}" TASK_SMTP_USERNAME
    decode_component "${task_userinfo#*:}" TASK_SMTP_PASSWORD
    [[ -n "$TASK_SMTP_USERNAME" && -n "$TASK_SMTP_PASSWORD" ]] || fail 'Credenciales SMTP vacías.'
fi
if [[ "$task_hostport" != *:* ]]; then
    if [[ "$TASK_SMTP" == smtps://* ]]; then task_hostport+=:465; else task_hostport+=:587; fi
fi
json_string() { local value=${1//\\/\\\\}; value=${value//\"/\\\"}; printf '"%s"' "$value"; }

install_packages() {
    if (( TASK_SKIP_PACKAGES )); then return; fi
    # /etc/os-release es configuración del sistema, no entrada de usuario.
    . /etc/os-release
    case "$ID" in
        debian|ubuntu)
            export DEBIAN_FRONTEND=noninteractive
            apt-get update
            apt-get install -y ca-certificates curl openssl tar git iproute2
            if ! command -v docker >/dev/null 2>&1; then
                for task_package in docker.io podman-docker containerd runc; do
                    if dpkg-query -W -f='${Status}' "$task_package" 2>/dev/null | grep -qx 'install ok installed'; then
                        fail 'Hay paquetes de motor en uso. Configura Docker/Compose y utiliza --skip-packages.'
                    fi
                done
                install -d -m 0755 /etc/apt/keyrings
                curl --proto '=https' --tlsv1.2 -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
                chmod 0644 /etc/apt/keyrings/docker.asc
                cat > /etc/apt/sources.list.d/docker.sources <<REPO
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
REPO
                apt-get update
                apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            fi
            if ! systemctl is-active --quiet chrony && ! systemctl is-active --quiet chronyd && ! systemctl is-active --quiet systemd-timesyncd; then
                apt-get install -y chrony
                systemctl enable --now chrony
            fi
            ;;
        *) fail 'En esta distribución instala Docker Compose v2, curl, OpenSSL, tar e iproute2 y usa --skip-packages.' ;;
    esac
}
if (( ! TASK_PREPARE )); then
    install_packages
    for task_command in docker curl openssl tar ss systemctl; do command -v "$task_command" >/dev/null || fail 'Falta una dependencia; consulta README.'; done
    systemctl enable --now docker
    docker info >/dev/null 2>&1 || fail 'Docker no está disponible.'
    task_compose_version=$(docker compose version --short)
    task_compose_version=${task_compose_version#v}
    [[ $(printf '%s\n' 2.24.4 "$task_compose_version" | sort -V | head -n 1) == 2.24.4 ]] || fail 'Docker Compose >=2.24.4 requerido.'
    [[ -z $(docker ps -aq --filter label=com.docker.compose.project=chat-server) ]] || fail 'Ya existe el proyecto chat-server; no se actualizará automáticamente.'
    [[ -z $(docker volume ls -q --filter label=com.docker.compose.project=chat-server) ]] || fail 'Hay volúmenes previos de chat-server; no se reutilizarán sin revisión.'
    [[ -z $(ss -H -ltn 'sport = :80 or sport = :443') ]] || fail 'Los puertos 80/443 están ocupados.'
    getent ahosts "$TASK_DOMAIN" >/dev/null || fail 'El dominio todavía no resuelve; configura DNS antes.'
fi
for task_command in openssl tar sed tail install git; do command -v "$task_command" >/dev/null || fail 'Falta Git/OpenSSL/tar/sed/coreutils.'; done
TASK_RELEASE=$(git -c safe.directory="$TASK_SOURCE" -C "$TASK_SOURCE" rev-parse HEAD)
if ! git -c safe.directory="$TASK_SOURCE" -C "$TASK_SOURCE" diff --quiet HEAD --; then TASK_RELEASE+=-dirty; fi
TASK_DOCKER_BIN=$(command -v docker || true)
TASK_DOCKER_BIN=${TASK_DOCKER_BIN:-/usr/bin/docker}
mkdir -p -- "$(dirname -- "$TASK_TARGET")"
TASK_STAGE=$(mktemp -d "${TASK_TARGET}.tmp.XXXXXX")
# Solo archivos aptos para Git de los directorios de release: excluye .env,
# secrets, settings.local, caches y datos locales incluso si viven bajo operations.
git -c safe.directory="$TASK_SOURCE" -C "$TASK_SOURCE" ls-files --cached --others --exclude-standard -z |
    while IFS= read -r -d '' task_file; do
        case "$task_file" in
            python/tests/*) continue ;;
            BD/*|caddy/*|operations/*|prometheus/*|python/*|web/public/*|docs/*|README.md|docker-compose.yml|docker-compose.production.yml)
                [[ ! -f "$TASK_SOURCE/$task_file" && ! -L "$TASK_SOURCE/$task_file" ]] || printf '%s\0' "$task_file" ;;
        esac
    done | tar -C "$TASK_SOURCE" --null -T - -cf - | tar -C "$TASK_STAGE" --no-same-owner -xf -
chmod -R u=rwX,go=rX "$TASK_STAGE"/{BD,caddy,operations,prometheus,python,web}
mkdir -p "$TASK_STAGE"/{secrets/chat-public,secrets/bootstrap-public,server-config,server-operator,backups,artifacts/operations}
openssl rand -hex 32 > "$TASK_STAGE/secrets/postgres_password"
openssl rand -hex 32 > "$TASK_STAGE/secrets/redis_password"
task_pg_password=$(<"$TASK_STAGE/secrets/postgres_password")
task_redis_password=$(<"$TASK_STAGE/secrets/redis_password")
printf 'postgresql://chat:%s@postgresql:5432/chat' "$task_pg_password" > "$TASK_STAGE/secrets/database_url"
printf 'redis://:%s@redis:6379/0' "$task_redis_password" > "$TASK_STAGE/secrets/redis_url"
unset task_pg_password task_redis_password
printf '%s' "$TASK_SMTP" > "$TASK_STAGE/secrets/smtp_url"
printf '%s' "$TASK_SMTP_PASSWORD" > "$TASK_STAGE/secrets/alertmanager_smtp_password"
openssl rand 48 > "$TASK_STAGE/secrets/invitation_hmac"
openssl rand 32 > "$TASK_STAGE/server-operator/backup.key"
openssl genpkey -algorithm ED25519 -out "$TASK_STAGE/secrets/chat_jwt_private.pem" 2>/dev/null
openssl pkey -in "$TASK_STAGE/secrets/chat_jwt_private.pem" -pubout -out "$TASK_STAGE/secrets/chat-public/chat-server-1.pem" 2>/dev/null
if [[ -n "$TASK_BOOTSTRAP_FILE" ]]; then
    [[ -f "$TASK_BOOTSTRAP_FILE" && ! -L "$TASK_BOOTSTRAP_FILE" ]] || fail 'Pública bootstrap inexistente.'
    # Solo SubjectPublicKeyInfo Ed25519; nunca aceptar/guardar una privada proporcionada.
    [[ $(head -n 1 -- "$TASK_BOOTSTRAP_FILE") == '-----BEGIN PUBLIC KEY-----' ]] || fail 'Proporciona solo la pública bootstrap.'
    openssl pkey -pubin -in "$TASK_BOOTSTRAP_FILE" -text -noout 2>/dev/null | grep -q '^ED25519 Public-Key:' || fail 'Bootstrap requiere Ed25519.'
    openssl pkey -pubin -in "$TASK_BOOTSTRAP_FILE" -pubout -out "$TASK_STAGE/secrets/bootstrap-public/$TASK_BOOTSTRAP_KID.pem" 2>/dev/null
else
    openssl genpkey -algorithm ED25519 -out "$TASK_STAGE/server-operator/bootstrap_issuer_private.pem" 2>/dev/null
    openssl pkey -in "$TASK_STAGE/server-operator/bootstrap_issuer_private.pem" -pubout \
        -out "$TASK_STAGE/secrets/bootstrap-public/$TASK_BOOTSTRAP_KID.pem" 2>/dev/null
fi
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$TASK_STAGE/secrets/vapid_private.pem" 2>/dev/null
task_vapid=$(openssl pkey -in "$TASK_STAGE/secrets/vapid_private.pem" -pubout -outform DER 2>/dev/null | tail -c 65 | openssl base64 -A | tr '+/' '-_' | tr -d '=')
task_main=[]
[[ -z "$TASK_MAIN_ORIGIN" ]] || task_main="[\"$TASK_MAIN_ORIGIN\"]"
sed -e 's/chat_jwt_active_kid = "chat-local-1"/chat_jwt_active_kid = "chat-server-1"/' \
    -e "s|https://chat.example.invalid|https://$TASK_DOMAIN|g" \
    -e "s|vapid_public_key = \"CONFIGURAR\"|vapid_public_key = \"$task_vapid\"|" \
    -e "s|mailto:operaciones@example.invalid|mailto:$TASK_CONTACT|" \
    -e "s|email_from = \"chat@example.invalid\"|email_from = \"$TASK_FROM\"|" \
    -e "s|bootstrap_web_origins = \[\]|bootstrap_web_origins = $task_main|" \
    "$TASK_STAGE/python/config/settings.toml" > "$TASK_STAGE/server-config/settings.toml"
sed -e "s|operaciones@example.invalid|$TASK_CONTACT|g" -e "s|chat.example.invalid|$TASK_DOMAIN|g" \
    "$TASK_STAGE/caddy/Caddyfile" > "$TASK_STAGE/server-config/Caddyfile"
sed "s|https://chat.example.invalid|https://$TASK_DOMAIN|g" "$TASK_STAGE/prometheus/prometheus.yml" > "$TASK_STAGE/server-config/prometheus.yml"
{
    printf 'global:\n  resolve_timeout: 5m\n  smtp_smarthost: '; json_string "$task_hostport"
    printf '\n  smtp_from: '; json_string "$TASK_FROM"
    printf '\n  smtp_auth_username: '; json_string "$TASK_SMTP_USERNAME"
    printf '\n  smtp_auth_password_file: /run/secrets/alertmanager_smtp_password\n  smtp_require_tls: true\n'
    [[ "$TASK_SMTP" != smtps://* ]] || printf '  smtp_force_implicit_tls: true\n'
    printf 'route:\n  receiver: oncall\n  group_by: [alertname, job]\n  group_wait: 30s\n  group_interval: 5m\n  repeat_interval: 4h\nreceivers:\n  - name: oncall\n    email_configs:\n      - to: '
    json_string "$TASK_ALERT"; printf '\n        send_resolved: true\n'
} > "$TASK_STAGE/server-config/alertmanager.yml"
cat > "$TASK_STAGE/.env" <<'ENV'
COMPOSE_PROJECT_NAME=chat-server
COMPOSE_FILE=docker-compose.yml:docker-compose.production.yml:docker-compose.server.yml
COMPOSE_PROFILES=observability
ENV
cat > "$TASK_STAGE/docker-compose.server.yml" <<'COMPOSE'
services:
  postgresql:
    image: chat-server-postgres:17.11
  migrate:
    image: chat-server-python:0.1.0
    volumes:
      - ./server-config/settings.toml:/etc/chat/settings.toml:ro
  python:
    image: chat-server-python:0.1.0
    volumes:
      - ./server-config/settings.toml:/etc/chat/settings.toml:ro
  worker:
    image: chat-server-python:0.1.0
    volumes:
      - ./server-config/settings.toml:/etc/chat/settings.toml:ro
  caddy:
    image: chat-server-caddy:0.1.0
    volumes:
      - ./server-config/Caddyfile:/etc/caddy/Caddyfile:ro
  prometheus:
    restart: unless-stopped
    volumes:
      - ./server-config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
  alertmanager:
    restart: unless-stopped
    volumes:
      - ./server-config/alertmanager.yml:/etc/alertmanager/config.yml:ro
  node-exporter:
    restart: unless-stopped
  blackbox-exporter:
    image: chat-server-blackbox:0.28.0
    restart: unless-stopped
  backup:
    image: chat-server-backup:0.1.0
    build:
      context: ./python
      target: backup
    profiles: [operations]
    working_dir: /app
    user: "1000:1000"
    init: true
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    ulimits:
      core: 0
    tmpfs:
      - /run/chat-backup:uid=1000,gid=1000,size=512m,mode=0700
      - /tmp:size=16m,mode=1777
    volumes:
      - ./python:/app:ro
      - ./operations:/operations:ro
      - ./backups:/backups
      - ./artifacts/operations:/metrics
      - ./server-operator/backup.key:/run/secrets/backup_key:ro
    secrets: [database_url]
    environment:
      PYTHONPATH: /app
      CHAT_BACKUP_DIRECT_DSN: "1"
      DATABASE_URL_FILE: /run/secrets/database_url
      CHAT_BACKUP_KEY_FILE: /run/secrets/backup_key
      CHAT_BACKUP_DIR: /backups
      CHAT_BACKUP_METRICS_DIR: /metrics
      CHAT_PG_BIN: /opt/pg-client/bin
      CHAT_RESTORE_TMP: /run/chat-backup
    networks: [backend]
    command: [python, /operations/backup_job.py, backup]
COMPOSE
mkdir -p "$TASK_STAGE/server-config/systemd"
cat > "$TASK_STAGE/server-config/systemd/chat-stack.service" <<UNIT
[Unit]
Description=Chat HTTPS y dependencias
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$TASK_TARGET
ExecStart=$TASK_DOCKER_BIN compose up -d --no-build --wait --wait-timeout 240
ExecStop=$TASK_DOCKER_BIN compose stop
TimeoutStartSec=300
TimeoutStopSec=90
[Install]
WantedBy=multi-user.target
UNIT
for task_action in backup restore-drill; do
    task_mode=backup; [[ "$task_action" != restore-drill ]] || task_mode=drill
    cat > "$TASK_STAGE/server-config/systemd/chat-$task_action.service" <<UNIT
[Unit]
Description=Chat $task_action cifrado en contenedor
After=chat-stack.service
Requires=chat-stack.service
[Service]
Type=oneshot
WorkingDirectory=$TASK_TARGET
ExecStart=$TASK_DOCKER_BIN compose run --rm --no-deps backup python /operations/backup_job.py $task_mode
TimeoutStartSec=2h
UMask=0077
LimitCORE=0
UNIT
    install -m 0644 "$TASK_STAGE/operations/systemd/chat-$task_action.timer" "$TASK_STAGE/server-config/systemd/chat-$task_action.timer"
done
install -m 0644 "$TASK_STAGE"/operations/systemd/chat-log-retention.{service,timer} "$TASK_STAGE/server-config/systemd/"
printf 'Instalación generada para https://%s\nFuente Git: %s\n' "$TASK_DOMAIN" "$TASK_RELEASE" > "$TASK_STAGE/INSTALLATION.txt"
chmod 0755 "$TASK_STAGE"
chmod 0700 "$TASK_STAGE/secrets" "$TASK_STAGE/server-operator" "$TASK_STAGE/backups"
chmod 0755 "$TASK_STAGE/secrets/chat-public" "$TASK_STAGE/secrets/bootstrap-public" "$TASK_STAGE/artifacts" "$TASK_STAGE/artifacts/operations" "$TASK_STAGE/server-config"
for task_secret in "$TASK_STAGE"/secrets/* "$TASK_STAGE/server-operator"/*; do
    [[ ! -f "$task_secret" ]] || chmod 0600 "$task_secret"
done
chmod 0644 "$TASK_STAGE"/secrets/chat-public/*.pem "$TASK_STAGE"/secrets/bootstrap-public/*.pem
chmod -R u=rwX,go=rX "$TASK_STAGE/server-config"
mv -T -- "$TASK_STAGE" "$TASK_TARGET"; TASK_STAGE=
unset TASK_SMTP TASK_SMTP_PASSWORD task_userinfo
if (( TASK_PREPARE )); then
    printf 'Preparación terminada en %s. No se instalaron servicios ni se contactó SMTP.\n' "$TASK_TARGET"
    exit 0
fi
chown root:1000 "$TASK_TARGET"/secrets/{database_url,redis_password,redis_url,chat_jwt_private.pem,invitation_hmac,vapid_private.pem,smtp_url}
chmod 0640 "$TASK_TARGET"/secrets/{database_url,redis_password,redis_url,chat_jwt_private.pem,invitation_hmac,vapid_private.pem,smtp_url}
chown root:70 "$TASK_TARGET/secrets/postgres_password"; chmod 0640 "$TASK_TARGET/secrets/postgres_password"
chown root:65534 "$TASK_TARGET/secrets/alertmanager_smtp_password"; chmod 0640 "$TASK_TARGET/secrets/alertmanager_smtp_password"
chown root:1000 "$TASK_TARGET/server-operator/backup.key"; chmod 0640 "$TASK_TARGET/server-operator/backup.key"
chown 1000:1000 "$TASK_TARGET/backups" "$TASK_TARGET/artifacts/operations"
cd -- "$TASK_TARGET"
compose() { docker compose --project-directory "$TASK_TARGET" "$@"; }
compose config --quiet
install -d -m 0755 /etc/systemd/journald.conf.d
install -m 0644 operations/systemd/journald-chat.conf /etc/systemd/journald.conf.d/chat.conf
systemctl restart systemd-journald
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow 80/tcp; ufw allow 443/tcp
elif command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http; firewall-cmd --permanent --add-service=https; firewall-cmd --reload
fi
compose build postgresql python caddy blackbox-exporter backup
# Configuración, TLS y autenticación SMTP; no enviar mensajes ni crear cuentas.
compose run --rm --no-deps --entrypoint python python -c '
import smtplib, ssl, sys
from urllib.parse import urlsplit, unquote
try:
    from chat.config import load_settings, validate_secrets, read_secret
    validate_secrets(load_settings())
    u = urlsplit(read_secret("SMTP_URL")); ctx = ssl.create_default_context()
    server = smtplib.SMTP_SSL(u.hostname, u.port or 465, timeout=10, context=ctx) if u.scheme == "smtps" else smtplib.SMTP(u.hostname, u.port or 587, timeout=10)
    with server:
        if u.scheme == "smtp": server.starttls(context=ctx)
        if u.username is not None: server.login(unquote(u.username), unquote(u.password or ""))
except Exception:
    sys.exit(1)
' >/dev/null 2>&1 || fail 'No se validó configuración/TLS/autenticación SMTP. Corrige el proveedor; no se arrancó la web.'
compose run --rm migrate
compose up -d --wait --wait-timeout 240
compose run --rm --no-deps backup python /operations/backup_job.py backup
compose run --rm --no-deps backup python /operations/backup_job.py drill
install -m 0644 server-config/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chat-stack.service chat-backup.timer chat-restore-drill.timer chat-log-retention.timer
task_https_ok=0
for ((task_attempt=0; task_attempt<90; task_attempt++)); do
    if curl --fail --silent --show-error --max-time 5 --resolve "$TASK_DOMAIN:443:127.0.0.1" "https://$TASK_DOMAIN/health/live" >/dev/null 2>&1; then task_https_ok=1; break; fi
    sleep 2
done
(( task_https_ok )) || fail 'Servicios arrancados, pero HTTPS aún no está disponible. Revisa DNS y puertos 80/443; no se declara éxito.'
printf 'Chat disponible en https://%s/\nRegistro habilitado con tu SMTP. Guarda las claves de server-operator fuera del servidor.\n' "$TASK_DOMAIN"
