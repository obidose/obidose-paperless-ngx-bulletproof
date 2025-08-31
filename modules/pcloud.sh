#!/usr/bin/env bash
# modules/pcloud.sh
# pCloud setup + early-restore helpers for the Paperless-ngx Bulletproof installer.
# Requires in caller: say/ok/warn/die; rclone installed; docker present.

# ---- Defaults (honor env from common.sh if set) ----
RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"

STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"

COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"

ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"

# Derived dirs (recomputed if DATA_ROOT changed before sourcing)
DIR_EXPORT="${DIR_EXPORT:-${DATA_ROOT}/export}"
DIR_MEDIA="${DIR_MEDIA:-${DATA_ROOT}/media}"
DIR_DATA="${DIR_DATA:-${DATA_ROOT}/data}"
DIR_CONSUME="${DIR_CONSUME:-${DATA_ROOT}/consume}"
DIR_DB="${DIR_DB:-${DATA_ROOT}/db}"
DIR_TIKA_CACHE="${DIR_TIKA_CACHE:-${DATA_ROOT}/tika-cache}"

# ---- tiny utils ----
_has(){ command -v "$1" >/dev/null 2>&1; }
_dc(){ docker compose -f "$COMPOSE_FILE" "$@"; }

_timeout(){
  # usage: _timeout SECONDS cmd...
  local t="$1"; shift
  if _has timeout; then timeout "$t" "$@"; else "$@"; fi
}

_sanitize_oneline(){
  # read from stdin -> print one clean line (strip CR/LF/NUL)
  tr -d '\r\0' | tr -d '\n'
}

# ---- rclone helpers ----
_pcloud_remote_exists(){
  rclone listremotes 2>/dev/null | grep -qx "${RCLONE_REMOTE_NAME}:"
}

_pcloud_remote_ok(){
  _pcloud_remote_exists || return 1
  _timeout 10 rclone about "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1
}

# Create a pcloud remote with token + explicit hostname, replacing any existing
_pcloud_create_oauth_remote(){
  # args: token_json, hostname (api.pcloud.com | eapi.pcloud.com)
  local token_json="$1" host="$2"
  rclone config delete "${RCLONE_REMOTE_NAME}" >/dev/null 2>&1 || true
  # rclone’s pcloud backend supports the advanced option "hostname"
  rclone config create "${RCLONE_REMOTE_NAME}" pcloud \
    token "$token_json" hostname "$host" --non-interactive >/dev/null
}

# Auto-probe API region for the pasted token
_pcloud_set_oauth_token_autoregion(){
  # arg: token_json
  local token_json="$1"

  # 1) Try Global
  _pcloud_create_oauth_remote "$token_json" "api.pcloud.com"
  if _pcloud_remote_ok; then
    ok "pCloud remote '${RCLONE_REMOTE_NAME}:' configured for api.pcloud.com."
    return 0
  fi

  # 2) Try EU
  _pcloud_create_oauth_remote "$token_json" "eapi.pcloud.com"
  if _pcloud_remote_ok; then
    ok "pCloud remote '${RCLONE_REMOTE_NAME}:' configured for eapi.pcloud.com."
    return 0
  fi

  # 3) Fail
  return 1
}

# WebDAV path
_pcloud_webdav_create(){
  # args: email, plain_pass, host_url
  local email="$1" pass_plain="$2" host="$3"
  local obscured; obscured="$(rclone obscure "$pass_plain")"
  rclone config delete "${RCLONE_REMOTE_NAME}" >/dev/null 2>&1 || true
  rclone config create "${RCLONE_REMOTE_NAME}" webdav --non-interactive -- \
    vendor other url "$host" user "$email" pass "$obscured" >/dev/null
}

_pcloud_webdav_try_both(){
  local email="$1" pass_plain="$2"
  say "Trying EU WebDAV endpoint…"
  _pcloud_webdav_create "$email" "$pass_plain" "https://ewebdav.pcloud.com"
  if _timeout 8 rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
    ok "Connected via EU WebDAV."
    return 0
  fi
  warn "EU endpoint failed. Trying Global endpoint…"
  _pcloud_webdav_create "$email" "$pass_plain" "https://webdav.pcloud.com"
  if _timeout 8 rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
    ok "Connected via Global WebDAV."
    return 0
  fi
  return 1
}

# ---- interactive connection menu (exported) ----
ensure_pcloud_remote_or_menu(){
  # Fast path
  if _pcloud_remote_ok; then
    ok "pCloud remote '${RCLONE_REMOTE_NAME}:' is ready."
    return 0
  fi

  while true; do
    echo
    say "Choose how to connect to pCloud:"
    echo "  1) Paste OAuth token JSON (recommended)"
    echo "  2) Headless OAuth helper (shows steps; you paste token)"
    echo "  3) Try legacy WebDAV (may fail on some networks/regions)"
    echo "  4) Skip"
    read -r -p "Choose [1-4] [1]: " _choice
    _choice="${_choice:-1}"

    case "$_choice" in
      1)
        echo
        say "On any machine with a browser, run:  rclone authorize \"pcloud\""
        say "Copy the JSON it prints and paste it below."
        read -r -p "Paste token JSON here: " _tok_raw || true
        _tok="$(printf '%s' "${_tok_raw}" | _sanitize_oneline)"
        if [ -z "$_tok" ]; then warn "Empty token."; continue; fi
        if _has jq && ! printf '%s' "$_tok" | jq -e '.access_token' >/dev/null 2>&1; then
          warn "Token does not look like JSON with access_token."; continue
        fi
        if _pcloud_set_oauth_token_autoregion "$_tok"; then
          ok "pCloud remote configured."
          return 0
        fi
        warn "Token invalid or not valid for either region. Try again."
        ;;

      2)
        echo
        say "Headless OAuth:"
        echo "  1) On any machine with a browser:  rclone authorize \"pcloud\""
        echo "  2) After login, rclone prints a JSON token."
        echo "  3) Paste that JSON token here and press Enter."
        read -r -p "Paste token JSON here: " _tok2_raw || true
        _tok2="$(printf '%s' "$_tok2_raw" | _sanitize_oneline)"
        if [ -z "$_tok2" ]; then warn "Empty token."; continue; fi
        if _pcloud_set_oauth_token_autoregion "$_tok2"; then
          ok "pCloud remote configured."
          return 0
        fi
        warn "Token invalid or not valid for either region. Try again."
        ;;

      3)
        echo
        say "Legacy WebDAV auth (email + password or App Password)."
        read -r -p "pCloud login email: " _em || true
        read -r -s -p "pCloud password (or App Password): " _pw; echo
        if [ -z "$_em" ] || [ -z "$_pw" ]; then warn "Email and password required."; continue; fi
        if _pcloud_webdav_try_both "$_em" "$_pw"; then
          if _pcloud_remote_ok; then ok "pCloud remote configured."; return 0; fi
        fi
        warn "Authentication failed on both endpoints."
        ;;

      4)
        warn "Skipping pCloud configuration for now."
        return 1
        ;;

      *)
        warn "Invalid choice."
        ;;
    esac
  done
}

# ---- Snapshot discovery ----
_pcloud_list_snapshots(){
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null \
   | awk '{print $NF}' | sort
}

_pcloud_latest_snapshot(){ _pcloud_list_snapshots | tail -n1; }

# ---- Ensure dirs ----
_pcloud_ensure_dirs(){
  mkdir -p "$STACK_DIR" "$DATA_ROOT" \
           "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"
}

# ---- Early restore (exported) ----
pcloud_early_restore_or_continue(){
  if ! _pcloud_remote_ok; then
    say "pCloud not configured; proceeding with fresh setup."
    return 0
  fi

  local latest; latest="$(_pcloud_latest_snapshot || true)"
  if [ -z "$latest" ]; then
    say "No snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}."
    return 0
  fi

  echo
  say "Found snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  echo "Latest: ${latest}"
  read -r -p "Restore the latest snapshot now? [Y/n]: " _ans
  _ans="${_ans:-Y}"
  if [[ "$_ans" =~ ^[Yy]$ ]]; then
    _pcloud_restore_from_snapshot "$latest"
    return 0
  fi

  read -r -p "List and choose another snapshot? [y/N]: " _ans2
  _ans2="${_ans2:-N}"
  if [[ "$_ans2" =~ ^[Yy]$ ]]; then
    _pcloud_list_snapshots || true
    local choice; read -r -p "Enter snapshot name exactly (or blank to skip): " choice
    if [ -n "$choice" ]; then
      _pcloud_restore_from_snapshot "$choice"
      return 0
    fi
  fi

  say "Skipping early restore — continuing with fresh setup."
}

_pcloud_restore_from_snapshot(){
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/_restore/${SNAP}"
  [ -z "$SNAP" ] && die "Internal error: empty snapshot name."

  _pcloud_ensure_dirs

  say "Fetching snapshot ${SNAP} to ${tmpdir}"
  mkdir -p "$tmpdir"
  rclone copy "${base}/${SNAP}" "$tmpdir" --fast-list

  # Restore env first (prefer encrypted)
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
    warn "No .env found in snapshot; the wizard will prompt later."
  fi

  # Restore compose if present
  if [ -f "$tmpdir/compose.snapshot.yml" ]; then
    say "Found compose.snapshot.yml; using it as docker-compose.yml"
    cp -f "$tmpdir/compose.snapshot.yml" "$COMPOSE_FILE"
  fi

  # Stop any running stack (ignore errors)
  (cd "$STACK_DIR" && docker compose down) >/dev/null 2>&1 || true

  # Restore data archives
  say "Restoring media/data/export archives (if present)…"
  for a in media data export; do
    if [ -f "$tmpdir/${a}.tar.gz" ]; then
      tar -C "${DATA_ROOT}" -xzf "$tmpdir/${a}.tar.gz"
      ok "Restored ${a}.tar.gz"
    fi
  done

  # Import postgres.sql if present
  if [ -f "$tmpdir/postgres.sql" ]; then
    say "Starting database to import SQL…"
    (cd "$STACK_DIR" && docker compose up -d db)
    # wait for db
    for i in {1..30}; do
      if _dc exec -T db pg_isready -U "${POSTGRES_USER:-paperless}" -d "${POSTGRES_DB:-paperless}" >/dev/null 2>&1; then break; fi
      sleep 2
    done

    local DBNAME DBUSER
    DBNAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 'paperless')"
    DBUSER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 'paperless')"

    say "Dropping & recreating database ${DBNAME}"
    _dc exec -T db psql -U "$DBUSER" -c \
      "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DBNAME}' AND pid <> pg_backend_pid();" || true
    _dc exec -T db psql -U "$DBUSER" -c \
      "DROP DATABASE IF EXISTS \"${DBNAME}\"; CREATE DATABASE \"${DBNAME}\";"

    say "Importing SQL dump…"
    cat "$tmpdir/postgres.sql" | _dc exec -T db psql -U "$DBUSER" "$DBNAME"
    ok "Database restored."
  fi

  say "Bringing full stack up…"
  (cd "$STACK_DIR" && docker compose up -d)
  ok "Restore complete."

  # Exit the installer after a successful early restore
  exit 0
}
