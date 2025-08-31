#!/usr/bin/env bash
set -Eeuo pipefail
set -o errtrace
trap 'echo -e "\e[1;31m[x]\e[0m Error at ${BASH_SOURCE}:${LINENO}: ${BASH_COMMAND}"; exit 1' ERR

# ======================================================================
# Paperless-ngx • Bulletproof One-Stop Wizard
# - Installs Docker + Compose, rclone, cron
# - Deploys Paperless (Redis, Postgres, Tika, Gotenberg)
# - Sets up pCloud backups via WebDAV (rclone)
# - Detects backups and optionally restores
# ======================================================================

# --------------------------- helpers ----------------------------------
log(){ echo -e "\e[1;32m[+]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }
err(){ echo -e "\e[1;31m[x]\e[0m $*"; exit 1; }
need_root(){ [ "$(id -u)" -eq 0 ] || err "Run as root."; }

prompt(){
  local msg="$1"; local def="${2:-}"; local out
  if [ -n "$def" ]; then
    read -r -p "$msg [$def]: " out || true
    echo "${out:-$def}"
  else
    read -r -p "$msg: " out || true
    echo "$out"
  fi
}

prompt_secret(){
  local msg="$1"; local out1 out2
  while true; do
    read -r -s -p "$msg: " out1 || true; echo
    read -r -s -p "Confirm $msg: " out2 || true; echo
    [ "$out1" = "$out2" ] && { echo "$out1"; return; }
    warn "Entries didn't match. Try again."
  done
}

confirm(){
  local msg="$1"; local def="${2:-Y}"; local ans
  case "$def" in
    Y|y) read -r -p "$msg [Y/n]: " ans || true; ans=${ans:-Y} ;;
    N|n) read -r -p "$msg [y/N]: " ans || true; ans=${ans:-N} ;;
    *)   read -r -p "$msg [y/n]: " ans || true ;;
  esac
  [[ "$ans" =~ ^[Yy]$ ]]
}

randpass(){
  set +o pipefail
  LC_ALL=C tr -dc 'A-Za-z0-9!@#%+=?' < /dev/urandom | head -c 22; echo
  set -o pipefail
}

ubuntu_version_ok(){
  . /etc/os-release
  case "$VERSION_ID" in 22.04|24.04) return 0;; *) return 1;; esac
}

# --------------------------- defaults ---------------------------------
INSTANCE_NAME="paperless"
STACK_DIR="/home/docker/paperless-setup"
DATA_ROOT="/home/docker/paperless"
TIMEZONE_DEFAULT=$(cat /etc/timezone 2>/dev/null || echo "Etc/UTC")

DOCKER_USER="docker"
DOCKER_UID=1001
DOCKER_GID=1001

ENABLE_TRAEFIK="yes"
HTTP_PORT=8000
DOMAIN="paperless.example.com"
LETSENCRYPT_EMAIL="admin@example.com"

POSTGRES_VERSION=15
POSTGRES_DB=paperless
POSTGRES_USER=paperless
POSTGRES_PASSWORD="$(randpass)"
PAPERLESS_ADMIN_USER=admin
PAPERLESS_ADMIN_PASSWORD="$(randpass)"

RCLONE_REMOTE_NAME="pcloud"
RCLONE_REMOTE_PATH_TEMPLATE="backups/paperless/${INSTANCE_NAME}"
RETENTION_DAYS=30
CRON_TIME="30 3 * * *" # daily at 03:30

COMPOSE_FILE="${STACK_DIR}/docker-compose.yml"
ENV_FILE="${STACK_DIR}/.env"
BACKUP_DIR_LOCAL="${STACK_DIR}/local_backups"
DIR_EXPORT="${DATA_ROOT}/export"
DIR_MEDIA="${DATA_ROOT}/media"
DIR_DATA="${DATA_ROOT}/data"
DIR_CONSUME="${DATA_ROOT}/consume"
DIR_DB="${DATA_ROOT}/db"
DIR_TIKA_CACHE="${DATA_ROOT}/tika-cache"

# --------------------------- installers -------------------------------
install_prereqs(){
  log "Installing prerequisites..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg lsb-release unzip tar cron \
                     software-properties-common
  if [ -f /etc/fuse.conf ] && grep -q "^#\?user_allow_other" /etc/fuse.conf; then
    sed -i 's/^#\?user_allow_other/user_allow_other/' /etc/fuse.conf || true
  fi
}

ensure_user(){
  if ! id -u "$DOCKER_USER" >/dev/null 2>&1; then
    log "Creating user $DOCKER_USER (UID=$DOCKER_UID, GID=$DOCKER_GID)"
    groupadd -g "$DOCKER_GID" "$DOCKER_USER" 2>/dev/null || true
    useradd -m -u "$DOCKER_UID" -g "$DOCKER_GID" -s /bin/bash "$DOCKER_USER"
  fi
}

install_docker(){
  if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker Engine + Compose plugin..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
  else
    log "Docker already installed."
  fi
  usermod -aG docker "$DOCKER_USER" || true
}

install_rclone(){
  if ! command -v rclone >/dev/null 2>&1; then
    log "Installing rclone..."
    curl -fsSL https://rclone.org/install.sh | bash
  else
    log "rclone already installed."
  fi
}

# --------------------------- rclone/pcloud -----------------------------
create_pcloud_remote(){
  local user="$1"; local pass_plain="$2"; local remote_name="$3"; local host="$4"
  local obscured; obscured=$(rclone obscure "$pass_plain")
  rclone config delete "$remote_name" >/dev/null 2>&1 || true
  rclone config create "$remote_name" webdav vendor other url "$host" user "$user" pass "$obscured" >/dev/null
}

setup_pcloud(){
  log "Connect to pCloud via WebDAV (if 2FA is ON, use an App Password)."
  local pc_user pc_pass host_global host_eu
  while true; do
    pc_user=$(prompt "pCloud login email")
    [ -n "$pc_user" ] && break
    warn "Email is required."
  done
  pc_pass=$(prompt_secret "pCloud password (or App Password)")

  host_eu="https://ewebdav.pcloud.com"
  host_global="https://webdav.pcloud.com"

  log "Trying EU WebDAV endpoint..."
  create_pcloud_remote "$pc_user" "$pc_pass" "$RCLONE_REMOTE_NAME" "$host_eu"
  if rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
    log "Connected to pCloud at $host_eu"
    return 0
  fi

  warn "EU endpoint failed. Trying Global endpoint..."
  create_pcloud_remote "$pc_user" "$pc_pass" "$RCLONE_REMOTE_NAME" "$host_global"
  if rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
    log "Connected to pCloud at $host_global"
    return 0
  fi

  warn "Both endpoints failed; running a verbose check:"
  rclone -vv lsd "${RCLONE_REMOTE_NAME}:" || true
  err "Could not authenticate to pCloud via WebDAV. Check email/password and 2FA (App Password if 2FA ON)."
}

find_latest_snapshot(){ rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | awk '{print $NF}' | sort | tail -n1 || true; }
list_snapshots(){ rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | awk '{print $NF}' | sort || true; }

# --------------------------- compose/env -------------------------------
prepare_dirs(){
  log "Creating directories at ${STACK_DIR} and ${DATA_ROOT}"
  mkdir -p "$STACK_DIR" "$BACKUP_DIR_LOCAL" "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"
  chown -R "$DOCKER_USER:$DOCKER_USER" "$DATA_ROOT" "$STACK_DIR"
}

write_env(){
  log "Writing ${ENV_FILE}"
  cat > "$ENV_FILE" <<EOF
# Generated: $(date -Is)
PUID=${DOCKER_UID}
PGID=${DOCKER_GID}
TZ=${TIMEZONE}

POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD='${POSTGRES_PASSWORD}'

# Paperless
PAPERLESS_ADMIN_USER=${PAPERLESS_ADMIN_USER}
PAPERLESS_ADMIN_PASSWORD='${PAPERLESS_ADMIN_PASSWORD}'
PAPERLESS_URL=${PAPERLESS_URL}

# Services
HTTP_PORT=${HTTP_PORT}
DOMAIN=${DOMAIN}
LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL}

# Backups
RCLONE_REMOTE_NAME=${RCLONE_REMOTE_NAME}
RCLONE_REMOTE_PATH=${RCLONE_REMOTE_PATH}
RETENTION_DAYS=${RETENTION_DAYS}

# Paths
DATA_ROOT=${DATA_ROOT}
STACK_DIR=${STACK_DIR}
EOF
}

write_compose(){
  log "Writing ${COMPOSE_FILE} (Traefik=${ENABLE_TRAEFIK})"
  if [ "$ENABLE_TRAEFIK" = "yes" ]; then
    cat > "$COMPOSE_FILE" <<YAML
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    networks: [paperless]

  db:
    image: postgres:${POSTGRES_VERSION}-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - ${DIR_DB}:/var/lib/postgresql/data
    networks: [paperless]

  gotenberg:
    image: gotenberg/gotenberg:8
    restart: unless-stopped
    command: ["gotenberg", "--chromium-disable-javascript=true"]
    networks: [paperless]

  tika:
    image: ghcr.io/paperless-ngx/tika:latest
    restart: unless-stopped
    volumes:
      - ${DIR_TIKA_CACHE}:/cache
    networks: [paperless]

  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    depends_on: [db, redis, gotenberg, tika]
    restart: unless-stopped
    environment:
      PUID: ${DOCKER_UID}
      PGID: ${DOCKER_GID}
      TZ: ${TIMEZONE}
      PAPERLESS_REDIS: redis://redis:6379
      PAPERLESS_DBHOST: db
      PAPERLESS_DBPORT: 5432
      PAPERLESS_DBNAME: ${POSTGRES_DB}
      PAPERLESS_DBUSER: ${POSTGRES_USER}
      PAPERLESS_DBPASS: ${POSTGRES_PASSWORD}
      PAPERLESS_ADMIN_USER: ${PAPERLESS_ADMIN_USER}
      PAPERLESS_ADMIN_PASSWORD: ${PAPERLESS_ADMIN_PASSWORD}
      PAPERLESS_URL: ${PAPERLESS_URL}
      PAPERLESS_TIKA_ENABLED: "1"
      PAPERLESS_TIKA_GOTENBERG_ENDPOINT: http://gotenberg:3000
      PAPERLESS_TIKA_ENDPOINT: http://tika:9998
      PAPERLESS_CONSUMER_POLLING: "10"
    volumes:
      - ${DIR_DATA}:/usr/src/paperless/data
      - ${DIR_MEDIA}:/usr/src/paperless/media
      - ${DIR_EXPORT}:/usr/src/paperless/export
      - ${DIR_CONSUME}:/usr/src/paperless/consume
    labels:
      - traefik.enable=true
      - traefik.http.routers.paperless.rule=Host(\`${DOMAIN}\`)
      - traefik.http.routers.paperless.entrypoints=websecure
      - traefik.http.routers.paperless.tls.certresolver=le
      - traefik.http.services.paperless.loadbalancer.server.port=8000
    networks: [paperless]

  traefik:
    image: traefik:v3.0
    restart: unless-stopped
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.le.acme.httpchallenge=true
      - --certificatesresolvers.le.acme.httpchallenge.entrypoint=web
      - --certificatesresolvers.le.acme.email=${LETSENCRYPT_EMAIL}
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
    ports:
      - 80:80
      - 443:443
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ${STACK_DIR}/letsencrypt:/letsencrypt
    networks: [paperless]

networks:
  paperless:
    name: paperless_net
YAML
  else
    cat > "$COMPOSE_FILE" <<YAML
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    networks: [paperless]

  db:
    image: postgres:${POSTGRES_VERSION}-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - ${DIR_DB}:/var/lib/postgresql/data
    networks: [paperless]

  gotenberg:
    image: gotenberg/gotenberg:8
    restart: unless-stopped
    command: ["gotenberg", "--chromium-disable-javascript=true"]
    networks: [paperless]

  tika:
    image: ghcr.io/paperless-ngx/tika:latest
    restart: unless-stopped
    volumes:
      - ${DIR_TIKA_CACHE}:/cache
    networks: [paperless]

  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    depends_on: [db, redis, gotenberg, tika]
    restart: unless-stopped
    environment:
      PUID: ${DOCKER_UID}
      PGID: ${DOCKER_GID}
      TZ: ${TIMEZONE}
      PAPERLESS_REDIS: redis://redis:6379
      PAPERLESS_DBHOST: db
      PAPERLESS_DBPORT: 5432
      PAPERLESS_DBNAME: ${POSTGRES_DB}
      PAPERLESS_DBUSER: ${POSTGRES_USER}
      PAPERLESS_DBPASS: ${POSTGRES_PASSWORD}
      PAPERLESS_ADMIN_USER: ${PAPERLESS_ADMIN_USER}
      PAPERLESS_ADMIN_PASSWORD: ${PAPERLESS_ADMIN_PASSWORD}
      PAPERLESS_URL: ${PAPERLESS_URL}
      PAPERLESS_TIKA_ENABLED: "1"
      PAPERLESS_TIKA_GOTENBERG_ENDPOINT: http://gotenberg:3000
      PAPERLESS_TIKA_ENDPOINT: http://tika:9998
      PAPERLESS_CONSUMER_POLLING: "10"
    volumes:
      - ${DIR_DATA}:/usr/src/paperless/data
      - ${DIR_MEDIA}:/usr/src/paperless/media
      - ${DIR_EXPORT}:/usr/src/paperless/export
      - ${DIR_CONSUME}:/usr/src/paperless/consume
    ports:
      - ${HTTP_PORT}:8000
    networks: [paperless]

networks:
  paperless:
    name: paperless_net
YAML
  fi
}

write_backup_script(){
  local bscript="${STACK_DIR}/backup_to_pcloud.sh"
  log "Writing backup script ${bscript}"
  cat > "$bscript" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
STACK_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$STACK_DIR/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/paperless}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"
POSTGRES_DB="${POSTGRES_DB:-paperless}"
POSTGRES_USER="${POSTGRES_USER:-paperless}"

mkdir -p "$STACK_DIR/local_backups"
STAMP=$(date +%F_%H%M%S)
DEST_DIR="$STACK_DIR/local_backups/$STAMP"
mkdir -p "$DEST_DIR"

log(){ echo -e "\e[1;32m[+]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }

log "Creating database dump..."
docker compose -f "$STACK_DIR/docker-compose.yml" exec -T db \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$DEST_DIR/postgres.sql"

log "Archiving data directories..."
for dir in media data export; do
  src="$DATA_ROOT/$dir"
  [ -d "$src" ] && tar -C "$DATA_ROOT" -czf "$DEST_DIR/${dir}.tar.gz" "$dir" || warn "Missing $src"
done

log "Writing manifest..."
cat > "$DEST_DIR/manifest.json" <<JSON
{
  "timestamp": "$STAMP",
  "host": "$(hostname)",
  "paperless_url": "${PAPERLESS_URL:-}",
  "postgres_db": "$POSTGRES_DB",
  "retention_days": "$RETENTION_DAYS"
}
JSON

log "Uploading to rclone remote $RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH/$STAMP ..."
rclone copy "$DEST_DIR" "$RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH/$STAMP" --checksum --transfers 4 --checkers 8 --fast-list

log "Retention: deleting backups older than ${RETENTION_DAYS}d"
rclone delete "$RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH" --min-age "${RETENTION_DAYS}d" --fast-list || true
rclone rmdirs "$RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH" --leave-root || true

log "Done."
BASH
  chmod +x "$bscript"
}

install_cron(){
  log "Installing daily cron for backups (${CRON_TIME})"
  local cronline="${CRON_TIME} root ${STACK_DIR}/backup_to_pcloud.sh >> ${STACK_DIR}/backup.log 2>&1"
  if ! grep -Fq "backup_to_pcloud.sh" /etc/crontab; then
    echo "$cronline" >> /etc/crontab
    systemctl restart cron
  else
    log "Cron line already present."
  fi
}

bring_up(){ log "Starting stack..."; (cd "$STACK_DIR" && docker compose --env-file "$ENV_FILE" up -d); }

restore_from_remote(){
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/restore_$SNAP"
  mkdir -p "$tmpdir"
  log "Fetching snapshot $SNAP to $tmpdir"
  rclone copy "$base/$SNAP" "$tmpdir" --fast-list

  log "Stopping stack (if running)"; (cd "$STACK_DIR" && docker compose down) || true
  log "Restoring media/data/export"
  for a in media data export; do
    [ -f "$tmpdir/${a}.tar.gz" ] && tar -C "${DATA_ROOT}" -xzf "$tmpdir/${a}.tar.gz" || warn "No archive for $a"
  done

  if [ -f "$tmpdir/postgres.sql" ]; then
    log "Starting db only to import SQL"; (cd "$STACK_DIR" && docker compose up -d db)
    sleep 8
    log "Dropping & recreating database ${POSTGRES_DB}"
    docker compose -f "$STACK_DIR/docker-compose.yml" exec -T db psql -U "$POSTGRES_USER" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${POSTGRES_DB}' AND pid <> pg_backend_pid();" || true
    docker compose -f "$STACK_DIR/docker-compose.yml" exec -T db psql -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\"; CREATE DATABASE \"${POSTGRES_DB}\";"
    log "Importing SQL dump"
    cat "$tmpdir/postgres.sql" | docker compose -f "$STACK_DIR"/docker-compose.yml exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB"
  else
    warn "No postgres.sql found in snapshot"
  fi

  log "Bringing full stack up"; (cd "$STACK_DIR" && docker compose up -d)
  log "Restore complete"
}

# --------------------------- wizard -----------------------------------
wizard(){
  echo; log "Starting Paperless-ngx setup wizard…"
  need_root
  ubuntu_version_ok || warn "Ubuntu $(. /etc/os-release; echo $VERSION_ID) detected; tested on 22.04/24.04"
  install_prereqs; ensure_user; install_docker; install_rclone

  echo; log "Press Enter to accept the [default] value, or type a custom value."
  TIMEZONE=$(prompt "Timezone (IANA, e.g., Pacific/Auckland; Enter=default)" "$TIMEZONE_DEFAULT")
  INSTANCE_NAME=$(prompt "Instance name (Enter=default)" "$INSTANCE_NAME")
  RCLONE_REMOTE_PATH_TEMPLATE="backups/paperless/${INSTANCE_NAME}"

  DATA_ROOT=$(prompt "Data root (persistent storage; Enter=default)" "$DATA_ROOT")
  STACK_DIR=$(prompt "Stack dir (where docker-compose.yml lives; Enter=default)" "$STACK_DIR")

  PAPERLESS_ADMIN_USER=$(prompt "Paperless admin username (Enter=default)" "$PAPERLESS_ADMIN_USER")
  local gen1="$PAPERLESS_ADMIN_PASSWORD"; local gen2="$POSTGRES_PASSWORD"
  PAPERLESS_ADMIN_PASSWORD=$(prompt "Paperless admin password (Enter=default)" "$gen1")
  POSTGRES_PASSWORD=$(prompt "Postgres password (Enter=default)" "$gen2")

  ENABLE_TRAEFIK=$(prompt "Enable Traefik with HTTPS? (yes/no; Enter=default)" "$ENABLE_TRAEFIK")
  if [ "$ENABLE_TRAEFIK" = "yes" ]; then
    DOMAIN=$(prompt "Domain for Paperless (DNS A/AAAA must point here; Enter=default)" "$DOMAIN")
    LETSENCRYPT_EMAIL=$(prompt "Let's Encrypt email (Enter=default)" "$LETSENCRYPT_EMAIL")
    PAPERLESS_URL="https://$DOMAIN"
  else
    HTTP_PORT=$(prompt "Bind Paperless on host port (Enter=default)" "$HTTP_PORT")
    PAPERLESS_URL="http://localhost:${HTTP_PORT}"
  fi

  RCLONE_REMOTE_NAME=$(prompt "rclone remote name (Enter=default)" "$RCLONE_REMOTE_NAME")
  RCLONE_REMOTE_PATH=$(prompt "Remote path for backups (Enter=default)" "$RCLONE_REMOTE_PATH_TEMPLATE")
  RETENTION_DAYS=$(prompt "Retention days (Enter=default)" "$RETENTION_DAYS")
  CRON_TIME=$(prompt "Backup cron (m h dom mon dow; Enter=default)" "$CRON_TIME")

  setup_pcloud
  prepare_dirs
  write_env
  write_compose
  write_backup_script
  install_cron

  local latest; latest=$(find_latest_snapshot || true)
  if [ -n "$latest" ]; then
    echo; log "Found snapshot(s) under ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
    echo "Latest: $latest"
    if confirm "Restore latest now?" Y; then
      restore_from_remote "$latest"
    else
      if confirm "List and choose a different snapshot?" N; then
        list_snapshots "$RCLONE_REMOTE_PATH" || true
        local choice; choice=$(prompt "Enter snapshot name exactly")
        [ -n "$choice" ] && restore_from_remote "$choice" || bring_up
      else
        bring_up
      fi
    fi
  else
    bring_up
  fi

  echo; log "All set! Access Paperless at: ${PAPERLESS_URL}"
}

# --------------------------- entrypoint -------------------------------
main(){ wizard "$@"; }
main "$@"
