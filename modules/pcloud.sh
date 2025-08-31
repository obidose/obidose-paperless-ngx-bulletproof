# modules/pcloud.sh
# pCloud (WebDAV with robust args) + API (OAuth) fallback + early restore helpers

# Defaults (allow env overrides)
RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"

STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"

COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"
ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"

# Paperless data subdirs
DIR_EXPORT="${DATA_ROOT}/export"
DIR_MEDIA="${DATA_ROOT}/media"
DIR_DATA="${DATA_ROOT}/data"
DIR_CONSUME="${DATA_ROOT}/consume"
DIR_DB="${DATA_ROOT}/db"
DIR_TIKA_CACHE="${DATA_ROOT}/tika-cache"

# ---------- helpers ----------
_pcloud_obscure() {  # stdin or arg
  if [ -n "${1:-}" ]; then printf '%s' "$1" | rclone obscure -; else rclone obscure -; fi
}

_pcloud_sanitize_oneline(){
  # strip CR/LF so rclone doesn’t see spurious newlines
  tr -d '\r\n'
}

_pcloud_config_path(){
  local conf="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
  echo "$conf"
}

# ---------- WebDAV flow ----------
_pcloud_create_webdav(){
  # args: <email> <plain_password> <remote_name> <webdav_host>
  local email pass_plain remote host
  email="$(printf '%s' "$1" | _pcloud_sanitize_oneline)"
  pass_plain="$(printf '%s' "$2" | _pcloud_sanitize_oneline)"
  remote="$3"
  host="$4"
  local obscured
  obscured="$(_pcloud_obscure "$pass_plain")"

  rclone config delete "$remote" >/dev/null 2>&1 || true

  # Use `--` so values starting with '-' are not parsed as flags
  rclone config create "$remote" webdav --non-interactive -- \
    vendor other url "$host" user "$email" pass "$obscured" >/dev/null
}

_pcloud_test_remote(){
  local remote="$1"
  rclone lsd "${remote}:" >/dev/null 2>&1
}

_pcloud_try_webdav(){
  local email="$1" pass="$2" remote="$3"
  local host_eu="https://ewebdav.pcloud.com"
  local host_global="https://webdav.pcloud.com"

  say "Trying EU WebDAV endpoint…"
  _pcloud_create_webdav "$email" "$pass" "$remote" "$host_eu"
  if _pcloud_test_remote "$remote"; then
    ok "Connected to pCloud at ${host_eu}"
    echo "$remote"
    return 0
  fi

  warn "EU endpoint failed. Trying Global endpoint…"
  _pcloud_create_webdav "$email" "$pass" "$remote" "$host_global"
  if _pcloud_test_remote "$remote"; then
    ok "Connected to pCloud at ${host_global}"
    echo "$remote"
    return 0
  fi

  return 1
}

# ---------- API (OAuth) flow ----------
_pcloud_create_api(){
  # args: <remote_name> <token_json>
  local remote="$1" token_json="$2"
  rclone config delete "$remote" >/dev/null 2>&1 || true
  # Token must be a single argument; we ensure no CR/LF
  token_json="$(printf '%s' "$token_json" | _pcloud_sanitize_oneline)"
  rclone config create "$remote" pcloud --non-interactive -- token "$token_json" >/dev/null
}

_pcloud_try_api_interactive(){
  local remote="pcloud_api" token
  echo
  say "Switching to pCloud API (OAuth) — most reliable. Steps:"
  echo "  1) On any device with a browser, run:  rclone authorize \"pcloud\""
  echo "  2) Approve in pCloud, copy the JSON token it prints."
  echo "  3) Paste the token JSON (single line) here."
  echo
  read -r -p "Paste token JSON: " token
  if [ -z "$token" ]; then
    warn "No token provided."
    return 1
  fi
  _pcloud_create_api "$remote" "$token"
  if _pcloud_test_remote "$remote"; then
    ok "Connected to pCloud via API."
    # point the rest of the installer to the API remote
    RCLONE_REMOTE_NAME="$remote"
    export RCLONE_REMOTE_NAME
    return 0
  fi
  warn "API token didn’t work. (Check token copy/paste.)"
  return 1
}

# ---------- snapshots ----------
_pcloud_list_snapshots() {
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null \
    | awk '{print $NF}' | sort
}

_pcloud_latest_snapshot() { _pcloud_list_snapshots | tail -n1; }

# ---------- early restore ----------
_pcloud_restore_from_snapshot() {
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/_restore/${SNAP}"

  [ -z "$SNAP" ] && die "Internal error: empty snapshot name passed to restore."

  mkdir -p "$STACK_DIR" "$DATA_ROOT" "$tmpdir" \
           "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"

  say "Fetching snapshot ${SNAP} to ${tmpdir}"
  rclone copy "${base}/${SNAP}" "$tmpdir" --fast-list

  # Restore .env if present (prefer encrypted)
  if [ -f "$tmpdir/.env.enc" ]; then
    say "Found encrypted .env.enc in snapshot."
    if [ -f "$ENV_BACKUP_PASSPHRASE_FILE" ]; then
      say "Decrypting .env using passphrase file: ${ENV_BACKUP_PASSPHRASE_FILE}"
      if ! openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
           -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
           -pass "file:${ENV_BACKUP_PASSPHRASE_FILE}"; then
         warn "Decryption with passphrase file failed."
         read -r -s -p "Enter passphrase to decrypt .env: " _pp; echo
         openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
           -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
           -pass "pass:${_pp}" || die "Failed to decrypt .env.enc with provided passphrase."
      fi
    else
      warn "No passphrase file at ${ENV_BACKUP_PASSPHRASE_FILE}."
      read -r -s -p "Enter passphrase to decrypt .env: " _pp2; echo
      openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
        -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
        -pass "pass:${_pp2}" || die "Failed to decrypt .env.enc with provided passphrase."
    fi
    ok "Decrypted .env to ${ENV_FILE}"
  elif [ -f "$tmpdir/.env" ]; then
    say "Found plain .env in snapshot; restoring."
    cp -f "$tmpdir/.env" "$ENV_FILE"
  else
    warn "No .env found in snapshot; the wizard will prompt for missing values later."
  fi

  # Restore compose if packaged
  if [ -f "$tmpdir/compose.snapshot.yml" ]; then
    say "Found compose.snapshot.yml; using it as docker-compose.yml"
    cp -f "$tmpdir/compose.snapshot.yml" "$COMPOSE_FILE"
  fi

  # Stop stack (ignore errors)
  (cd "$STACK_DIR" && docker compose down) >/dev/null 2>&1 || true

  say "Restoring media/data/export archives (if present)…"
  for a in media data export; do
    [ -f "$tmpdir/${a}.tar.gz" ] && tar -C "${DATA_ROOT}" -xzf "$tmpdir/${a}.tar.gz" && ok "Restored ${a}.tar.gz"
  done

  if [ -f "$tmpdir/postgres.sql" ]; then
    say "Starting database to import SQL…"
    (cd "$STACK_DIR" && docker compose up -d db)
    sleep 8
    local DBNAME DBUSER
    DBNAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 'paperless')"
    DBUSER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 'paperless')"
    say "Dropping & recreating database ${DBNAME}"
    docker compose -f "$COMPOSE_FILE" exec -T db \
      psql -U "$DBUSER" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DBNAME}' AND pid <> pg_backend_pid();" || true
    docker compose -f "$COMPOSE_FILE" exec -T db \
      psql -U "$DBUSER" -c "DROP DATABASE IF EXISTS \"${DBNAME}\"; CREATE DATABASE \"${DBNAME}\";"
    say "Importing SQL dump…"
    cat "$tmpdir/postgres.sql" | docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DBUSER" "$DBNAME"
    ok "Database restored."
  fi

  say "Bringing full stack up…"
  (cd "$STACK_DIR" && docker compose up -d)
  ok "Restore complete."
  exit 0
}

# ---------- public entry points used by install.sh ----------
setup_pcloud_remote_interactive() {
  say "Connect to pCloud via WebDAV (if 2FA is ON, use an App Password)."
  local email pass conf
  conf="$(_pcloud_config_path)"
  while true; do
    read -r -p "pCloud login email: " email
    [ -z "$email" ] && { warn "Email is required."; continue; }
    read -r -s -p "pCloud password (or App Password): " pass; echo

    if _pcloud_try_webdav "$email" "$pass" "$RCLONE_REMOTE_NAME"; then
      ok "rclone config path: $conf"
      return 0
    fi

    warn "Authentication failed on both endpoints. If 2FA is ON, use an App Password."
    ok "rclone config path: $conf"
    if confirm "Use pCloud API (OAuth) instead? (recommended)" "Y"; then
      if _pcloud_try_api_interactive; then
        return 0
      fi
    fi
    if ! confirm "Try again?" "Y"; then
      die "Aborted."
    fi
  done
}

early_restore_or_continue() {
  local latest
  latest="$(_pcloud_latest_snapshot || true)"
  if [ -z "$latest" ]; then
    say "No existing snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH} — proceeding with fresh setup."
    return 0
  fi

  echo
  say "Found snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  echo "Latest: ${latest}"
  if confirm "Restore the latest snapshot now?" "Y"; then
    _pcloud_restore_from_snapshot "$latest"
    return 0
  fi
  if confirm "List and choose a different snapshot?" "N"; then
    _pcloud_list_snapshots || true
    local choice; choice=$(prompt "Enter snapshot name exactly (or blank to skip)")
    [ -n "$choice" ] && _pcloud_restore_from_snapshot "$choice"
  fi

  say "Skipping early restore — continuing with fresh setup."
}
