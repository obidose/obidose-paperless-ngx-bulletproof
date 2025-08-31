# modules/common.sh
# Shared helpers + default config for the Paperless-ngx Bulletproof installer

# Guard against double sourcing
[ -n "${__COMMON_SH__:-}" ] && return 0
__COMMON_SH__=1

# ---------- color + messaging fallbacks (install.sh defines these; we provide safe defaults) ----------
COLOR_BLUE=${COLOR_BLUE:-"\e[1;34m"}
COLOR_GREEN=${COLOR_GREEN:-"\e[1;32m"}
COLOR_YELLOW=${COLOR_YELLOW:-"\e[1;33m"}
COLOR_RED=${COLOR_RED:-"\e[1;31m"}
COLOR_OFF=${COLOR_OFF:-"\e[0m"}

if ! command -v say >/dev/null 2>&1; then
  say(){  echo -e "${COLOR_BLUE}[•]${COLOR_OFF} $*"; }
fi
if ! command -v ok >/dev/null 2>&1; then
  ok(){   echo -e "${COLOR_GREEN}[✓]${COLOR_OFF} $*"; }
fi
if ! command -v warn >/dev/null 2>&1; then
  warn(){ echo -e "${COLOR_YELLOW}[!]${COLOR_OFF} $*"; }
fi
if ! command -v die >/dev/null 2>&1; then
  die(){  echo -e "${COLOR_RED}[x]${COLOR_OFF} $*"; exit 1; }
fi
if ! command -v log >/dev/null 2>&1; then
  log(){  say "$@"; }
fi

# ---------- basic sanity ----------
preflight_ubuntu() {
  if [ ! -r /etc/os-release ]; then
    warn "/etc/os-release not found; continuing anyway."
    return 0
  fi
  . /etc/os-release
  case "$VERSION_ID" in
    22.04|24.04) : ;;
    *) warn "Ubuntu $VERSION_ID detected; tested on 22.04/24.04." ;;
  esac
}

# ---------- helpers ----------
randpass() {
  LC_ALL=C tr -dc 'A-Za-z0-9!@#%+=?' </dev/urandom | head -c 22
}

prompt(){
  local msg="$1"; local def="${2:-}"; local out
  if [ -n "$def" ]; then
    read -r -p "$msg [$def]: " out || true
    echo "${out:-$def}"
  else
    read -r -p "$msg: " out || true
    echo "$out"
  fi
}

prompt_secret(){
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

# ---------- default instance paths (installer may export these earlier) ----------
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"

# ---------- config state (safe defaults) ----------
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
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
CRON_TIME="${CRON_TIME:-30 3 * * *}"

ENV_BACKUP_MODE="${ENV_BACKUP_MODE:-openssl}"                  # none|plain|openssl
ENV_BACKUP_PASSPHRASE_FILE="${ENV_BACKUP_PASSPHRASE_FILE:-/root/.paperless_env_pass}"
INCLUDE_COMPOSE_IN_BACKUP="${INCLUDE_COMPOSE_IN_BACKUP:-yes}"  # yes|no

# ---------- derived paths ----------
DIR_EXPORT="${DATA_ROOT}/export"
DIR_MEDIA="${DATA_ROOT}/media"
DIR_DATA="${DATA_ROOT}/data"
DIR_CONSUME="${DATA_ROOT}/consume"
DIR_DB="${DATA_ROOT}/db"
DIR_TIKA_CACHE="${DATA_ROOT}/tika-cache"

COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"

# ---------- tiny utilities used by other modules ----------
ensure_dir_tree() {
  mkdir -p "$STACK_DIR" "$DATA_ROOT" \
           "$DIR_EXPORT" "$DIR_MEDIA" "$DIR_DATA" "$DIR_CONSUME" "$DIR_DB" "$DIR_TIKA_CACHE"
}

# Load a local or remote .env fragment
merge_env_file(){
  local src="$1" tmp="/tmp/env.merge.$$"
  if [[ "$src" =~ ^https?:// ]]; then
    curl -fsSL "$src" -o "$tmp" || die "Unable to fetch preset: $src"
  else
    [ -f "$src" ] || die "Preset file not found: $src"
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
  say "Presets (optional): load defaults from repo/local/URL."
  echo "  1) Repo preset: traefik.env"
  echo "  2) Repo preset: direct.env"
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
  INSTANCE_NAME=$(prompt "Instance name (Enter=default)" "${INSTANCE_NAME}")
  DATA_ROOT=$(prompt "Data root (persistent storage; Enter=default)" "${DATA_ROOT}")
  STACK_DIR=$(prompt "Stack dir (where docker-compose.yml lives; Enter=default)" "${STACK_DIR}")

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

  # update derived paths if DATA_ROOT changed
  DIR_EXPORT="${DATA_ROOT}/export"
  DIR_MEDIA="${DATA_ROOT}/media"
  DIR_DATA="${DATA_ROOT}/data"
  DIR_CONSUME="${DATA_ROOT}/consume"
  DIR_DB="${DATA_ROOT}/db"
  DIR_TIKA_CACHE="${DATA_ROOT}/tika-cache"
}
