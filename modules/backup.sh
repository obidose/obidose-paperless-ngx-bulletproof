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
: "${RETENTION_CLASS:=auto}"                        # auto|daily|weekly|monthly

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

START_TIME="$(date --iso-8601=seconds)"

if [ "${RETENTION_CLASS}" = "auto" ]; then
  if [ "$(date +%d)" = "01" ]; then
    RETENTION_CLASS="monthly"
  elif [ "$(date +%u)" = "7" ]; then
    RETENTION_CLASS="weekly"
  else
    RETENTION_CLASS="daily"
  fi
fi

say "Retention class: ${RETENTION_CLASS}"

# -------- incremental state --------
STATE_DIR="${STACK_DIR}/.backup_state"
mkdir -p "$STATE_DIR"
SNAR_DATA="${STATE_DIR}/data.snar"
SNAR_MEDIA="${STATE_DIR}/media.snar"
SNAR_EXPORT="${STATE_DIR}/export.snar"
LAST_SNAP_FILE="${STATE_DIR}/last"
PARENT=""
if [ "$RETENTION_CLASS" = "monthly" ]; then
  rm -f "$SNAR_DATA" "$SNAR_MEDIA" "$SNAR_EXPORT"
else
  PARENT="$(cat "$LAST_SNAP_FILE" 2>/dev/null || echo '')"
fi

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
  local src="$1" name="$2" snar=""
  case "$name" in
    media)  snar="$SNAR_MEDIA" ;;
    data)   snar="$SNAR_DATA" ;;
    export) snar="$SNAR_EXPORT" ;;
  esac
  if [ -d "$src" ]; then
    say "Archiving ${name}…"
    tar --listed-incremental="$snar" -C "$(dirname "$src")" -czf "${WORK}/${name}.tar.gz" "$(basename "$src")"
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

# -------- capture Paperless-NGX version --------
if VERSION=$(cd "$STACK_DIR" && docker compose ps -q paperless \
  | xargs -r docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null); then
  if [ -n "$VERSION" ]; then
    say "Recording Paperless-NGX version ${VERSION}"
    echo "$VERSION" > "${WORK}/paperless.version"
  else
    warn "Paperless-NGX version not found."
  fi
else
  warn "Failed to determine Paperless-NGX version."
fi

# Capture Postgres version (if label present)
if DB_VERSION=$(cd "$STACK_DIR" && docker compose ps -q db \
  | xargs -r docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null); then
  if [ -n "$DB_VERSION" ]; then
    say "Recording Postgres version ${DB_VERSION}"
  else
    warn "Postgres version not found."
  fi
else
  warn "Failed to determine Postgres version."
fi

# Image digests
APP_DIGEST=""
if APP_CONT=$(cd "$STACK_DIR" && docker compose ps -q paperless 2>/dev/null); then
  APP_DIGEST=$(docker inspect --format '{{index .RepoDigests 0}}' "$APP_CONT" 2>/dev/null || docker inspect --format '{{.Image}}' "$APP_CONT" 2>/dev/null || true)
fi
DB_DIGEST=""
if DB_CONT=$(cd "$STACK_DIR" && docker compose ps -q db 2>/dev/null); then
  DB_DIGEST=$(docker inspect --format '{{index .RepoDigests 0}}' "$DB_CONT" 2>/dev/null || docker inspect --format '{{.Image}}' "$DB_CONT" 2>/dev/null || true)
fi

# Compose digest (if snapshot exists)
COMPOSE_DIGEST=""
if [ -f "${WORK}/compose.snapshot.yml" ]; then
  COMPOSE_DIGEST="$(sha256sum "${WORK}/compose.snapshot.yml" | awk '{print $1}')"
fi

# -------- manifest --------
generate_manifest(){
  local manifest="${WORK}/manifest.yaml"
  local end_time="$(date --iso-8601=seconds)"
  {
    echo "started: ${START_TIME}"
    echo "ended: ${end_time}"
    echo "retention: ${RETENTION_CLASS}"
    if [ "$RETENTION_CLASS" = "monthly" ]; then
      echo "mode: full"
    else
      echo "mode: incremental"
      [ -n "$PARENT" ] && echo "parent: ${PARENT}"
    fi
    echo "host:"
    echo "  name: $(hostname)"
    echo "  kernel: $(uname -srm)"
    echo "versions:"
    echo "  paperless:"
    [ -n "$VERSION" ] && echo "    version: ${VERSION}"
    [ -n "$APP_DIGEST" ] && echo "    digest: ${APP_DIGEST}"
    echo "  postgres:"
    [ -n "$DB_VERSION" ] && echo "    version: ${DB_VERSION}"
    [ -n "$DB_DIGEST" ] && echo "    digest: ${DB_DIGEST}"
    [ -n "$COMPOSE_DIGEST" ] && {
      echo "  compose:"
      echo "    digest: ${COMPOSE_DIGEST}"
    }
    echo "files:"
    for f in "${WORK}"/*; do
      [ -f "$f" ] || continue
      local base="$(basename "$f")"
      [[ "$base" == "manifest.yaml" ]] && continue
      local size="$(stat -c %s "$f" 2>/dev/null || echo 0)"
      local sha="$(sha256sum "$f" | awk '{print $1}')"
      echo "  ${base}:"
      echo "    size: ${size}"
      echo "    sha256: ${sha}"
    done
  } > "$manifest"
}
generate_manifest

# -------- push to remote --------
DEST="${REMOTE}/${SNAP}"
say "Uploading snapshot to ${DEST}"
rclone copy "${WORK}" "${DEST}" --fast-list

# -------- verify --------
if ! rclone lsf "${DEST}" --files-only >/dev/null 2>&1; then
  die "Upload verification failed."
fi
ok "Snapshot uploaded: ${DEST}"

# remember snapshot for incremental chain
echo "$SNAP" > "$LAST_SNAP_FILE"

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
