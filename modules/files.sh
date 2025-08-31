#!/usr/bin/env bash
set -Eeuo pipefail

prepare_dirs(){
  log "Creating directories at ${STACK_DIR} and ${DATA_ROOT}"
  mkdir -p "$STACK_DIR" "$BACKUP_DIR_LOCAL" "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE" "$STACK_DIR/letsencrypt"
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
      POSTGRES_DB: "${POSTGRES_DB}"
      POSTGRES_USER: "${POSTGRES_USER}"
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
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
      PUID: "${DOCKER_UID}"
      PGID: "${DOCKER_GID}"
      TZ: "${TIMEZONE}"
      PAPERLESS_REDIS: "redis://redis:6379"
      PAPERLESS_DBHOST: "db"
      PAPERLESS_DBPORT: "5432"
      PAPERLESS_DBNAME: "${POSTGRES_DB}"
      PAPERLESS_DBUSER: "${POSTGRES_USER}"
      PAPERLESS_DBPASS: "${POSTGRES_PASSWORD}"
      PAPERLESS_ADMIN_USER: "${PAPERLESS_ADMIN_USER}"
      PAPERLESS_ADMIN_PASSWORD: "${PAPERLESS_ADMIN_PASSWORD}"
      PAPERLESS_URL: "${PAPERLESS_URL}"
      PAPERLESS_TIKA_ENABLED: "1"
      PAPERLESS_TIKA_GOTENBERG_ENDPOINT: "http://gotenberg:3000"
      PAPERLESS_TIKA_ENDPOINT: "http://tika:9998"
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
      POSTGRES_DB: "${POSTGRES_DB}"
      POSTGRES_USER: "${POSTGRES_USER}"
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
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
      PUID: "${DOCKER_UID}"
      PGID: "${DOCKER_GID}"
      TZ: "${TIMEZONE}"
      PAPERLESS_REDIS: "redis://redis:6379"
      PAPERLESS_DBHOST: "db"
      PAPERLESS_DBPORT: "5432"
      PAPERLESS_DBNAME: "${POSTGRES_DB}"
      PAPERLESS_DBUSER: "${POSTGRES_USER}"
      PAPERLESS_DBPASS: "${POSTGRES_PASSWORD}"
      PAPERLESS_ADMIN_USER: "${PAPERLESS_ADMIN_USER}"
      PAPERLESS_ADMIN_PASSWORD: "${PAPERLESS_ADMIN_PASSWORD}"
      PAPERLESS_URL: "${PAPERLESS_URL}"
      PAPERLESS_TIKA_ENABLED: "1"
      PAPERLESS_TIKA_GOTENBERG_ENDPOINT: "http://gotenberg:3000"
      PAPERLESS_TIKA_ENDPOINT: "http://tika:9998"
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

find_latest_snapshot(){ rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | awk '{print $NF}' | sort | tail -n1 || true; }
list_snapshots(){ rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | awk '{print $NF}' | sort || true; }

restore_from_remote(){
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/restore_$SNAP"
  mkdir -p "$tmpdir"
  log "Fetching snapshot $SNAP to $tmpdir"
  rclone copy "$base/$SNAP" "$tmpdir" --fast-list

  log "Stopping stack (if running)"
  (cd "$STACK_DIR" && docker compose down) || true

  log "Restoring media/data/export"
  for a in media data export; do
    [ -f "$tmpdir/${a}.tar.gz" ] && tar -C "${DATA_ROOT}" -xzf "$tmpdir/${a}.tar.gz" || warn "No archive for $a"
  done

  if [ -f "$tmpdir/postgres.sql" ]; then
    log "Starting db only to import SQL"
    (cd "$STACK_DIR" && docker compose up -d db)
    sleep 8
    log "Dropping & recreating database ${POSTGRES_DB}"
    docker compose -f "$STACK_DIR/docker-compose.yml" exec -T db \
      psql -U "$POSTGRES_USER" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${POSTGRES_DB}' AND pid <> pg_backend_pid();" || true
    docker compose -f "$STACK_DIR/docker-compose.yml" exec -T db \
      psql -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\"; CREATE DATABASE \"${POSTGRES_DB}\";"
    log "Importing SQL dump"
    cat "$tmpdir/postgres.sql" | docker compose -f "$STACK_DIR"/docker-compose.yml exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB"
  else
    warn "No postgres.sql found in snapshot"
  fi

  log "Bringing full stack up"
  (cd "$STACK_DIR" && docker compose up -d)
  log "Restore complete"
}
