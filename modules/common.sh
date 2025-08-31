#!/usr/bin/env bash
set -Eeuo pipefail

# ----------- visual helpers ----------
log(){ echo -e "\e[1;32m[+]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }
err(){ echo -e "\e[1;31m[x]\e[0m $*"; exit 1; }

prompt(){
  local msg="$1"; local def="${2:-}"; local out
  if [ -n "$def" ]; then
    read -r -p "$msg [${def}]: " out || true
    echo "${out:-$def}"
  else
    read -r -p "$msg: " out || true
    echo "$out"
  fi
}

prompt_secret_once(){
  local msg="$1"; local out
  read -r -s -p "$msg: " out || true; echo
  echo "$out"
}

confirm(){
  local msg="$1"; local def="${2:-Y}"; local ans
  case "$def" in
    Y|y) read -r -p "$msg [Y/n]: " ans || true; ans=${ans:-Y} ;;
    N|n) read -r -p "$msg [y/N]: " ans || true; ans=${ans:-N} ;;
    *)   read -r -p "$msg [y/n]: " ans || true ;;
  esac
  [[ "$ans" =~ ^[Yy]$ ]]
}

randpass(){ tr -dc 'A-Za-z0-9!@#%+=?' < /dev/urandom | head -c 22; }

preflight_ubuntu(){
  . /etc/os-release
  case "$VERSION_ID" in 22.04|24.04) : ;; *)
      warn "Ubuntu $VERSION_ID detected; tested on 22.04/24.04." ;;
  esac
}

ed -s modules/common.sh <<'ED'
/# ------------ config state -------------/i
# ------------ helpers -------------
randpass() {
  # Generate a 22-char strong password from /dev/urandom
  LC_ALL=C tr -dc 'A-Za-z0-9!@#%+=?' </dev/urandom | head -c 22
}
.
wq
ED

# ------------ config state -------------
TZ="${TZ:-$(cat /etc/timezone 2>/dev/null || echo Etc/UTC)}"
PUID="${PUID:-1001}"
PGID="${PGID:-1001}"
ENABLE_TRAEFIK="${ENABLE_TRAEFIK:-yes}"
HTTP_PORT="${HTTP_PORT:-8000}"
DOMAIN="${DOMAIN:-paperless.example.com}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-admin@example.com}"

POSTGRES_VERSION="${POSTGRES_VERSION:-15}"
POSTGRES_DB="${POSTGRES_DB:-paperless}"
POSTGRES_USER="${POSTGRES_USER:-paperless}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(randpass)}"

PAPERLESS_ADMIN_USER="${PAPERLESS_ADMIN_USER:-admin}"
PAPERLESS_ADMIN_PASSWORD="${PAPERLESS_ADMIN_PASSWORD:-$(randpass)}"

RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/paperless}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
CRON_TIME="${CRON_TIME:-30 3 * * *}"

ENV_BACKUP_MODE="${ENV_BACKUP_MODE:-openssl}"              # none|plain|openssl
ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"
INCLUDE_COMPOSE_IN_BACKUP="${INCLUDE_COMPOSE_IN_BACKUP:-yes}" # yes|no

# Paths within data root
DIR_EXPORT=""
DIR_MEDIA=""
DIR_DATA=""
DIR_CONSUME=""
DIR_DB=""
DIR_TIKA_CACHE=""
COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"

compute_paths(){
  DIR_EXPORT="${DATA_ROOT}/export"
  DIR_MEDIA="${DATA_ROOT}/media"
  DIR_DATA="${DATA_ROOT}/data"
  DIR_CONSUME="${DATA_ROOT}/consume"
  DIR_DB="${DATA_ROOT}/db"
  DIR_TIKA_CACHE="${DATA_ROOT}/tika-cache"
}

# Load defaults from a remote .env-style file
load_env_defaults_from(){
  local url="$1"
  local tmp="/tmp/env.defaults.$$"
  curl -fsSL "$url" -o "$tmp" || return 0
  set -a
  # shellcheck disable=SC1090
  source "$tmp"
  set +a
  rm -f "$tmp"
}

merge_env_file(){
  # Load a local or remote .env fragment
  local src="$1"
  local tmp="/tmp/env.merge.$$"
  if [[ "$src" =~ ^https?:// ]]; then
    curl -fsSL "$src" -o "$tmp" || err "Unable to fetch preset: $src"
  else
    [ -f "$src" ] || err "Preset file not found: $src"
    cp "$src" "$tmp"
  fi
  dos2unix "$tmp" >/dev/null 2>&1 || true
  set -a
  # shellcheck disable=SC1090
  source "$tmp"
  set +a
  rm -f "$tmp"
}

pick_and_merge_preset(){
  local base="$1"
  echo
  log "Presets (optional): you can load defaults from repo/local/URL."
  echo "  1) Use repo preset: traefik.env"
  echo "  2) Use repo preset: direct.env"
  echo "  3) Provide a URL to a .env"
  echo "  4) Provide a local path to a .env"
  echo "  5) Skip"
  local choice; choice=$(prompt "Choose [1-5]" "5")
  case "$choice" in
    1) merge_env_file "${base}/presets/traefik.env" ;;
    2) merge_env_file "${base}/presets/direct.env" ;;
    3) local u; u=$(prompt "Preset URL"); [ -n "$u" ] && merge_env_file "$u" ;;
    4) local p; p=$(prompt "Local preset path"); [ -n "$p" ] && merge_env_file "$p" ;;
    *) : ;;
  esac
}

prompt_core_values(){
  echo
  echo "Press Enter to accept the [default] value, or type a custom value."
  TZ=$(prompt "Timezone (IANA, e.g., Pacific/Auckland; Enter=default)" "$TZ")
  INSTANCE_NAME=$(prompt "Instance name (Enter=default)" "${INSTANCE_NAME:-paperless}")
  DATA_ROOT=$(prompt "Data root (persistent storage; Enter=default)" "${DATA_ROOT:-/home/docker/paperless}")
  STACK_DIR=$(prompt "Stack dir (where docker-compose.yml lives; Enter=default)" "${STACK_DIR:-/home/docker/paperless-setup}")

  PAPERLESS_ADMIN_USER=$(prompt "Paperless admin username (Enter=default)" "$PAPERLESS_ADMIN_USER")
  PAPERLESS_ADMIN_PASSWORD=$(prompt "Paperless admin password (Enter=default)" "$PAPERLESS_ADMIN_PASSWORD")
  POSTGRES_PASSWORD=$(prompt "Postgres password (Enter=default)" "$POSTGRES_PASSWORD")

  ENABLE_TRAEFIK=$(prompt "Enable Traefik with HTTPS? (yes/no; Enter=default)" "$ENABLE_TRAEFIK")
  if [ "$ENABLE_TRAEFIK" = "yes" ]; then
    DOMAIN=$(prompt "Domain for Paperless (DNS A/AAAA must point here; Enter=default)" "$DOMAIN")
    LETSENCRYPT_EMAIL=$(prompt "Let's Encrypt email (Enter=default)" "$LETSENCRYPT_EMAIL")
    PAPERLESS_URL="https://${DOMAIN}"
  else
    HTTP_PORT=$(prompt "Bind Paperless on host port (Enter=default)" "$HTTP_PORT")
    PAPERLESS_URL="http://localhost:${HTTP_PORT}"
  fi

  RCLONE_REMOTE_NAME=$(prompt "rclone remote name (Enter=default)" "$RCLONE_REMOTE_NAME")
  RCLONE_REMOTE_PATH=$(prompt "Remote path for backups (Enter=default)" "backups/paperless/${INSTANCE_NAME}")
  RETENTION_DAYS=$(prompt "Retention days (Enter=default)" "$RETENTION_DAYS")
  CRON_TIME=$(prompt "Backup cron (m h dom mon dow; Enter=default)" "$CRON_TIME")

  ENV_BACKUP_MODE=$(prompt "Include .env in backups? (none/plain/openssl; Enter=default)" "$ENV_BACKUP_MODE")
  if [ "$ENV_BACKUP_MODE" = "openssl" ]; then
    ENV_BACKUP_PASSPHRASE_FILE=$(prompt "Passphrase file path (non-interactive for cron; Enter=default)" "$ENV_BACKUP_PASSPHRASE_FILE")
  fi

  INCLUDE_COMPOSE_IN_BACKUP=$(prompt "Include compose snapshot in backups? (yes/no; Enter=default)" "$INCLUDE_COMPOSE_IN_BACKUP")

  compute_paths
}

final_summary(){
  echo
  log "URLs"
  if [ "$ENABLE_TRAEFIK" = "yes" ]; then
    echo "  Paperless: https://${DOMAIN}"
  else
    echo "  Paperless: http://<server-ip>:${HTTP_PORT}"
  fi
  echo
  log "Admin"
  echo "  User: ${PAPERLESS_ADMIN_USER}"
  echo "  Pass: (as set above)"
  echo
  log "Backups"
  echo "  Remote: ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  echo "  Cron:   ${CRON_TIME}"
  echo "  .env mode: ${ENV_BACKUP_MODE} (passfile: ${ENV_BACKUP_PASSPHRASE_FILE})"
  echo
  log "Tools"
  echo "  Run 'bulletproof' anytime for backup/restore/menu."
}

cleanup_tmp(){ rm -rf "$1" || true; }
