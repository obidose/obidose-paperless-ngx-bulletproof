#!/usr/bin/env bash
set -Eeuo pipefail

# install_cron_backup
# - Installs /usr/local/bin/paperless-backup (idempotent)
# - Creates /etc/cron.d/paperless-backup using CRON_TIME from .env (defaults kept)
# - Backs up: media/, data/, export/, postgres.sql, .env(.enc), compose snapshot
# - Uploads to: ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}/${YYYYmmdd-HHMMSS}
# - Retention: deletes snapshots older than ${RETENTION_DAYS} by name (lexical date compare)
install_cron_backup() {
  # Expect these vars already exported by common.sh and written to .env by files.sh
  local ENV_FILE="${ENV_FILE:-/home/docker/paperless-setup/.env}"
  local COMPOSE_FILE="${COMPOSE_FILE:-/home/docker/paperless-setup/docker-compose.yml}"
  local STACK_DIR_DEFAULT="/home/docker/paperless-setup"
  local DATA_ROOT_DEFAULT="/home/docker/paperless"

  # Create runner script
  install -d /usr/local/bin
  cat >/usr/local/bin/paperless-backup <<'BACKUP'
#!/usr/bin/env bash
set -Eeuo pipefail

# --- static fallbacks ---
STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"
ENV_FILE="${ENV_FILE:-/home/docker/paperless-setup/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-/home/docker/paperless-setup/docker-compose.yml}"

# Load environment if present (populates RCLONE_*, POSTGRES_*, RETENTION_DAYS, etc.)
if [ -r "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# sane defaults if missing
RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
ENV_BACKUP_MODE="${ENV_BACKUP_MODE:-openssl}"        # none|plain|openssl
ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"
INCLUDE_COMPOSE_IN_BACKUP="${INCLUDE_COMPOSE_IN_BACKUP:-yes}"

# Derived paths
DIR_EXPORT="${DIR_EXPORT:-${DATA_ROOT}/export}"
DIR_MEDIA="${DIR_MEDIA:-${DATA_ROOT}/media}"
DIR_DATA="${DIR_DATA:-${DATA_ROOT}/data}"

log(){ echo "[$(date -u +%FT%TZ)] $*"; }

REMOTE_BASE="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
SNAP="$(date -u +%Y%m%d-%H%M%S)"
TMP="${STACK_DIR}/_backup_tmp/${SNAP}"
OUT="${TMP}"

mkdir -p "$OUT"

# 1) DB dump
if command -v docker >/dev/null 2>&1; then
  log "Dumping PostgreSQL…"
  : "${POSTGRES_DB:=paperless}"
  : "${POSTGRES_USER:=paperless}"
  if [ -n "${POSTGRES_PASSWORD:-}" ]; then
    PGPASSWORD="${POSTGRES_PASSWORD}" docker compose -f "$COMPOSE_FILE" exec -T db \
      pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "${OUT}/postgres.sql"
  else
    docker compose -f "$COMPOSE_FILE" exec -T db \
      pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "${OUT}/postgres.sql"
  fi
else
  log "WARNING: docker not found; skipping DB dump."
fi

# 2) Tar data dirs (skip if missing)
pack_dir(){ local name="$1" path="$2"; if [ -d "$path" ]; then tar -C "$(dirname "$path")" -czf "${OUT}/${name}.tar.gz" "$(basename "$path")"; fi; }
log "Packing media/data/export…"
pack_dir media  "$DIR_MEDIA"
pack_dir data   "$DIR_DATA"
pack_dir export "$DIR_EXPORT"

# 3) Backup .env (encrypted/plain/skip)
if [ -r "$ENV_FILE" ]; then
  case "${ENV_BACKUP_MODE}" in
    openssl)
      if [ -r "$ENV_BACKUP_PASSPHRASE_FILE" ]; then
        log "Encrypting .env → .env.enc"
        openssl enc -aes-256-cbc -md sha256 -pbkdf2 -salt \
          -in "$ENV_FILE" -out "${OUT}/.env.enc" \
          -pass "file:${ENV_BACKUP_PASSPHRASE_FILE}"
      else
        log "WARNING: passphrase file missing; falling back to plain .env"
        cp -f "$ENV_FILE" "${OUT}/.env"
      fi
      ;;
    plain)
      cp -f "$ENV_FILE" "${OUT}/.env"
      ;;
    none|*)
      : # skip
      ;;
  esac
fi

# 4) Compose snapshot
if [ "${INCLUDE_COMPOSE_IN_BACKUP}" = "yes" ] && [ -r "$COMPOSE_FILE" ]; then
  cp -f "$COMPOSE_FILE" "${OUT}/compose.snapshot.yml"
fi

# 5) Upload to cloud
log "Uploading snapshot ${SNAP} to ${REMOTE_BASE}…"
rclone mkdir "${REMOTE_BASE}" >/dev/null 2>&1 || true
rclone copy "${OUT}" "${REMOTE_BASE}/${SNAP}" --fast-list --transfers=4 --checkers=8

# 6) Retention by age using lexicographic date in name
if [ -n "${RETENTION_DAYS}" ]; then
  THRESHOLD="$(date -u -d "${RETENTION_DAYS} days ago" +%Y%m%d-%H%M%S)"
  log "Applying retention: delete snapshots older than ${RETENTION_DAYS}d (name < ${THRESHOLD})"
  rclone lsf --dirs-only "${REMOTE_BASE}" | sed 's:/$::' | while read -r d; do
    [[ "$d" =~ ^[0-9]{8}-[0-9]{6}$ ]] || continue
    if [[ "$d" < "$THRESHOLD" ]]; then
      log "Purging ${d}"
      rclone purge "${REMOTE_BASE}/${d}" || true
    fi
  done
fi

# 7) Cleanup tmp
rm -rf "${STACK_DIR}/_backup_tmp/${SNAP}"
log "Backup done."
BACKUP
  chmod +x /usr/local/bin/paperless-backup

  # Create /etc/cron.d entry (root, flock-protected)
  local CRON_FILE="/etc/cron.d/paperless-backup"
  # Pull CRON_TIME from current env (already set in common.sh), fallback if empty
  local _CRON="${CRON_TIME:-30 3 * * *}"
  cat >"$CRON_FILE" <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${_CRON} root flock -n /var/lock/paperless-backup.lock /usr/local/bin/paperless-backup >>/var/log/paperless-backup.log 2>&1
CRON
  chmod 0644 "$CRON_FILE"
  systemctl restart cron >/dev/null 2>&1 || service cron restart >/dev/null 2>&1 || true

  ok "Installed backup cron (${_CRON}) and /usr/local/bin/paperless-backup"
}
