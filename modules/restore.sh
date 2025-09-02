#!/usr/bin/env bash
set -euo pipefail

# --- Defaults (overridden by .env) ---
STACK_DIR_DEFAULT="/home/docker/paperless-setup"
DATA_ROOT_DEFAULT="/home/docker/paperless"
RCLONE_REMOTE_NAME_DEFAULT="pcloud"

# --- Locate stack + .env ---
STACK_DIR="${STACK_DIR:-${STACK_DIR_DEFAULT}}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a; . "$ENV_FILE"; set +a
fi

# --- Resolve basics from env or sensible defaults ---
STACK_DIR="${STACK_DIR:-${STACK_DIR_DEFAULT}}"
DATA_ROOT="${DATA_ROOT:-${DATA_ROOT_DEFAULT}}"
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-${RCLONE_REMOTE_NAME_DEFAULT}}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"

# Optional encryption settings for .env backup
ENV_BACKUP_MODE="${ENV_BACKUP_MODE:-openssl}"   # openssl|plain|none
ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"

# Optional compose handling
USE_COMPOSE_SNAPSHOT="${USE_COMPOSE_SNAPSHOT:-no}"   # yes to replace docker-compose.yml when present

# DB vars (fall back to common defaults if not in .env)
POSTGRES_USER="${POSTGRES_USER:-paperless}"
POSTGRES_DB="${POSTGRES_DB:-paperless}"

# --- Helpers ---
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo -e "\e[1;34m==>\e[0m $*"; }
ok()   { echo -e "\e[1;32m✔\e[0m $*"; }

need() { command -v "$1" &>/dev/null || die "Missing required command: $1"; }

extract_tar() {
  local src="$1" dest="$2"
  mkdir -p "$dest"
  case "$src" in
    *.tar.zst|*.tzst)  tar --zstd -xf "$src" -C "$dest" ;;
    *.tar.gz|*.tgz)    tar -xzf "$src" -C "$dest" ;;
    *.tar)             tar -xf "$src" -C "$dest" ;;
    *)                 die "Unknown archive format: $src" ;;
  esac
}

restore_env_from_snapshot() {
  local snapdir="$1"
  if [[ -f "${snapdir}/.env" ]]; then
    cp -f "${snapdir}/.env" "${STACK_DIR}/.env"
    ok "Restored .env"
    return 0
  fi
  if [[ -f "${snapdir}/.env.enc" ]]; then
    if [[ "$ENV_BACKUP_MODE" == "openssl" ]]; then
      if [[ -f "$ENV_BACKUP_PASSPHRASE_FILE" ]]; then
        if openssl enc -aes-256-cbc -pbkdf2 -d -in "${snapdir}/.env.enc" -out "${STACK_DIR}/.env" -pass "file:${ENV_BACKUP_PASSPHRASE_FILE}"; then
          ok "Decrypted + restored .env (openssl)"
          return 0
        fi
      fi
      echo "Could not auto-decrypt .env.enc."
      echo "If you know the passphrase, run:"
      echo "  openssl enc -aes-256-cbc -pbkdf2 -d -in '${snapdir}/.env.enc' -out '${STACK_DIR}/.env'"
    fi
  fi
  return 1
}

# --- Checks ---
need rclone
need docker
need tar

cd "$STACK_DIR" || die "Stack dir not found: $STACK_DIR"
COMPOSE="docker compose"

# --- Choose snapshot (arg or interactive) ---
SNAP_ARG="${1:-}"
REMOTE="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"

info "Listing snapshots under ${REMOTE} ..."
# robust list of snapshot directories (folder names)
mapfile -t SNAP_DIRS < <(rclone lsf --dirs-only --format "p" "$REMOTE" 2>/dev/null || true)

if [[ ${#SNAP_DIRS[@]} -eq 0 ]]; then
  die "No snapshots found at $REMOTE. Run a backup first."
fi

if [[ -n "$SNAP_ARG" ]]; then
  SNAPSHOT="$SNAP_ARG"
else
  # pick latest by lexical order (YYYYMMDD-HHMMSS)
  LATEST="${SNAP_DIRS[-1]}"
  echo "Available snapshots:"
  printf '  %s\n' "${SNAP_DIRS[@]}"
  read -rp "Enter snapshot folder to restore [default: ${LATEST}]: " CHOICE
  SNAPSHOT="${CHOICE:-${LATEST}}"
fi

SNAP_REMOTE="${REMOTE}/${SNAPSHOT%/}"
info "Restoring snapshot: ${SNAP_REMOTE}"

WORKDIR="$(mktemp -d /tmp/paperless-restore.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

info "Syncing snapshot locally ..."
rclone sync "$SNAP_REMOTE" "$WORKDIR"

# Expected contents
DATA_TAR="$(ls -1 "$WORKDIR"/data*.tar* 2>/dev/null | head -n1 || true)"
MEDIA_TAR="$(ls -1 "$WORKDIR"/media*.tar* 2>/dev/null | head -n1 || true)"
EXPORT_TAR="$(ls -1 "$WORKDIR"/export*.tar* 2>/dev/null | head -n1 || true)"
DB_DUMP="$(ls -1 "$WORKDIR"/*.sql* 2>/dev/null | head -n1 || true)"
COMPOSE_SNAP="$WORKDIR/compose.snapshot.yml"

# --- Stop stack ---
info "Stopping stack ..."
$COMPOSE down || true

# --- Restore .env (if present in snapshot) ---
if restore_env_from_snapshot "$WORKDIR"; then
  ok ".env ready"
else
  info "Proceeding with existing ${STACK_DIR}/.env (no .env in snapshot or decryption skipped)."
fi

# Reload any new .env values
if [[ -f "$ENV_FILE" ]]; then set -a; . "$ENV_FILE"; set +a; fi

# --- Restore data trees ---
[[ -n "$DATA_TAR"   ]] && { info "Restoring data/"   ; rm -rf "${DATA_ROOT}/data"   ; mkdir -p "${DATA_ROOT}/data"   ; extract_tar "$DATA_TAR"   "${DATA_ROOT}"; }
[[ -n "$MEDIA_TAR"  ]] && { info "Restoring media/"  ; rm -rf "${DATA_ROOT}/media"  ; mkdir -p "${DATA_ROOT}/media"  ; extract_tar "$MEDIA_TAR"  "${DATA_ROOT}"; }
[[ -n "$EXPORT_TAR" ]] && { info "Restoring export/" ; rm -rf "${DATA_ROOT}/export" ; mkdir -p "${DATA_ROOT}/export" ; extract_tar "$EXPORT_TAR" "${DATA_ROOT}"; }

# --- Optional: restore compose snapshot ---
if [[ -f "$COMPOSE_SNAP" && "${USE_COMPOSE_SNAPSHOT}" == "yes" ]]; then
  info "Applying compose.snapshot.yml -> docker-compose.yml"
  cp -f "$COMPOSE_SNAP" "${STACK_DIR}/docker-compose.yml"
fi

# --- Bring up DB only for restore ---
info "Starting database container ..."
$COMPOSE up -d db

# Wait for DB to accept connections
info "Waiting for Postgres ..."
for i in {1..30}; do
  if $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1;" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# --- Restore DB if dump is present ---
if [[ -n "$DB_DUMP" ]]; then
  info "Restoring database ${POSTGRES_DB} ..."
  $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

  case "$DB_DUMP" in
    *.sql.gz)  gunzip -c "$DB_DUMP" | $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 ;;
    *.sql)     $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f - < "$DB_DUMP" ;;
    *)         die "Unknown DB dump format: $DB_DUMP" ;;
  esac
  ok "Database restored"
else
  info "No DB dump found in snapshot; skipping DB restore."
fi

# --- Bring full stack back ---
info "Starting full stack ..."
$COMPOSE up -d
ok "Restore complete."

echo
echo "Stack dir:   ${STACK_DIR}"
echo "Data root:   ${DATA_ROOT}"
echo "Snapshot:    ${SNAPSHOT%/}"
echo "Remote:      ${SNAP_REMOTE}"
echo
echo "Tip: run 'bulletproof status' and 'bulletproof logs' to verify health."
