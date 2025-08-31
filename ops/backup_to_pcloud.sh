#!/usr/bin/env bash
set -Eeuo pipefail

STACK_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$STACK_DIR/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/paperless}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"
POSTGRES_DB="${POSTGRES_DB:-paperless}"
POSTGRES_USER="${POSTGRES_USER:-paperless}"
COMPOSE_FILE="${COMPOSE_FILE:-$STACK_DIR/docker-compose.yml}"

ENV_BACKUP_MODE="${ENV_BACKUP_MODE:-openssl}" # none|plain|openssl
ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"
INCLUDE_COMPOSE_IN_BACKUP="${INCLUDE_COMPOSE_IN_BACKUP:-yes}"

log(){ echo -e "\e[1;32m[+]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }

STAMP=$(date +%F_%H%M%S)
DEST_DIR="$STACK_DIR/local_backups/$STAMP"
mkdir -p "$DEST_DIR"

log "Creating database dump…"
docker compose -f "$STACK_DIR/docker-compose.yml" exec -T db \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$DEST_DIR/postgres.sql"

log "Archiving data directories…"
for dir in media data export; do
  src="$DATA_ROOT/$dir"
  [ -d "$src" ] && tar -C "$DATA_ROOT" -czf "$DEST_DIR/${dir}.tar.gz" "$dir" || warn "Missing $src"
done

if [ "${INCLUDE_COMPOSE_IN_BACKUP}" = "yes" ] && [ -f "$COMPOSE_FILE" ]; then
  cp "$COMPOSE_FILE" "$DEST_DIR/compose.snapshot.yml"
  sha=$(sha256sum "$COMPOSE_FILE" | awk '{print $1}')
else
  sha=""
fi

# Include .env snapshot
case "$ENV_BACKUP_MODE" in
  none)  env_includes="none" ;;
  plain)
    [ -f "$ENV_FILE" ] && cp "$ENV_FILE" "$DEST_DIR/env.snapshot" || warn ".env not found"
    env_includes="plain"
    ;;
  openssl)
    if [ -f "$ENV_FILE" ]; then
      if [ -f "$ENV_BACKUP_PASSPHRASE_FILE" ]; then
        openssl enc -aes-256-cbc -pbkdf2 -salt \
          -pass file:"$ENV_BACKUP_PASSPHRASE_FILE" \
          -in "$ENV_FILE" -out "$DEST_DIR/env.snapshot.enc"
        env_includes="openssl"
      else
        warn "Passphrase file ${ENV_BACKUP_PASSPHRASE_FILE} not found; skipping env encryption."
        env_includes="none"
      fi
    else
      warn ".env not found; skipping env snapshot."
      env_includes="none"
    fi
    ;;
  *) env_includes="none" ;;
esac

log "Writing manifest…"
cat > "$DEST_DIR/manifest.json" <<JSON
{
  "timestamp": "$STAMP",
  "host": "$(hostname)",
  "paperless_url": "${PAPERLESS_URL:-}",
  "postgres_db": "$POSTGRES_DB",
  "retention_days": "$RETENTION_DAYS",
  "includes": {
    "env": "$env_includes",
    "compose": ${INCLUDE_COMPOSE_IN_BACKUP:-yes}
  },
  "compose_sha256": "${sha:-}"
}
JSON

log "Uploading to rclone remote $RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH/$STAMP …"
rclone copy "$DEST_DIR" "$RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH/$STAMP" --checksum --transfers 4 --checkers 8 --fast-list

log "Retention: deleting backups older than ${RETENTION_DAYS}d"
rclone delete "$RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH" --min-age "${RETENTION_DAYS}d" --fast-list || true
rclone rmdirs "$RCLONE_REMOTE_NAME:$RCLONE_REMOTE_PATH" --leave-root || true

log "Done."
