# modules/pcloud.sh
# pCloud connection + early restore helpers (robust: API preferred, WebDAV guarded)

# -------- defaults (safe if not set yet) --------
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

# -------- rclone wrappers with timeouts --------
RCLONE_BIN="${RCLONE_BIN:-rclone}"
RCLONE_BASE_OPTS=(--low-level-retries 1 --timeout 10s)

run_rclone() {
  # hard cap overall runtime so we never hang the wizard
  timeout 12 "$RCLONE_BIN" "${RCLONE_BASE_OPTS[@]}" "$@"
}

has_working_remote() {
  run_rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1
}

# -------- network guard for WebDAV endpoints --------
probe_https() {
  # Fast IPv4 probe to avoid IPv6/MTU weirdness
  curl -4 -sS -I --connect-timeout 4 --max-time 6 "$1" | head -n1 | grep -q '^HTTP/'
}

# -------- API (OAuth) helpers --------
create_api_remote_with_token() {
  # args: <token-json>
  local token_json="$1"
  [ -n "$token_json" ] || return 1
  "$RCLONE_BIN" config delete "$RCLONE_REMOTE_NAME" >/dev/null 2>&1 || true
  "$RCLONE_BIN" config create "$RCLONE_REMOTE_NAME" pcloud token "$token_json" --non-interactive --no-output
}

headless_oauth_walkthrough() {
  say "Launching rclone's headless OAuth configurator. Steps:"
  echo "  1) Select: n (New remote)"
  echo "  2) name: ${RCLONE_REMOTE_NAME}"
  echo "  3) Storage: pcloud"
  echo "  4) Use auto config? n"
  echo "  5) Follow the URL it prints in your local browser, Allow, then paste the code"
  echo "  6) Finish and 'q' to quit"
  "$RCLONE_BIN" config
}

# -------- WebDAV helpers (last resort) --------
create_webdav_remote() {
  # args: <email> <pass_plain> <url>
  local email="$1" pass_plain="$2" url="$3"
  local obscured
  obscured="$(printf '%s' "$pass_plain" | "$RCLONE_BIN" obscure -)"
  "$RCLONE_BIN" config delete "$RCLONE_REMOTE_NAME" >/dev/null 2>&1 || true
  # vendor=other is required for pCloud’s WebDAV quirks
  run_rclone config create "$RCLONE_REMOTE_NAME" webdav --non-interactive --no-output -- \
    vendor other url "$url" user "$email" pass "$obscured"
}

try_webdav_hosts_interactive() {
  # single-pass WebDAV attempt with guards; returns 0 on success
  say "Connect to pCloud via WebDAV (only if OAuth isn't possible)."
  local email pass host_eu="https://ewebdav.pcloud.com" host_glob="https://webdav.pcloud.com"

  read -r -p "pCloud login email: " email
  read -r -s -p "pCloud password (or App Password): " pass; echo

  # EU first if reachable
  if probe_https "$host_eu"; then
    say "Trying EU WebDAV endpoint..."
    create_webdav_remote "$email" "$pass" "$host_eu" || true
    if has_working_remote; then
      ok "Connected via EU WebDAV."
      return 0
    fi
    warn "EU WebDAV failed (auth or other)."
  else
    warn "EU WebDAV host not reachable quickly; skipping."
  fi

  # Global if reachable
  if probe_https "$host_glob"; then
    say "Trying Global WebDAV endpoint..."
    create_webdav_remote "$email" "$pass" "$host_glob" || true
    if has_working_remote; then
      ok "Connected via Global WebDAV."
      return 0
    fi
    warn "Global WebDAV failed (auth or other)."
  else
    warn "Global WebDAV host not reachable quickly; skipping."
  fi

  return 1
}

# -------- top-level: ensure a working remote (API preferred) --------
ensure_pcloud_remote_or_menu() {
  # 0) If a working remote already exists, use it untouched
  if has_working_remote; then
    ok "Using existing rclone remote '${RCLONE_REMOTE_NAME}'."
    return 0
  fi

  while true; do
    echo
    say "Choose how to connect to pCloud:"
    echo "  1) Paste an OAuth token (recommended)"
    echo "  2) Headless OAuth (rclone prints URL; paste the code)"
    echo "  3) Try WebDAV (EU then Global) [may time out / fail]"
    echo "  4) Skip for now"
    local choice; choice=$(prompt "Choose [1-4]" "1")

    case "$choice" in
      1)
        echo
        echo "On a machine with a browser, run:  rclone authorize \"pcloud\""
        echo "Copy the full JSON token it prints, then paste it below."
        read -r -p "Paste token JSON here: " token
        if create_api_remote_with_token "$token" && has_working_remote; then
          ok "pCloud OAuth remote created."
          return 0
        fi
        warn "Token invalid or creation failed. Try again."
        ;;
      2)
        headless_oauth_walkthrough
        if has_working_remote; then
          ok "pCloud OAuth remote created."
          return 0
        fi
        warn "Did not detect a working '${RCLONE_REMOTE_NAME}' remote. Try again."
        ;;
      3)
        if try_webdav_hosts_interactive && has_working_remote; then
          return 0
        fi
        warn "WebDAV attempt failed. Consider OAuth instead."
        ;;
      4)
        die "pCloud remote is required for backups/restores. Aborting as requested."
        ;;
      *)
        warn "Invalid choice."
        ;;
    esac
  done
}

# -------- snapshot helpers --------
list_snapshots() {
  run_rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null \
    | awk '{print $NF}' | sort
}

latest_snapshot() {
  list_snapshots | tail -n1
}

# -------- early restore path (unchanged in spirit, with guards) --------
restore_from_snapshot() {
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/_restore/${SNAP}"

  [ -n "$SNAP" ] || die "Internal error: empty snapshot name."

  mkdir -p "$STACK_DIR" "$DATA_ROOT" \
           "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"

  say "Fetching snapshot ${SNAP} to ${tmpdir}"
  mkdir -p "$tmpdir"
  run_rclone copy "${base}/${SNAP}" "$tmpdir" --fast-list

  # .env first (encrypted preferred)
  if [ -f "$tmpdir/.env.enc" ]; then
    say "Found encrypted .env.enc in snapshot."
    if [ -f "$ENV_BACKUP_PASSPHRASE_FILE" ]; then
      say "Decrypting using passphrase file: ${ENV_BACKUP_PASSPHRASE_FILE}"
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

  # Compose file
  if [ -f "$tmpdir/compose.snapshot.yml" ]; then
    say "Found compose.snapshot.yml; using it as docker-compose.yml"
    cp -f "$tmpdir/compose.snapshot.yml" "$COMPOSE_FILE"
  fi

  # Stop running stack (ignore errors)
  (cd "$STACK_DIR" && docker compose down) >/dev/null 2>&1 || true

  # Restore data archives
  say "Restoring media/data/export archives (if present)…"
  for a in media data export; do
    if [ -f "$tmpdir/${a}.tar.gz" ]; then
      tar -C "${DATA_ROOT}" -xzf "$tmpdir/${a}.tar.gz"
      ok "Restored ${a}.tar.gz"
    fi
  done

  # DB
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

early_restore_or_continue() {
  local latest
  latest="$(latest_snapshot || true)"
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
    restore_from_snapshot "$latest"
    return 0
  fi

  read -r -p "List and choose a different snapshot? [y/N]: " _ans2
  _ans2="${_ans2:-N}"
  if [[ "$_ans2" =~ ^[Yy]$ ]]; then
    list_snapshots || true
    local choice
    read -r -p "Enter snapshot name exactly (or blank to skip): " choice
    if [ -n "$choice" ]; then
      restore_from_snapshot "$choice"
      return 0
    fi
  fi

  say "Skipping early restore — continuing with fresh setup."
}
