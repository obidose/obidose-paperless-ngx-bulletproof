#!/usr/bin/env bash
set -Eeuo pipefail

log(){ echo -e "\e[1;32m[+]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }
err(){ echo -e "\e[1;31m[x]\e[0m $*"; exit 1; }
need_root(){ [ "$(id -u)" -eq 0 ] || err "Run as root."; }

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

confirm(){
  local msg="$1"; local def="${2:-Y}"; local ans
  case "$def" in
    Y|y) read -r -p "$msg [Y/n]: " ans || true; ans=${ans:-Y} ;;
    N|n) read -r -p "$msg [y/N]: " ans || true; ans=${ans:-N} ;;
    *)   read -r -p "$msg [y/n]: " ans || true ;;
  esac
  [[ "$ans" =~ ^[Yy]$ ]]
}

randpass(){ LC_ALL=C tr -dc 'A-Za-z0-9!@#%+=?' < /dev/urandom | head -c 22; }

ubuntu_version_ok(){
  . /etc/os-release
  case "$VERSION_ID" in 22.04|24.04) return 0;; *) return 1;; esac
}

# ===== Defaults (can be overridden by environment before sourcing) =====
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"
TIMEZONE_DEFAULT="$(cat /etc/timezone 2>/dev/null || echo "Etc/UTC")"

DOCKER_USER="${DOCKER_USER:-docker}"
DOCKER_UID="${DOCKER_UID:-1001}"
DOCKER_GID="${DOCKER_GID:-1001}"

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
RETENTION_DAYS="${RETENTION_DAYS:-30}"
CRON_TIME="${CRON_TIME:-30 3 * * *}"

COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"
BACKUP_DIR_LOCAL="${BACKUP_DIR_LOCAL:-${STACK_DIR}/local_backups}"
DIR_EXPORT="${DIR_EXPORT:-${DATA_ROOT}/export}"
DIR_MEDIA="${DIR_MEDIA:-${DATA_ROOT}/media}"
DIR_DATA="${DIR_DATA:-${DATA_ROOT}/data}"
DIR_CONSUME="${DIR_CONSUME:-${DATA_ROOT}/consume}"
DIR_DB="${DIR_DB:-${DATA_ROOT}/db}"
DIR_TIKA_CACHE="${DIR_TIKA_CACHE:-${DATA_ROOT}/tika-cache}"
