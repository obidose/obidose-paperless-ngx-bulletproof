#!/usr/bin/env bash
# bulletproof.sh — Paperless-ngx “bulletproof” helper
# - Backup/List/Restore snapshots via rclone (pCloud remote by default)
# - Status/Logs/Doctor utilities
# Respects:
#   STACK_DIR (default: /home/docker/paperless-setup)
#   DATA_ROOT (default: /home/docker/paperless)
#   ENV_FILE  (default: ${STACK_DIR}/.env)
#   COMPOSE_FILE (default: ${STACK_DIR}/docker-compose.yml)
# And values from .env:
#   INSTANCE_NAME, RCLONE_REMOTE_NAME, RCLONE_REMOTE_PATH
set -Eeuo pipefail

# ---------- pretty ----------
COLOR_BLUE="\e[1;34m"; COLOR_GREEN="\e[1;32m"; COLOR_YELLOW="\e[1;33m"; COLOR_RED="\e[1;31m"; COLOR_OFF="\e[0m"
say(){  echo -e "${COLOR_BLUE}[*]${COLOR_OFF} $*"; }
ok(){   echo -e "${COLOR_GREEN}[ok]${COLOR_OFF} $*"; }
warn(){ echo -e "${COLOR_YELLOW}[!]${COLOR_OFF} $*"; }
die(){  echo -e "${COLOR_RED}[x]${COLOR_OFF} $*"; exit 1; }

trap 'code=$?; echo -e "${COLOR_RED}[x]${COLOR_OFF} bulletproof failed at ${BASH_SOURCE[0]}:${LINENO} (exit ${code})"; exit $code' ERR

# ---------- locations & defaults ----------
STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"
ENV_FILE="${ENV_FILE:-${STACK_DIR}/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-${STACK_DIR}/docker-compose.yml}"

# Load .env if present so we inherit INSTANCE_NAME, RCLONE_* etc.
if [ -f "$ENV_FILE" ]; then
  set +u
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set -u
fi

INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
RCLONE_REMOTE_NAME="${RCLONE_REMOTE_NAME:-pcloud}"
RCLONE_REMOTE_PATH="${RCLONE_REMOTE_PATH:-backups/paperless/${INSTANCE_NAME}}"

BACKUP_SH="${STACK_DIR}/backup.sh"
RESTORE_SH="${STACK_DIR}/restore.sh"

dc(){ docker compose -f "$COMPOSE_FILE" "$@"; }

need_cmds(){
  for c in docker rclone awk sed grep cut; do
    command -v "$c" >/dev/null 2>&1 || die "Missing command: $c"
  done
}

check_stack(){
  [ -d "$STACK_DIR" ] || die "Stack dir not found: ${STACK_DIR}"
  [ -f "$COMPOSE_FILE" ] || die "Compose file not found: ${COMPOSE_FILE}"
  if [ ! -f "$ENV_FILE" ]; then
    warn "No .env at ${ENV_FILE} (continuing with defaults)."
  fi
}

check_remote_exists(){
  if ! rclone listremotes | grep -qx "${RCLONE_REMOTE_NAME}:"; then
    die "rclone remote '${RCLONE_REMOTE_NAME}:' not found. Re-run the installer or 'rclone config'."
  fi
}

check_remote_reachable(){
  # Works for both pcloud and webdav types
  if rclone about "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
    return 0
  else
    # Some backends don't support "about" — try a harmless lsd on root
    if rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

# ---------- actions ----------
do_backup(){
  need_cmds; check_stack; check_remote_exists
  if [ ! -x "$BACKUP_SH" ]; then
    if [ -f "$BACKUP_SH" ]; then
      chmod +x "$BACKUP_SH" || true
    else
      warn "No ${BACKUP_SH} found."
      echo "Install it with:"
      echo "  curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/modules/backup.sh \\"
      echo "    -o ${BACKUP_SH} && chmod +x ${BACKUP_SH}"
      exit 1
    fi
  fi
  if ! check_remote_reachable; then
    warn "rclone remote '${RCLONE_REMOTE_NAME}:' not reachable right now."
  fi
  say "Running backup script at ${BACKUP_SH}"
  ( cd "$STACK_DIR" && \
    RCLONE_REMOTE_NAME="$RCLONE_REMOTE_NAME" \
    RCLONE_REMOTE_PATH="$RCLONE_REMOTE_PATH" \
    bash "$BACKUP_SH" )
  ok "Backup completed (remote: ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH})."
}

do_list(){
  need_cmds; check_remote_exists
  say "Listing snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  if ! rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | awk '{print $NF}' | sort; then
    warn "No snapshots found or remote not reachable."
  fi
}

do_restore(){
  need_cmds; check_stack; check_remote_exists
  if [ -x "$RESTORE_SH" ]; then
    do_list
    read -r -p "Enter snapshot name to restore: " snap
    [ -n "$snap" ] || die "No snapshot specified."
    ( cd "$STACK_DIR" && \
      RCLONE_REMOTE_NAME="$RCLONE_REMOTE_NAME" \
      RCLONE_REMOTE_PATH="$RCLONE_REMOTE_PATH" \
      SNAPSHOT="$snap" \
      bash "$RESTORE_SH" )
    ok "Restore invoked."
  else
    warn "No ${RESTORE_SH} found."
    warn "Use the installer's restore flow (it guides through pCloud + snapshot selection)."
    exit 1
  fi
}

do_status(){
  need_cmds; check_stack
  say "Docker Compose status"
  dc ps || true
  echo
  say "Container health/ports"
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | sed -n '1,200p'
  echo
  say "Paperless URL (if in .env / traefik mode)"
  if grep -q '^PAPERLESS_URL=' "$ENV_FILE" 2>/dev/null; then
    awk -F= '/^PAPERLESS_URL=/{print $2}' "$ENV_FILE"
  else
    echo "(not set)"
  fi
  echo
  say "Disk usage"
  df -h | sed -n '1,200p'
}

do_logs(){
  need_cmds; check_stack
  local target="${1:-}"
  if [ -n "$target" ]; then
    say "Logs for service: $target (last 200 lines)"
    dc logs --tail=200 --timestamps "$target" || die "No such service: $target"
    return 0
  fi
  say "Recent logs (last 200 lines per common service)"
  local svcs=(paperless db traefik tika gotenberg redis)
  for s in "${svcs[@]}"; do
    echo -e "\n==== $s ===="
    dc logs --tail=200 --timestamps "$s" || true
  done
}

do_doctor(){
  need_cmds
  say "Doctor: quick checks"
  echo "- STACK_DIR: $STACK_DIR"
  echo "- COMPOSE_FILE: $COMPOSE_FILE $( [ -f "$COMPOSE_FILE" ] && echo '[ok]' || echo '[missing]' )"
  echo "- ENV_FILE: $ENV_FILE $( [ -f "$ENV_FILE" ] && echo '[ok]' || echo '[missing]' )"
  echo "- INSTANCE_NAME: $INSTANCE_NAME"
  echo "- Remote: ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  if check_remote_exists && check_remote_reachable; then
    ok "rclone remote reachable"
  else
    warn "rclone remote not reachable (run 'rclone config' or re-run installer)."
  fi
  if docker info >/dev/null 2>&1; then
    ok "Docker reachable"
  else
    warn "Docker daemon not reachable."
  fi
  if [ -f "$COMPOSE_FILE" ]; then
    if dc ps >/dev/null 2>&1; then
      ok "Compose file OK"
    else
      warn "Compose reports an error (run 'docker compose -f \"$COMPOSE_FILE\" ps' manually)."
    fi
  fi
  # show unhealthy containers if any
  if docker ps --format '{{.Names}} {{.Status}}' | grep -qi 'unhealthy'; then
    warn "Unhealthy containers detected:"
    docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -i 'unhealthy' || true
  else
    ok "No unhealthy containers reported"
  fi
}

usage(){
  cat <<USAGE
Usage: bulletproof [command] [args]

Commands:
  backup             Run ${BACKUP_SH} if present
  list               List pCloud snapshots at ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}
  restore            Run ${RESTORE_SH} if present
  status             Show docker status, ports, disk usage
  logs [service]     Tail last 200 lines (optionally for a specific service)
  doctor             Quick health checks
  help               This help

If no command is given, an interactive menu opens.
USAGE
}

menu(){
  while :; do
    echo
    echo "[*] Bulletproof menu"
    echo "  1) Backup now"
    echo "  2) List snapshots"
    echo "  3) Restore snapshot"
    echo "  4) Status"
    echo "  5) Logs"
    echo "  6) Doctor"
    echo "  0) Quit"
    read -r -p "Choose: " c
    case "${c:-}" in
      1) do_backup ;;
      2) do_list ;;
      3) do_restore ;;
      4) do_status ;;
      5) do_logs ;;
      6) do_doctor ;;
      0) exit 0 ;;
      *) echo "Invalid option" ;;
    esac
  done
}

main(){
  case "${1:-}" in
    backup)  shift; do_backup "$@";;
    list)    shift; do_list "$@";;
    restore) shift; do_restore "$@";;
    status)  shift; do_status "$@";;
    logs)    shift; do_logs "$@";;
    doctor)  shift; do_doctor "$@";;
    -h|--help|help) usage ;;
    "") menu ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
