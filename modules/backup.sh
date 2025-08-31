#!/usr/bin/env bash
set -Eeuo pipefail

# -------- mini log helpers --------
say(){  echo -e "[*] $*"; }
ok(){   echo -e "[ok] $*"; }
warn(){ echo -e "[!] $*"; }
die(){  echo -e "[x] $*"; exit 1; }

# -------- load env --------
ENV_FILE="/home/docker/paperless-setup/.env"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
else
  warn "No .env at $ENV_FILE — falling back to defaults."
fi

# -------- defaults (match installer/common.sh) --------
: "${INSTANCE_NAME:=paperless}"
: "${STACK_DIR:=/home/docker/paperless-setup}"
: "${DATA_ROOT:=/home/docker/paperless}"

: "${DIR_EXPORT:=${DATA_ROOT}/export}"
: "${DIR_MEDIA:=${DATA_ROOT}/media}"
: "${DIR_DATA:=${DATA_ROOT}/data}"

: "${COMPOSE_FILE:=${STACK_DIR}/docker-compose.yml}"

: "${RCLONE_REMOTE_NAME:=pcloud}"
: "${RCLONE_REMOTE_PATH:=backups/paperless/${INSTANCE_NAME}}"

: "${POSTGRES_DB:=paperless}"
: "${POSTGRES_USER:=paperless}"

: "${ENV_BACKUP_MODE:=openssl}"                     # none|plain|openssl
: "${ENV_BACKUP_PASSPHRASE_FILE:=/root/.paperless_env_pass}"
: "${INCLUDE_COMPOSE_IN_BACKUP:=yes}"               # yes|no
: "${RETENTION_DAYS:=30}"                           # 0 = no pruning

REMOTE="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"

# -------- preflight --------
command -v rclone >/dev/null || die "rclone not installed."
rclone about "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1 || die "pCloud remote '${RCLONE_REMOTE_NAME}:' not working."

[ -d "$STACK_DIR" ] || die "STACK_DIR not found: $STACK_DIR"
[ -f "$COMPOSE_FILE" ] || die "compose file not found: $COMPOSE_FILE"

# -------- ensure remote path exists (nested) --------
ensure_remote_path(){
  local remote_path="$1"   # e.g. pcloud:backups/paperless/paperless
  local remote="${remote_path%%:*}:"     # pcloud:
  local path="${remote_path#*:}"         # backups/paperless/paperless

  IFS='/' read -r -a parts <<<"$path"
  local acc=""
  for p in "${parts[@]}"; do
    if [ -z "$acc" ]; then acc="$p"; else acc="$acc/$p"; fi
    rclone mkdir "${remote}${acc}" >/dev/null 2>&1 || true
  done
}
ensure_remote_path "$REMOTE"

# -------- snapshot workspace --------
SNAP="$(date +%Y-%m-%d_%H-%M-%S)"
WORK="/tmp/paperless-backup.${SNAP}"
mkdir -p "$WORK"

say "Creating snapshot: $SNAP"

# -------- dump Postgres --------
dump_db(){
  say "Dumping Postgres database '${POSTGRES_DB}'…"
  if (cd "$STACK_DIR" && docker compose ps db >/dev/null 2>&1); then
    if (cd "$STACK_DIR" && docker compose exec -T db which pg_dump >/dev/null 2>&1); then
      (cd "$STACK_DIR" && docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB") > "${WORK}/postgres.sql" \
        || warn "pg_dump failed (continuing without DB dump)."
    else
      warn "pg_dump missing in db container (continuing without DB dump)."
    fi
  else
    warn "db service not running (continuing without DB dump)."
  fi
}
dump_db

# -------- tar data --------
tar_dir(){
  local src="$1" name="$2"
  if [ -d "$src" ]; then
    say "Archiving ${name}…"
    tar -C "$(dirname "$src")" -czf "${WORK}/${name}.tar.gz" "$(basename "$src")"
  else
    warn "Skip ${name}: directory not found at ${src}"
  fi
}
tar_dir "$DIR_MEDIA"   "media"
tar_dir "$DIR_DATA"    "data"
tar_dir "$DIR_EXPORT"  "export"

# -------- include env (optionally encrypted) --------
if [ -f "$ENV_FILE" ]; then
  case "$ENV_BACKUP_MODE" in
    none)
      warn "ENV_BACKUP_MODE=none — not backing up .env"
      ;;
    plain)
      say "Including plain .env"
      cp -a "$ENV_FILE" "${WORK}/.env"
      ;;
    openssl)
      if [ -f "$ENV_BACKUP_PASSPHRASE_FILE" ]; then
        say "Encrypting .env with passphrase file"
        openssl enc -aes-256-cbc -md sha256 -pbkdf2 -salt \
          -in "$ENV_FILE" -out "${WORK}/.env.enc" -pass "file:${ENV_BACKUP_PASSPHRASE_FILE}"
      else
        warn "Passphrase file missing at ${ENV_BACKUP_PASSPHRASE_FILE}; falling back to plain .env"
        cp -a "$ENV_FILE" "${WORK}/.env"
      fi
      ;;
    *)
      warn "Unknown ENV_BACKUP_MODE=${ENV_BACKUP_MODE}; copying plain .env"
      cp -a "$ENV_FILE" "${WORK}/.env"
      ;;
  esac
else
  warn "No .env found at ${ENV_FILE}"
fi

# -------- include compose snapshot --------
if [ "${INCLUDE_COMPOSE_IN_BACKUP,,}" = "yes" ]; then
  cp -a "$COMPOSE_FILE" "${WORK}/compose.snapshot.yml" || true
fi

# -------- push to remote --------
DEST="${REMOTE}/${SNAP}"
say "Uploading snapshot to ${DEST}"
rclone copy "${WORK}" "${DEST}" --fast-list

# -------- verify --------
if ! rclone lsf "${DEST}" --files-only >/dev/null 2>&1; then
  die "Upload verification failed."
fi
ok "Snapshot uploaded: ${DEST}"

# -------- retention (prune old snapshots) --------
if [[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] && [ "$RETENTION_DAYS" -gt 0 ]; then
  say "Pruning snapshots older than ${RETENTION_DAYS} days…"
  CUTOFF="$(date -u -d "-${RETENTION_DAYS} days" +%Y-%m-%d)_00-00-00"
  mapfile -t DIRS < <(rclone lsf "${REMOTE}" --dirs-only 2>/dev/null | sed 's:/$::' | sort || true)
  for d in "${DIRS[@]:-}"; do
    [[ -z "$d" ]] && continue
    if [[ "$d" < "$CUTOFF" ]]; then
      say "Deleting old snapshot: ${REMOTE}/${d}"
      rclone purge "${REMOTE}/${d}" >/dev/null 2>&1 || rclone delete "${REMOTE}/${d}" --rmdirs >/dev/null 2>&1 || true
    fi
  done
fi

# -------- cleanup --------
rm -rf "$WORK"
ok "Backup complete."
