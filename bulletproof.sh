#!/usr/bin/env bash
set -Eeuo pipefail
set -o errtrace
trap 'echo -e "\e[1;31m[x]\e[0m Error at ${BASH_SOURCE}:${LINENO}: ${BASH_COMMAND}"; exit 1' ERR

# Simple TUI for Paperless-ngx "bulletproof" management
# Assumes the wizard was used, so STACK_DIR/.env exist.

# ---- config discovery ----
DEFAULT_STACK_DIR="/home/docker/paperless-setup"
ENV_FILE="${ENV_FILE:-$DEFAULT_STACK_DIR/.env}"

need_root(){ [ "$(id -u)" -eq 0 ] || { echo "[x] Run as root"; exit 1; }; }
log(){ echo -e "\e[1;32m[+]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }
pause(){ read -r -p "Press Enter to continue… " _; }

find_env(){
  if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$DEFAULT_STACK_DIR/.env" ]; then
      ENV_FILE="$DEFAULT_STACK_DIR/.env"
    else
      warn "Could not find .env. Enter path to your stack .env (e.g., /home/docker/paperless-setup/.env)"
      read -r -p "Path: " ENV_FILE
      [ -f "$ENV_FILE" ] || { echo "[x] File not found"; exit 1; }
    fi
  fi
  set -a; source "$ENV_FILE"; set +a
  : "${STACK_DIR:=$DEFAULT_STACK_DIR}"
  : "${DATA_ROOT:=/home/docker/paperless}"
  : "${RCLONE_REMOTE_NAME:=pcloud}"
  : "${RCLONE_REMOTE_PATH:=backups/paperless/paperless}"
  : "${PAPERLESS_URL:=http://localhost:8000}"
  COMPOSE_YML="$STACK_DIR/docker-compose.yml"
}

dc(){ docker compose -f "$COMPOSE_YML" "$@"; }

latest_snap(){
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" 2>/dev/null | awk '{print $NF}' | sort | tail -n1
}

backup_now(){
  log "Running backup now…"
  "$STACK_DIR/backup_to_pcloud.sh"
  log "Done."
}

show_status(){
  echo; log "Containers"
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
  echo; log "URL"
  echo "  ${PAPERLESS_URL}"
  echo; log "Cron line (if present)"
  grep -F "backup_to_pcloud.sh" /etc/crontab || echo "(no cron line found)"
  echo; log "Disk usage (data root)"
  du -sh "$DATA_ROOT" 2>/dev/null || true
  echo
  pause
}

list_snaps(){
  log "Snapshots under ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" | sort -k2 || true
  echo
  pause
}

restore_latest(){
  local s; s=$(latest_snap || true)
  [ -n "$s" ] || { echo "[x] No snapshots found."; pause; return; }
  read -r -p "Restore latest snapshot '$s'? [y/N]: " ans; ans=${ans:-N}
  [[ "$ans" =~ ^[Yy]$ ]] || return
  restore_named "$s"
}

restore_choose(){
  list_snaps
  read -r -p "Enter snapshot to restore (exact folder name): " s
  [ -n "$s" ] || return
  restore_named "$s"
}

restore_named(){
  local s="$1"
  local base="${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  local tmp="${STACK_DIR}/restore_${s}"
  log "Fetching ${base}/${s} -> ${tmp}"
  mkdir -p "$tmp"
  rclone copy "${base}/${s}" "$tmp" --fast-list

  log "Stopping stack"
  dc down || true

  log "Restoring media/data/export -> ${DATA_ROOT}"
  for a in media data export; do
    if [ -f "$tmp/${a}.tar.gz" ]; then
      tar -C "${DATA_ROOT}" -xzf "$tmp/${a}.tar.gz"
    else
      warn "Missing archive for ${a} (continuing)"
    fi
  done

  log "Restoring database"
  if [ -f "$tmp/postgres.sql" ]; then
    dc up -d db
    sleep 8
    dc exec -T db psql -U "$POSTGRES_USER" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${POSTGRES_DB}' AND pid <> pg_backend_pid();" || true
    dc exec -T db psql -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\"; CREATE DATABASE \"${POSTGRES_DB}\";"
    cat "$tmp/postgres.sql" | dc exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB"
  else
    warn "No postgres.sql in snapshot!"
  fi

  log "Starting full stack"
  dc up -d
  log "Restore complete."
  echo
  pause
}

tail_backup_log(){
  local logf="$STACK_DIR/backup.log"
  [ -f "$logf" ] || { warn "No backup.log yet (cron hasn't run)."; pause; return; }
  tail -n 200 "$logf"
  echo
  pause
}

restart_stack(){ dc down || true; dc up -d; log "Restarted."; pause; }
upgrade_stack(){ dc pull; dc up -d; log "Upgraded images and restarted."; pause; }

edit_env(){ ${EDITOR:-nano} "$ENV_FILE"; echo; log "Saved. Changes apply to next run."; pause; }

verify_rclone(){
  log "rclone remotes:"
  rclone listremotes || true
  echo
  log "Listing ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}"
  rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" || true
  echo
  pause
}

menu(){
  clear
  cat <<MENU
Paperless-ngx • bulletproof

  1) Run backup now
  2) Show status
  3) List snapshots
  4) Restore latest snapshot
  5) Restore a chosen snapshot
  6) Tail backup log
  7) Restart stack
  8) Upgrade (pull images + up -d)
  9) Edit stack .env
  0) Exit
MENU
  echo -n "Choose: "
}

main(){
  need_root
  find_env
  while true; do
    menu
    read -r ans
    case "$ans" in
      1) backup_now ;;
      2) show_status ;;
      3) list_snaps ;;
      4) restore_latest ;;
      5) restore_choose ;;
      6) tail_backup_log ;;
      7) restart_stack ;;
      8) upgrade_stack ;;
      9) edit_env ;;
      0) exit 0 ;;
      *) echo "?" ;;
    esac
  done
}

main "$@"
