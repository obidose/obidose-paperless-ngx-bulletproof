#!/usr/bin/env bash
set -Eeuo pipefail

STACK_DIR_DEFAULT="/home/docker/paperless-setup"
ENV_FILE="${STACK_DIR_DEFAULT}/.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$(pwd)/.env"
[ -f "$ENV_FILE" ] || { echo "[x] .env not found. Set STACK_DIR in this script or cd to your stack dir."; exit 1; }

set -a; source "$ENV_FILE"; set +a

menu(){
  clear
  cat <<TXT
Paperless-ngx • Bulletproof Menu
================================
Stack dir:  $STACK_DIR
Data root:  $DATA_ROOT
Remote:     ${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}
Env mode:   ${ENV_BACKUP_MODE} (passfile: ${ENV_BACKUP_PASSPHRASE_FILE})

1) Status
2) Backup now
3) List backups
4) Restore from backup
5) Toggle env backup mode (none/plain/openssl)
6) Set/change env passphrase file
7) Update stack (pull + up -d)
8) Restart paperless service
9) Logs (paperless)
0) Exit
TXT
  read -r -p "Choose: " CH
}

status(){
  echo; docker compose -f "$STACK_DIR/docker-compose.yml" ps; echo
  if [ "$ENABLE_TRAEFIK" = "yes" ]; then
    echo "URL: https://${DOMAIN}"
  else
    echo "URL: http://<server-ip>:${HTTP_PORT}"
  fi
}

backup_now(){ "${STACK_DIR}/backup_to_pcloud.sh"; }

list_backups(){ rclone lsd "${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}" | sort -k2; }

restore(){
  list_backups
  read -r -p "Enter snapshot name: " SNAP
  [ -z "$SNAP" ] && return
  bash -lc "
    set -Eeuo pipefail
    set -a; source '$ENV_FILE'; set +a
    TMP=\$(mktemp -d)
    rclone copy '${RCLONE_REMOTE_NAME}:${RCLONE_REMOTE_PATH}/$SNAP' \"\$TMP\" --fast-list
    cd '$STACK_DIR'
    docker compose down || true
    for a in media data export; do
      [ -f \"\$TMP/\${a}.tar.gz\" ] && tar -C '$DATA_ROOT' -xzf \"\$TMP/\${a}.tar.gz\";
    done
    if [ -f \"\$TMP/env.snapshot\" ] || [ -f \"\$TMP/env.snapshot.enc\" ]; then
      read -r -p 'Use snapshot .env? [Y/n]: ' ANS; ANS=\${ANS:-Y}
      if [[ \$ANS =~ ^[Yy]\$ ]]; then
        if [ -f \"\$TMP/env.snapshot.enc\" ]; then
          if [ -f \"$ENV_BACKUP_PASSPHRASE_FILE\" ]; then
            openssl enc -d -aes-256-cbc -pbkdf2 -salt -pass file:\"$ENV_BACKUP_PASSPHRASE_FILE\" -in \"\$TMP/env.snapshot.enc\" -out '$ENV_FILE'
          else
            read -r -s -p 'Passphrase: ' P; echo
            openssl enc -d -aes-256-cbc -pbkdf2 -salt -pass pass:\"\$P\" -in \"\$TMP/env.snapshot.enc\" -out '$ENV_FILE'
          fi
        else
          cp \"\$TMP/env.snapshot\" '$ENV_FILE'
        fi
      fi
    fi
    if [ -f \"\$TMP/compose.snapshot.yml\" ]; then
      cp \"\$TMP/compose.snapshot.yml\" '$STACK_DIR/docker-compose.yml'
    fi
    docker compose up -d
    rm -rf \"\$TMP\"
  "
}

toggle_env_mode(){
  echo "Current: ${ENV_BACKUP_MODE}"
  read -r -p "Enter new mode (none/plain/openssl): " M
  [ -z "$M" ] && return
  sed -i "s/^ENV_BACKUP_MODE=.*/ENV_BACKUP_MODE=${M}/" "$ENV_FILE"
  echo "Updated. (Will apply to next backup.)"
}

set_passfile(){
  echo "Current: ${ENV_BACKUP_PASSPHRASE_FILE}"
  read -r -p "Enter passphrase file path: " PF
  [ -z "$PF" ] && return
  sed -i "s|^ENV_BACKUP_PASSPHRASE_FILE=.*|ENV_BACKUP_PASSPHRASE_FILE=${PF}|" "$ENV_FILE"
  if [ ! -f "$PF" ]; then
    read -r -s -p "Create file now (enter passphrase): " P; echo
    printf "%s" "$P" > "$PF"
    chmod 600 "$PF"
  fi
  echo "Set to: $PF"
}

update_stack(){
  (cd "$STACK_DIR" && docker compose pull && docker compose up -d)
}

logs(){
  (cd "$STACK_DIR" && docker compose logs -f --tail=200 paperless)
}

while true; do
  menu
  case "$CH" in
    1) status; read -r -p "Enter to continue…" _ ;;
    2) backup_now; read -r -p "Enter to continue…" _ ;;
    3) list_backups; read -r -p "Enter to continue…" _ ;;
    4) restore; read -r -p "Enter to continue…" _ ;;
    5) toggle_env_mode; read -r -p "Enter to continue…" _ ;;
    6) set_passfile; read -r -p "Enter to continue…" _ ;;
    7) update_stack; read -r -p "Enter to continue…" _ ;;
    8) (cd "$STACK_DIR" && docker compose restart paperless); read -r -p "Enter to continue…" _ ;;
    9) logs ;;
    0) exit 0 ;;
    *) : ;;
  esac
done
