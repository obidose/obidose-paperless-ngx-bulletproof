#!/usr/bin/env bash
set -Eeuo pipefail
# modules/pcloud.sh
# pCloud (WebDAV) + early restore helpers

# ---------- defaults (used only if not already set) ----------
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

# rclone timeout knobs to prevent hangs
_RTIME_FLAGS=(--timeout=12s --contimeout=6s --low-level-retries=1 --retries=1)

# ---------- tiny helpers ----------

_trim_crlf_spaces() {
  # strip CR/LF and leading/trailing whitespace
  # usage: _trim_crlf_spaces "string"
  printf '%s' "$1" | tr -d '\r\n' | sed -e 's/^[[:space:]]\+//' -e 's/[[:space:]]\+$//'
}

pcloud__conf_path() {
  if [ -n "${RCLONE_CONFIG:-}" ]; then
    printf '%s\n' "$RCLONE_CONFIG"
  else
    printf '%s\n' "${HOME}/.config/rclone/rclone.conf"
  fi
}

# Remove an existing remote block from a config file
# args: <conf> <remote>
pcloud__strip_remote_from_conf() {
  local conf="$1" remote="$2"
  # awk approach: keep everything except the [remote] block
  awk -v r="[$remote]" '
    BEGIN{inblk=0}
    /^\[.*\]$/{
      inblk=($0==r)
    }
    { if(!inblk) print $0 }
  ' "$conf" 2>/dev/null || true
}

# Write/replace the remote in rclone.conf
# args: <remote> <host> <email> <plain_password>
pcloud__write_remote_conf() {
  local remote="$1" host="$2" email_raw="$3" pass_raw="$4"
  local conf confdir email pass obscured tmp

  email="$(_trim_crlf_spaces "$email_raw")"
  pass="$(_trim_crlf_spaces "$pass_raw")"
  obscured="$(rclone obscure "$pass" | tr -d '\r\n')"

  conf="$(pcloud__conf_path)"
  confdir="$(dirname "$conf")"
  mkdir -p "$confdir"

  tmp="$(mktemp)"
  if [ -f "$conf" ]; then
    pcloud__strip_remote_from_conf "$conf" "$remote" > "$tmp" || :
  else
    : > "$tmp"
  fi

  {
    printf '\n[%s]\n' "$remote"
    printf 'type = webdav\n'
    printf 'url = %s\n' "$host"
    printf 'vendor = other\n'
    printf 'user = %s\n' "$email"
    printf 'pass = %s\n' "$obscured"
  } >> "$tmp"

  mv -f "$tmp" "$conf"
  chmod 600 "$conf" || true
}

# Fast auth probe with curl PROPFIND (Depth:0). Returns 0 on 207.
# args: <host> <email> <pass>
pcloud__http_auth_ok() {
  local host="$1" email="$2" pass="$3"
  local code
  code="$(
    curl -sS -u "${email}:${pass}" \
      --connect-timeout 8 --max-time 12 \
      -X PROPFIND -H "Depth: 0" \
      -o /dev/null -w "%{http_code}" \
      --data '<propfind xmlns="DAV:"><allprop/></propfind>' \
      "${host}/" || echo "000"
  )"
  [ "$code" = "207" ]
}

# Try EU first, then Global. If curl probe succeeds, write config and (optionally) quick rclone check.
# args: <email> <plain_password> <remote>
pcloud__try_hosts() {
  local email="$1" pass_plain="$2" remote="$3"
  local host_eu="https://ewebdav.pcloud.com"
  local host_global="https://webdav.pcloud.com"

  say "Trying EU WebDAV endpoint…"
  if pcloud__http_auth_ok "$host_eu" "$email" "$pass_plain"; then
    pcloud__write_remote_conf "$remote" "$host_eu" "$email" "$pass_plain"
    # quick non-blocking check; ignore failure to avoid hangs
    rclone "${_RTIME_FLAGS[@]}" -q lsd "${remote}:" >/dev/null 2>&1 || true
    ok "Connected to pCloud at ${host_eu}"
    return 0
  fi

  warn "EU endpoint failed. Trying Global endpoint…"
  if pcloud__http_auth_ok "$host_global" "$email" "$pass_plain"; then
    pcloud__write_remote_conf "$remote" "$host_global" "$email" "$pass_plain"
    rclone "${_RTIME_FLAGS[@]}" -q lsd "${remote}:" >/dev/null 2>&1 || true
    ok "Connected to pCloud at ${host_global}"
    return 0
  fi

  return 1
}

# list snapshot folder names with timeouts (won’t hang)
pcloud__list_snapshots() {
  rclone "${_RTIME_FLAGS[@]}" lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null \
    | awk '{print $NF}' | sort
}

pcloud__latest_snapshot() {
  pcloud__list_snapshots | tail -n1
}

# ---------- interactive login ----------

setup_pcloud_remote_interactive() {
  say "Connect to pCloud via WebDAV (if 2FA is ON, use an App Password)."
  local email pass
  while true; do
    email="$(prompt "pCloud login email")"
    [ -n "$email" ] || { warn "Email is required."; continue; }
    pass="$(prompt_secret "pCloud password (or App Password)")"

    if pcloud__try_hosts "$email" "$pass" "$RCLONE_REMOTE_NAME"; then
      export PCLOUD_EMAIL="$email"  # optional
      break
    fi

    warn "Authentication failed on both endpoints."
    warn "Tip: If 2FA is ON, create an App Password in pCloud and use it here."
    # Show where config is written for inspection
    say "rclone config: $(pcloud__conf_path)"
    read -r -p "Try again? [Y/n]: " a; a="${a:-Y}"
    [[ "$a" =~ ^[Yy]$ ]] || die "Aborted."
  done
}

# ---------- restore helpers ----------

pcloud__ensure_dirs() {
  mkdir -p "$STACK_DIR" "$DATA_ROOT" \
           "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"
}

# args: <snapshot_name>
pcloud__restore_from_snapshot() {
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/_restore/${SNAP}"
  [ -n "$SNAP" ] || die "Internal error: empty snapshot name."

  pcloud__ensure_dirs

  say "Fetching snapshot ${SNAP} to ${tmpdir}"
  mkdir -p "$tmpdir"
  rclone "${_RTIME_FLAGS[@]}" copy "${base}/${SNAP}" "$tmpdir" --fast-list

  # Restore .env (prefer encrypted)
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
          -pass "pass:${_pp}" || die "Failed to decrypt .env.enc."
      fi
    else
      warn "No passphrase file at ${ENV_BACKUP_PASSPHRASE_FILE}."
      read -r -s -p "Enter passphrase to decrypt .env: " _pp2; echo
      openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
        -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
        -pass "pass:${_pp2}" || die "Failed to decrypt .env.enc."
    fi
    ok "Decrypted .env to ${ENV_FILE}"
  elif [ -f "$tmpdir/.env" ]; then
    say "Found plain .env in snapshot; restoring."
    cp -f "$tmpdir/.env" "$ENV_FILE"
  else
    warn "No .env found in snapshot; the wizard will prompt for missing values later."
  fi

  # Restore compose file if present
  if [ -f "$tmpdir/compose.snapshot.yml" ]; then
    say "Found compose.snapshot.yml; using it as docker-compose.yml"
    cp -f "$tmpdir/compose.snapshot.yml" "$COMPOSE_FILE"
  fi

  # Stop stack if running
  (cd "$STACK_DIR" && docker compose down) >/dev/null 2>&1 || true

  # Restore data archives
  say "Restoring media/data/export archives (if present)…"
  for a in media data export; do
    if [ -f "$tmpdir/${a}.tar.gz" ]; then
      tar -C "${DATA_ROOT}" -xzf "$tmpdir/${a}.tar.gz"
      ok "Restored ${a}.tar.gz"
    fi
  done

  # Restore database if dump present
  if [ -f "$tmpdir/postgres.sql" ]; then
    say "Starting database to import SQL…"
    (cd "$STACK_DIR" && docker compose up -d db)
    sleep 8

    local DBNAME DBUSER
    DBNAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
    DBUSER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
    DBNAME="${DBNAME:-paperless}"
    DBUSER="${DBUSER:-paperless}"

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

# Offer early restore if backups exist (with timeouts)
early_restore_or_continue() {
  local latest
  latest="$(pcloud__latest_snapshot || true)"

  if [ -z "$latest" ]; then
    say "No existing snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH} — proceeding with fresh setup."
    return 0
  fi

  echo
  say "Found snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  echo "Latest: ${latest}"
  read -r -p "Restore the latest snapshot now? [Y/n]: " _ans
  _ans="${_ans:-Y}"
  if [[ "$_ans" =~ ^[Yy]$ ]]; then
    pcloud__restore_from_snapshot "$latest"
    return 0
  fi

  read -r -p "List and choose a different snapshot? [y/N]: " _ans2
  _ans2="${_ans2:-N}"
  if [[ "$_ans2" =~ ^[Yy]$ ]]; then
    pcloud__list_snapshots || true
    local choice
    read -r -p "Enter snapshot name exactly (or blank to skip): " choice
    if [ -n "$choice" ]; then
      pcloud__restore_from_snapshot "$choice"
      return 0
    fi
  fi

  say "Skipping early restore — continuing with fresh setup."
}
