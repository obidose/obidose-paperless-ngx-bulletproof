#!/usr/bin/env bash
set -Eeuo pipefail

# ===== Pretty output (ASCII only) =====
BLUE="\e[34m"; GREEN="\e[32m"; YEL="\e[33m"; RED="\e[31m"; OFF="\e[0m"
say(){  echo -e "${BLUE}[*]${OFF} $*"; }
ok(){   echo -e "${GREEN}[ok]${OFF} $*"; }
warn(){ echo -e "${YEL}[!]${OFF} $*"; }
die(){  echo -e "${RED}[x]${OFF} $*"; exit 1; }

# ===== Locations (same defaults as installer) =====
STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"
COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"

# Reasonable defaults if .env missing
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
ENABLE_TRAEFIK="${ENABLE_TRAEFIK:-yes}"
DOMAIN="${DOMAIN:-paperless.example.com}"
HTTP_PORT="${HTTP_PORT:-8000}"
RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"

# ===== Helpers =====
need(){ command -v "$1" >/dev/null 2>&1 || die "Missing dependency: $1"; }
have(){ command -v "$1" >/dev/null 2>&1; }

load_env(){
  if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
    # Re-apply common vars if provided by .env
    ENABLE_TRAEFIK="${ENABLE_TRAEFIK:-$ENABLE_TRAEFIK}"
    DOMAIN="${DOMAIN:-$DOMAIN}"
    HTTP_PORT="${HTTP_PORT:-$HTTP_PORT}"
    INSTANCE_NAME="${INSTANCE_NAME:-$INSTANCE_NAME}"
    RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-$RCLONE_REMOTE_NAME}"
    RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-$RCLONE_REMOTE_PATH}"
  fi
}

dc(){ docker compose -f "$COMPOSE_FILE" "$@"; }

# Health string for containers
health_of(){
  local id="$1"
  docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo "unknown"
}

# ===== Existing actions (pass-through if your backup script exists) =====
backup_now(){
  if [[ -x "${STACK_DIR}/backup.sh" ]]; then
    "${STACK_DIR}/backup.sh"
  else
    warn "No ${STACK_DIR}/backup.sh found. Skipping."
    return 1
  fi
}

list_snapshots(){
  need rclone
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | awk '{print $NF}' | sort
}

restore_snapshot(){
  local snap="${1:-}"
  if [[ -z "$snap" ]]; then
    say "Available snapshots:"
    list_snapshots || true
    read -r -p "Enter snapshot name to restore: " snap
  fi
  if [[ -x "${STACK_DIR}/restore.sh" ]]; then
    "${STACK_DIR}/restore.sh" "$snap"
  else
    warn "No ${STACK_DIR}/restore.sh found. Use the installer’s restore flow for now."
    return 1
  fi
}

# ===== New: STATUS =====
cmd_status(){
  need docker
  [[ -f "$COMPOSE_FILE" ]] || die "Compose file not found: $COMPOSE_FILE"
  say "Compose file: $COMPOSE_FILE"
  echo

  say "Containers:"
  dc ps || true
  echo

  say "Health summary:"
  local ids; ids="$(dc ps -q || true)"
  if [[ -n "$ids" ]]; then
    while read -r id; do
      [[ -z "$id" ]] && continue
      local name; name="$(docker inspect --format='{{.Name}}' "$id" 2>/dev/null | sed 's#^/##')"
      local h; h="$(health_of "$id")"
      printf "  %-30s  %s\n" "$name" "$h"
    done <<< "$ids"
  else
    echo "  (no containers reported)"
  fi
  echo

  say "Ports (host -> container):"
  dc ps --format '{{.Service}} {{.Publishers}}' 2>/dev/null || echo "  (compose format not supported; shown above)"
  echo
}

# ===== New: LOGS =====
cmd_logs(){
  need docker
  local svc="" lines=200
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n) shift; lines="${1:-200}"; shift || true ;;
      -n*) lines="${1#-n}"; shift ;;
      *) svc="$1"; shift ;;
    esac
  done
  if [[ -n "$svc" ]]; then
    say "Last ${lines} lines for service '${svc}'"
    dc logs --no-color --tail "${lines}" "$svc"
  else
    say "Last ${lines} lines for all services"
    dc logs --no-color --tail "${lines}"
  fi
}

# ===== New: DOCTOR =====
cmd_doctor(){
  load_env
  say "System checks"
  echo "  Kernel: $(uname -srmo)" || true
  echo "  Uptime: $(uptime -p 2>/dev/null || true)"
  echo

  say "Disk space"
  df -h / "$DATA_ROOT" 2>/dev/null || df -h /
  echo

  say "Docker"
  need docker
  docker --version || true
  systemctl is-active docker >/dev/null 2>&1 && echo "  Docker service: active" || echo "  Docker service: unknown or inactive"
  echo

  say "Compose file"
  if [[ -f "$COMPOSE_FILE" ]]; then
    echo "  Found: $COMPOSE_FILE"
  else
    warn "Compose file missing: $COMPOSE_FILE"
  fi
  echo

  say "Containers & health"
  dc ps || true
  local unhealthy=0
  while read -r id; do
    [[ -z "$id" ]] && continue
    h="$(health_of "$id")"
    if [[ "$h" != "healthy" && "$h" != "running" ]]; then unhealthy=$((unhealthy+1)); fi
  done < <(dc ps -q || true)
  [[ $unhealthy -gt 0 ]] && warn "Unhealthy/not running containers detected: $unhealthy" || ok "All containers healthy/running"
  echo

  say "Paperless HTTP probe"
  local url
  if [[ "${ENABLE_TRAEFIK,,}" == "yes" ]]; then
    url="https://${DOMAIN}"
  else
    url="http://127.0.0.1:${HTTP_PORT}"
  fi
  if have curl; then
    code="$(curl -k -sS -o /dev/null -m 8 -w '%{http_code}' "$url" || echo 000)"
    echo "  GET $url -> $code"
    [[ "$code" =~ ^2|3 ]] && ok "Paperless responds" || warn "Unexpected HTTP code"
  else
    warn "curl not installed; skipping HTTP probe."
  fi
  echo

  say "Database probe"
  if dc ps db >/dev/null 2>&1; then
    if dc exec -T db pg_isready -U "${POSTGRES_USER:-paperless}" -d "${POSTGRES_DB:-paperless}" >/dev/null 2>&1; then
      ok "Postgres is ready"
    else
      warn "pg_isready failed"
    fi
  else
    warn "db service not found in compose"
  fi
  echo

  say "rclone remote"
  if have rclone; then
    if rclone listremotes | grep -qx "${RCLONE_REMOTE_NAME}:"; then
      rclone about "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1 && ok "rclone remote works" || warn "rclone about failed"
      echo "  Remote path: ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
      # Show top-level of backup path (non-fatal)
      rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | tail -n5 | sed 's/^/    /' || true
    else
      warn "Remote '${RCLONE_REMOTE_NAME}:' not defined"
    fi
  else
    warn "rclone not installed"
  fi
  echo

  say "Cron"
  if crontab -l >/dev/null 2>&1; then
    crontab -l | sed 's/^/  /' | grep -E "paperless|backup|rclone|tar" || echo "  (no matching backup cron lines found)"
  else
    warn "No crontab for current user"
  fi
  echo

  say "Traefik (if enabled)"
  if [[ "${ENABLE_TRAEFIK,,}" == "yes" ]]; then
    if dc ps traefik >/dev/null 2>&1; then
      dc ps traefik || true
      # Last cert events (non-fatal)
      dc logs --tail 50 traefik 2>/dev/null | grep -i -E "acme|certificate|tls" | tail -n 10 | sed 's/^/  /' || true
    else
      warn "traefik service not found in compose"
    fi
  else
    echo "  Traefik disabled."
  fi
  echo

  ok "Doctor finished."
}

# ===== Menu =====
usage(){
  cat <<EOF
Usage: $(basename "$0") [command]

Commands:
  menu              Interactive menu
  backup            Run backup now (calls ${STACK_DIR}/backup.sh if present)
  list              List available snapshots (rclone)
  restore [SNAP]    Restore a snapshot (calls ${STACK_DIR}/restore.sh if present)

  status            Show container status, health, and ports
  logs [svc] [-n N] Show recent logs for stack or a specific service
  doctor            Run diagnostics (disk, Docker, HTTP, DB, rclone, cron, Traefik)

Examples:
  $(basename "$0") status
  $(basename "$0") logs web -n 200
  $(basename "$0") doctor
EOF
}

menu(){
  load_env
  while true; do
    echo
    say "Bulletproof menu"
    echo "  1) Backup now"
    echo "  2) List snapshots"
    echo "  3) Restore snapshot"
    echo "  4) Status"
    echo "  5) Logs"
    echo "  6) Doctor"
    echo "  0) Quit"
    read -r -p "Choose: " ans
    case "$ans" in
      1) backup_now ;;
      2) list_snapshots || true ;;
      3) restore_snapshot ;;
      4) cmd_status ;;
      5) read -r -p "Service (blank=all): " svc; read -r -p "Lines [200]: " n; n="${n:-200}"; cmd_logs ${svc:+$svc} -n "$n" ;;
      6) cmd_doctor ;;
      0) exit 0 ;;
      *) echo "Unknown choice";;
    esac
  done
}

# ===== Dispatch =====
load_env

case "${1:-menu}" in
  menu)    menu ;;
  backup)  backup_now ;;
  list)    list_snapshots ;;
  restore) shift; restore_snapshot "${1:-}" ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "$@" ;;
  doctor)  cmd_doctor ;;
  -h|--help|help) usage ;;
  *)        usage; exit 1 ;;
esac
