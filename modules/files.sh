#!/usr/bin/env bash
set -Eeuo pipefail

# This module writes the .env and docker-compose.yml, installs the backup
# script + cron, and controls bringing the stack up / showing status.

# ---------- write .env ----------
write_env_file() {
  log "Writing ${ENV_FILE}"
  # Decide PUBLIC URL
  if [ "${ENABLE_TRAEFIK:-yes}" = "yes" ]; then
    PAPERLESS_URL="https://${DOMAIN}"
  else
    PAPERLESS_URL="http://localhost:${HTTP_PORT}"
  fi

  mkdir -p "$(dirname "$ENV_FILE")"
  cat > "$ENV_FILE" <<EOF
# Generated: $(date -Is)
# Instance
INSTANCE_NAME=${INSTANCE_NAME}
STACK_DIR=${STACK_DIR}
DATA_ROOT=${DATA_ROOT}

# IDs & timezone
PUID=${PUID}
PGID=${PGID}
TZ=${TZ}

# Paperless admin
PAPERLESS_ADMIN_USER=${PAPERLESS_ADMIN_USER}
PAPERLESS_ADMIN_PASSWORD=${PAPERLESS_ADMIN_PASSWORD}
PAPERLESS_URL=${PAPERLESS_URL}

# Postgres
POSTGRES_VERSION=${POSTGRES_VERSION}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# Exposure
ENABLE_TRAEFIK=${ENABLE_TRAEFIK}
DOMAIN=${DOMAIN}
LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL}
HTTP_PORT=${HTTP_PORT}

# Backups (rclone)
RCLONE_REMOTE_NAME=${RCLONE_REMOTE_NAME}
RCLONE_REMOTE_PATH=${RCLONE_REMOTE_PATH}
RETENTION_DAYS=${RETENTION_DAYS}
EOF
}

# ---------- write docker-compose.yml ----------
write_compose_file() {
  log "Writing ${COMPOSE_FILE} (Traefik=${ENABLE_TRAEFIK})"
  mkdir -p "$(dirname "$COMPOSE_FILE")"

  if [ "${ENABLE_TRAEFIK}" = "yes" ]; then
    cat > "$COMPOSE_FILE" <<YAML
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server","--save","60","1","--loglevel","warning"]
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
    command: ["gotenberg","--chromium-disable-javascript=true"]
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
      PUID: ${PUID}
      PGID: ${PGID}
      TZ: ${TZ}
      PAPERLESS_REDIS: redis://redis:6379
      PAPERLESS_DBHOST: db
      PAPERLESS_DBPORT: 5432
      PAPERLESS_DBNAME: ${POSTGRES_DB}
      PAPERLESS_DBUSER: ${POSTGRES_USER}
      PAPERLESS_DBPASS: ${POSTGRES_PASSWORD}
      PAPERLESS_ADMIN_USER: ${PAPERLESS_ADMIN_USER}
      PAPERLESS_ADMIN_PASSWORD: ${PAPERLESS_ADMIN_PASSWORD}
      PAPERLESS_URL: \${PAPERLESS_URL}
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
    mkdir -p "${STACK_DIR}/letsencrypt"
    touch "${STACK_DIR}/letsencrypt/acme.json"
    chmod 600 "${STACK_DIR}/letsencrypt/acme.json"
  else
    cat > "$COMPOSE_FILE" <<YAML
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server","--save","60","1","--loglevel","warning"]
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
    command: ["gotenberg","--chromium-disable-javascript=true"]
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
      PUID: ${PUID}
      PGID: ${PGID}
      TZ: ${TZ}
      PAPERLESS_REDIS: redis://redis:6379
      PAPERLESS_DBHOST: db
      PAPERLESS_DBPORT: 5432
      PAPERLESS_DBNAME: ${POSTGRES_DB}
      PAPERLESS_DBUSER: ${POSTGRES_USER}
      PAPERLESS_DBPASS: ${POSTGRES_PASSWORD}
      PAPERLESS_ADMIN_USER: ${PAPERLESS_ADMIN_USER}
      PAPERLESS_ADMIN_PASSWORD: ${PAPERLESS_ADMIN_PASSWORD}
      PAPERLESS_URL: \${PAPERLESS_URL}
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

# ---------- backup script ----------
write_backup_script() {
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

# ---------- cron ----------
install_cron_backup() {
  log "Installing daily cron for backups (${CRON_TIME})"
  local cronline="${CRON_TIME} root ${STACK_DIR}/backup.sh >> ${STACK_DIR}/backup.log 2>&1"
  if ! grep -Fq "${STACK_DIR}/backup.sh" /etc/crontab; then
    echo "$cronline" >> /etc/crontab
    systemctl restart cron
  else
    log "Cron line already present."
  fi
}

# ---------- stack control ----------
bring_up_stack() {
  log "Starting containers…"
  (cd "$STACK_DIR" && docker compose --env-file "$ENV_FILE" up -d)
}

show_status() {
  # Load values in case we’re in a fresh shell
  [ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a || true
  local url
  if [ "${ENABLE_TRAEFIK:-yes}" = "yes" ]; then
    url="https://${DOMAIN}"
  else
    url="http://localhost:${HTTP_PORT}"
  fi
  echo
  ok "Paperless-ngx should be reachable at: ${url}"
}
