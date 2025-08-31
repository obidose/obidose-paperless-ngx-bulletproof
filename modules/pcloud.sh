#!/usr/bin/env bash
set -Eeuo pipefail
# modules/pcloud.sh
# pCloud (OAuth) + auto region selection + early restore helpers

# EXPECTS (from common.sh / install.sh):
#   say/ok/warn/die/log helpers
#   jq installed
#   RCLONE_REMOTE_NAME (default: pcloud)
#   INSTANCE_NAME (default: paperless)
#   RCLONE_REMOTE_PATH (default: backups/paperless/${INSTANCE_NAME})
#   STACK_DIR, DATA_ROOT, COMPOSE_FILE, ENV_FILE
#   ENV_BACKUP_PASSPHRASE_FILE

RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"

STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"

COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"

ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"

DIR_EXPORT="${DATA_ROOT}/export"
DIR_MEDIA="${DATA_ROOT}/media"
DIR_DATA="${DATA_ROOT}/data"
DIR_CONSUME="${DATA_ROOT}/consume"
DIR_DB="${DATA_ROOT}/db"
DIR_TIKA_CACHE="${DATA_ROOT}/tika-cache"

# -------------------- internal helpers --------------------

_pcloud_api_probe() {
  # args: <access_token> <host>  (host is api.pcloud.com or eapi.pcloud.com)
  local token="$1" host="$2"
  curl -fsS --connect-timeout 6 --max-time 10 \
    "https://${host}/userinfo?access_token=${token}" \
    | jq -e 'has("result") and .result == 0' >/dev/null 2>&1
}

_pcloud_detect_host_from_token() {
  # args: <access_token>
  local token="$1"
  if _pcloud_api_probe "$token" "eapi.pcloud.com"; then
    echo "eapi.pcloud.com"; return 0
  fi
  if _pcloud_api_probe "$token" "api.pcloud.com"; then
    echo "api.pcloud.com"; return 0
  fi
  return 1
}

_pcloud_get_existing_token_json() {
  # Reads token string (JSON) for remote named $RCLONE_REMOTE_NAME
  # Returns empty if not found
  rclone config dump --all \
    | jq -r --arg name "$RCLONE_REMOTE_NAME" '
        to_entries[]
        | select(.key==$name)
        | .value.token // empty
      '
}

_pcloud_remote_type() {
  rclone config dump --all \
    | jq -r --arg name "$RCLONE_REMOTE_NAME" '
        to_entries[]
        | select(.key==$name)
        | .value.type // empty
      '
}

_pcloud_remote_has_hostname() {
  rclone config dump --all \
    | jq -r --arg name "$RCLONE_REMOTE_NAME" '
        to_entries[]
        | select(.key==$name)
        | .value.hostname // empty
      '
}

_pcloud_write_remote_with_token() {
  # args: <token_json> <hostname>
  local token_json="$1" host="$2"
  rclone config delete "$RCLONE_REMOTE_NAME" >/dev/null 2>&1 || true
  rclone config create "$RCLONE_REMOTE_NAME" pcloud \
    token "$token_json" \
    hostname "$host" \
    --non-interactive --no-output >/dev/null
}

_pcloud_test_remote() {
  rclone about "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1
}

# -------------------- user flows --------------------

pcloud_auto_region_or_setup() {
  # 1) If an OAuth pcloud remote already exists, detect region and fix hostname.
  local t typ host token_json access_token
  typ="$(_pcloud_remote_type || true)"
  if [ "$typ" = "pcloud" ]; then
    token_json="$(_pcloud_get_existing_token_json || true)"
    if [ -n "$token_json" ] && command -v jq >/dev/null 2>&1; then
      access_token="$(printf '%s' "$token_json" | jq -r 'try (fromjson | .access_token) catch empty')"
      if [ -n "$access_token" ]; then
        host="$(_pcloud_detect_host_from_token "$access_token" || true)"
        if [ -n "$host" ]; then
          # Only update hostname if missing or wrong
          local current_host
          current_host="$(_pcloud_remote_has_hostname || true)"
          if [ "$current_host" != "$host" ]; then
            say "Setting pCloud API hostname to ${host}"
            rclone config update "$RCLONE_REMOTE_NAME" hostname "$host" --non-interactive >/dev/null
          fi
          if _pcloud_test_remote; then
            ok "pCloud remote '${RCLONE_REMOTE_NAME}' is ready."
            return 0
          fi
        fi
      fi
    fi
    warn "Existing '${RCLONE_REMOTE_NAME}' remote not working; reconfiguring."
  fi

  # 2) No working remote; guide user to set it up
  echo
  say "Choose how to connect to pCloud:"
  echo "  1) Paste OAuth token JSON (recommended)"
  echo "  2) Headless OAuth helper (prints steps and asks for token)"
  echo "  3) Try legacy WebDAV (may fail on some networks/regions)"
  echo "  4) Skip"
  local choice; read -r -p "Choose [1-4] [1]: " choice; choice="${choice:-1}"

  case "$choice" in
    1) _flow_paste_token ;;
    2) _flow_headless_helper ;;
    3) _flow_webdav_legacy ;;
    *) die "pCloud not configured. Aborting." ;;
  esac
}

_flow_paste_token() {
  say "On a machine with a browser, run:  rclone authorize \"pcloud\""
  say "Copy the full JSON it prints, then paste it below."

  local token_json access_token host
  printf "Paste token JSON here: "
  IFS= read -r token_json || true
  token_json="$(echo -n "$token_json" | tr -d '\r\n')"

  if ! command -v jq >/dev/null 2>&1; then
    die "jq is required for token parsing."
  fi

  access_token="$(printf '%s' "$token_json" | jq -r 'try (.access_token) catch empty')"
  [ -n "$access_token" ] || die "Invalid token JSON."

  host="$(_pcloud_detect_host_from_token "$access_token" || true)"
  [ -n "$host" ] || die "Token was not accepted by EU or US API."

  _pcloud_write_remote_with_token "$token_json" "$host"
  if _pcloud_test_remote; then
    ok "pCloud remote '${RCLONE_REMOTE_NAME}' configured for ${host}."
  else
    die "pCloud remote failed a test call after setup."
  fi
}

_flow_headless_helper() {
  cat <<'NOTE'

Headless OAuth:
1) On any machine with a browser:  rclone authorize "pcloud"
2) After login, rclone prints a JSON token.
3) Paste that JSON token here and press Enter.

NOTE
  _flow_paste_token
}

_flow_webdav_legacy() {
  say "Trying WebDAV (EU first, then Global). If 2FA is on, use an App Password."
  local email pass host

  read -r -p "pCloud login email: " email
  read -r -s -p "pCloud password (or App Password): " pass; echo

  # Try EU WebDAV quickly (curl HEAD to avoid long hangs)
  if curl -sS -I --connect-timeout 6 --max-time 8 https://ewebdav.pcloud.com >/dev/null; then
    host="https://ewebdav.pcloud.com"
  elif curl -sS -I --connect-timeout 6 --max-time 8 https://webdav.pcloud.com >/dev/null; then
    host="https://webdav.pcloud.com"
  else
    warn "Both WebDAV endpoints timed out."
    die "WebDAV unavailable; use OAuth method instead."
  fi

  # Create a WebDAV remote named like the OAuth one, for uniformity.
  rclone config delete "$RCLONE_REMOTE_NAME" >/dev/null 2>&1 || true
  rclone config create "$RCLONE_REMOTE_NAME" webdav \
    vendor other url "$host" \
    user "$email" pass "$(rclone obscure "$pass")" \
    --non-interactive --no-output >/dev/null

  if rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
    ok "Connected to WebDAV at $host"
  else
    die "WebDAV auth failed. Use OAuth method."
  fi
}

# -------------------- listing + restore helpers --------------------

pcloud_list_snapshots() {
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null \
    | awk '{print $NF}' | sort
}

pcloud_latest_snapshot() {
  pcloud_list_snapshots | tail -n1
}

pcloud_ensure_dirs() {
  mkdir -p "$STACK_DIR" "$DATA_ROOT" \
           "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"
}

pcloud_restore_snapshot() {
  # args: <snapshot_name>
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/_restore/${SNAP}"

  [ -n "$SNAP" ] || die "Empty snapshot name."

  pcloud_ensure_dirs
  say "Fetching snapshot ${SNAP} to ${tmpdir}"
  mkdir -p "$tmpdir"
  rclone copy "${base}/${SNAP}" "$tmpdir" --fast-list

  # env restore (encrypted preferred)
  if [ -f "$tmpdir/.env.enc" ]; then
    say "Decrypting .env.enc"
    if [ -f "$ENV_BACKUP_PASSPHRASE_FILE" ]; then
      openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
        -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
        -pass "file:${ENV_BACKUP_PASSPHRASE_FILE}" || {
          warn "Passphrase file failed; prompting."
          read -r -s -p "Enter passphrase: " _pp; echo
          openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
            -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
            -pass "pass:${_pp}" || die "Failed to decrypt .env.enc"
        }
    else
      read -r -s -p "Enter passphrase: " _pp; echo
      openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
        -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
        -pass "pass:${_pp}" || die "Failed to decrypt .env.enc"
    fi
    ok "Decrypted .env -> ${ENV_FILE}"
  elif [ -f "$tmpdir/.env" ]; then
    cp -f "$tmpdir/.env" "$ENV_FILE"
    ok "Restored plain .env"
  else
    warn "No .env in snapshot."
  fi

  # compose restore
  if [ -f "$tmpdir/compose.snapshot.yml" ]; then
    cp -f "$tmpdir/compose.snapshot.yml" "$COMPOSE_FILE"
    ok "Restored compose file."
  fi

  # stop any running stack (ignore errors)
  (cd "$STACK_DIR" && docker compose down) >/dev/null 2>&1 || true

  # data archives
  for a in media data export; do
    if [ -f "$tmpdir/${a}.tar.gz" ]; then
      tar -C "${DATA_ROOT}" -xzf "$tmpdir/${a}.tar.gz"
      ok "Restored ${a}.tar.gz"
    fi
  done

  # DB restore
  if [ -f "$tmpdir/postgres.sql" ]; then
    say "Starting db container to import SQL…"
    (cd "$STACK_DIR" && docker compose up -d db)
    sleep 8
    local DBNAME DBUSER
    DBNAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 'paperless')"
    DBUSER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 'paperless')"

    docker compose -f "$COMPOSE_FILE" exec -T db \
      psql -U "$DBUSER" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DBNAME}' AND pid <> pg_backend_pid();" || true
    docker compose -f "$COMPOSE_FILE" exec -T db \
      psql -U "$DBUSER" -c "DROP DATABASE IF EXISTS \"${DBNAME}\"; CREATE DATABASE \"${DBNAME}\";"

    cat "$tmpdir/postgres.sql" | docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DBUSER" "$DBNAME"
    ok "Database restored."
  fi

  say "Bringing stack up…"
  (cd "$STACK_DIR" && docker compose up -d)
  ok "Restore complete."
  exit 0
}

pcloud_early_restore_or_continue() {
  # Needs a working remote already.
  if ! _pcloud_test_remote; then
    return 0
  fi
  local latest
  latest="$(pcloud_latest_snapshot || true)"
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
    pcloud_restore_snapshot "$latest"
  fi

  read -r -p "List and choose another snapshot? [y/N]: " _ans2
  _ans2="${_ans2:-N}"
  if [[ "$_ans2" =~ ^[Yy]$ ]]; then
    pcloud_list_snapshots || true
    local choice
    read -r -p "Enter snapshot name (exact): " choice
    [ -n "$choice" ] && pcloud_restore_snapshot "$choice"
  fi

  say "Skipping restore; continuing with fresh setup."
}
