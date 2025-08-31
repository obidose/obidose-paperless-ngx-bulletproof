# modules/pcloud.sh
# pCloud (WebDAV) + early restore helpers
# Assumes: say/ok/warn/die helpers exist (from install.sh), and rclone is installed (deps.sh).
# Expects these vars (with sane defaults if not set yet):
#   RCLONE_REMOTE_NAME (default: pcloud)
#   INSTANCE_NAME      (default: paperless)
#   RCLONE_REMOTE_PATH (default: backups/paperless/${INSTANCE_NAME})
#   STACK_DIR          (default: /home/docker/paperless-setup)
#   DATA_ROOT          (default: /home/docker/paperless)
#   COMPOSE_FILE       (default: ${STACK_DIR}/docker-compose.yml)
#   ENV_FILE           (default: ${STACK_DIR}/.env)
#   ENV_BACKUP_PASSPHRASE_FILE (default: /root/.paperless_env_pass)

# -------------------- defaults --------------------
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

# -------------------- low-level helpers --------------------

pcloud__create_remote() {
  # args: <email> <plain_password> <remote_name> <webdav_host>
  local email="$1" pass_plain="$2" remote="$3" host="$4"
  local obscured
  obscured="$(rclone obscure "$pass_plain")"

  # Reset any prior config for a clean attempt
  rclone config delete "$remote" >/dev/null 2>&1 || true

  # Use vendor=other for best compatibility with pCloud’s WebDAV
  rclone config create "$remote" webdav vendor other \
    url "$host" user "$email" pass "$obscured" >/dev/null
}

pcloud__test_remote() {
  # args: <remote_name>
  local remote="$1"
  rclone lsd "${remote}:" >/dev/null 2>&1
}

pcloud__try_hosts() {
  # args: <email> <plain_password> <remote_name>
  local email="$1" pass_plain="$2" remote="$3"
  local host_eu="https://ewebdav.pcloud.com"
  local host_global="https://webdav.pcloud.com"

  say "Trying EU WebDAV endpoint…"
  pcloud__create_remote "$email" "$pass_plain" "$remote" "$host_eu"
  if pcloud__test_remote "$remote"; then
    ok "Connected to pCloud at ${host_eu}"
    return 0
  fi

  warn "EU endpoint failed. Trying Global endpoint…"
  pcloud__create_remote "$email" "$pass_plain" "$remote" "$host_global"
  if pcloud__test_remote "$remote"; then
    ok "Connected to pCloud at ${host_global}"
    return 0
  fi

  return 1
}

pcloud__list_snapshots() {
  # lists snapshot directory names sorted alphabetically (timestamp-friendly)
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null \
    | awk '{print $NF}' | sort
}

pcloud__latest_snapshot() {
  pcloud__list_snapshots | tail -n1
}

# -------------------- interactive setup --------------------

setup_pcloud_remote_interactive() {
  # Interactive loop: ask once, try EU→Global, if both fail, show verbose hint and re-prompt.
  say "Connect to pCloud via WebDAV (if 2FA is ON, use an App Password)."
  local email pass

  while true; do
    read -r -p "pCloud login email: " email
    if [ -z "$email" ]; then
      warn "Email is required."
      continue
    fi
    # Single hidden prompt (no double entry); if auth fails we loop back.
    read -r -s -p "pCloud password (or App Password): " pass; echo

    if pcloud__try_hosts "$email" "$pass" "$RCLONE_REMOTE_NAME"; then
      export PCLOUD_EMAIL="$email"  # keep for this session (optional)
      break
    fi

    warn "Both endpoints failed; running a verbose check:"
    rclone -vv lsd "${RCLONE_REMOTE_NAME}:" || true
    warn "Authentication failed. Re-check email/password. If 2FA is ON, use an App Password."
    # loop to re-prompt
  done
}

# -------------------- early restore path --------------------

pcloud__ensure_dirs() {
  mkdir -p "$STACK_DIR" "$DATA_ROOT" \
           "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"
}

pcloud__restore_from_snapshot() {
  # args: <snapshot_name>
  local SNAP="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmpdir="${STACK_DIR}/_restore/${SNAP}"

  [ -z "$SNAP" ] && die "Internal error: empty snapshot name passed to restore."

  pcloud__ensure_dirs

  say "Fetching snapshot ${SNAP} to ${tmpdir}"
  mkdir -p "$tmpdir"
  rclone copy "${base}/${SNAP}" "$tmpdir" --fast-list

  # If an env backup is present, restore it first (prefer .env.enc over .env)
  if [ -f "$tmpdir/.env.enc" ]; then
    say "Found encrypted .env.enc in snapshot."
    if [ -f "$ENV_BACKUP_PASSPHRASE_FILE" ]; then
      say "Decrypting .env using passphrase file: ${ENV_BACKUP_PASSPHRASE_FILE}"
      if ! openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -salt \
          -in "$tmpdir/.env.enc" -out "$ENV_FILE" \
          -pass "file:${ENV_BACKUP_PASSPHRASE_FILE}"; then
        warn "Decryption with passphrase file failed."
        # Prompt once interactively
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

  # Restore compose file if present
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

  # If postgres.sql exists, import it
  if [ -f "$tmpdir/postgres.sql" ]; then
    say "Starting database to import SQL…"
    (cd "$STACK_DIR" && docker compose up -d db)
    sleep 8

    # Try to read DB settings from .env if present
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

  # Exit the installer after a successful early restore to avoid running the fresh path.
  exit 0
}

early_restore_or_continue() {
  # If any snapshot exists, offer an early restore path.
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
    return 0 # (unreached due to exit 0 on success)
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
