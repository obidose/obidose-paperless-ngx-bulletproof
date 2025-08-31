#!/usr/bin/env bash
set -Eeuo pipefail
set -o errtrace
trap 'echo -e "\e[1;31m[x]\e[0m Error at ${BASH_SOURCE}:${LINENO}: ${BASH_COMMAND}"; exit 1' ERR

# === Settings: point at your repo's raw content ===
GITHUB_RAW="${GITHUB_RAW:-https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main}"

# === Fetch a file to a path, fail if missing ===
fetch() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  curl -fsSL "$src" -o "$dst"
}

# === Stage modules locally ===
STAGE_DIR="/opt/paperless-wiz"
mkdir -p "$STAGE_DIR/modules" "$STAGE_DIR/ops"

fetch "$GITHUB_RAW/modules/common.sh" "$STAGE_DIR/modules/common.sh"
fetch "$GITHUB_RAW/modules/deps.sh"   "$STAGE_DIR/modules/deps.sh"
fetch "$GITHUB_RAW/modules/pcloud.sh" "$STAGE_DIR/modules/pcloud.sh"
fetch "$GITHUB_RAW/modules/files.sh"  "$STAGE_DIR/modules/files.sh"

# shellcheck source=/dev/null
source "$STAGE_DIR/modules/common.sh"
source "$STAGE_DIR/modules/deps.sh"
source "$STAGE_DIR/modules/pcloud.sh"
source "$STAGE_DIR/modules/files.sh"

need_root
ubuntu_version_ok || warn "Ubuntu $(. /etc/os-release; echo $VERSION_ID) detected; tested on 22.04/24.04"

echo; log "Starting Paperless-ngx setup wizard…"
install_prereqs
ensure_user
install_docker
install_rclone

echo; log "Press Enter to accept the [default] value, or type a custom value."
TIMEZONE=$(prompt "Timezone (IANA, e.g., Pacific/Auckland; Enter=default)" "$TIMEZONE_DEFAULT")
INSTANCE_NAME=$(prompt "Instance name (Enter=default)" "$INSTANCE_NAME")
RCLONE_REMOTE_PATH_TEMPLATE="backups/paperless/${INSTANCE_NAME}"

DATA_ROOT=$(prompt "Data root (persistent storage; Enter=default)" "$DATA_ROOT")
STACK_DIR=$(prompt "Stack dir (where docker-compose.yml lives; Enter=default)" "$STACK_DIR")

PAPERLESS_ADMIN_USER=$(prompt "Paperless admin username (Enter=default)" "$PAPERLESS_ADMIN_USER")
local_admin_default="$PAPERLESS_ADMIN_PASSWORD"
local_db_default="$POSTGRES_PASSWORD"
PAPERLESS_ADMIN_PASSWORD=$(prompt "Paperless admin password (Enter=default)" "$local_admin_default")
POSTGRES_PASSWORD=$(prompt "Postgres password (Enter=default)" "$local_db_default")

ENABLE_TRAEFIK=$(prompt "Enable Traefik with HTTPS? (yes/no; Enter=default)" "$ENABLE_TRAEFIK")
if [ "$ENABLE_TRAEFIK" = "yes" ]; then
  DOMAIN=$(prompt "Domain for Paperless (DNS A/AAAA must point here; Enter=default)" "$DOMAIN")
  LETSENCRYPT_EMAIL=$(prompt "Let's Encrypt email (Enter=default)" "$LETSENCRYPT_EMAIL")
  PAPERLESS_URL="https://$DOMAIN"
else
  HTTP_PORT=$(prompt "Bind Paperless on host port (Enter=default)" "$HTTP_PORT")
  PAPERLESS_URL="http://localhost:${HTTP_PORT}"
fi

RCLONE_REMOTE_NAME=$(prompt "rclone remote name (Enter=default)" "$RCLONE_REMOTE_NAME")
RCLONE_REMOTE_PATH=$(prompt "Remote path for backups (Enter=default)" "$RCLONE_REMOTE_PATH_TEMPLATE")
RETENTION_DAYS=$(prompt "Retention days (Enter=default)" "$RETENTION_DAYS")
CRON_TIME=$(prompt "Backup cron (m h dom mon dow; Enter=default)" "$CRON_TIME")

setup_pcloud   # asks for pCloud email + password once, tries EU then Global

prepare_dirs
write_env
write_compose
write_backup_script
install_cron

# Offer restore if snapshots exist
latest="$(find_latest_snapshot || true)"
if [ -n "$latest" ]; then
  echo; log "Found snapshot(s) under ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  echo "Latest: $latest"
  if confirm "Restore latest now?" "Y"; then
    restore_from_remote "$latest"
  else
    if confirm "List and choose a different snapshot?" "N"; then
      list_snapshots "$RCLONE_REMOTE_PATH" || true
      choice="$(prompt "Enter snapshot name exactly")"
      [ -n "$choice" ] && restore_from_remote "$choice" || bring_up
    else
      bring_up
    fi
  fi
else
  bring_up
fi

# Install the menu command: /usr/local/bin/bulletproof
fetch "$GITHUB_RAW/bulletproof.sh" "/usr/local/bin/bulletproof"
chmod +x /usr/local/bin/bulletproof
log "Installed helper menu: run 'bulletproof' any time."

echo; log "All set! Access Paperless at: ${PAPERLESS_URL}"
