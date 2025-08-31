#!/usr/bin/env bash
set -Eeuo pipefail

create_pcloud_remote(){
  local user="$1"; local pass_plain="$2"; local remote_name="$3"; local host="$4"
  local obscured; obscured=$(rclone obscure "$pass_plain")
  rclone config delete "$remote_name" >/dev/null 2>&1 || true
  # vendor=other to avoid "unknown vendor" warning
  rclone config create "$remote_name" webdav vendor other url "$host" user "$user" pass "$obscured" >/dev/null
}

setup_pcloud(){
  log "Connect to pCloud via WebDAV (if 2FA is ON, use an App Password)."
  local pc_user pc_pass host_eu host_global code ok_host
  host_eu="https://ewebdav.pcloud.com"
  host_global="https://webdav.pcloud.com"

  while true; do
    read -r -p "pCloud login email: " pc_user
    pc_user=$(printf '%s' "$pc_user" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$pc_user" ] || { warn "Email is required."; continue; }

    read -r -s -p "pCloud password (or App Password): " pc_pass; echo
    pc_pass=$(printf '%s' "$pc_pass" | tr -d '\r' | sed 's/[[:space:]]*$//')
    [ -n "$pc_pass" ] || { warn "Password cannot be empty."; continue; }

    code=$(curl -sS -u "$pc_user:$pc_pass" -X PROPFIND -H "Depth: 0" \
             -o /dev/null -w "%{http_code}" \
             --data '<propfind xmlns="DAV:"><allprop/></propfind>' "$host_eu/" || true)
    if [ "$code" = "207" ]; then
      ok_host="$host_eu"
    else
      warn "EU endpoint returned HTTP $code. Trying Global…"
      code=$(curl -sS -u "$pc_user:$pc_pass" -X PROPFIND -H "Depth: 0" \
               -o /dev/null -w "%{http_code}" \
               --data '<propfind xmlns="DAV:"><allprop/></propfind>' "$host_global/" || true)
      [ "$code" = "207" ] && ok_host="$host_global" || ok_host=""
    fi

    if [ -n "$ok_host" ]; then
      log "Auth OK on $ok_host — creating rclone remote '${RCLONE_REMOTE_NAME}'"
      create_pcloud_remote "$pc_user" "$pc_pass" "$RCLONE_REMOTE_NAME" "$ok_host"
      if rclone lsd "${RCLONE_REMOTE_NAME}:" >/dev/null 2>&1; then
        log "rclone remote '${RCLONE_REMOTE_NAME}' ready."
        return 0
      fi
      warn "rclone list failed even though curl auth succeeded. Retrying…"
    else
      warn "Both endpoints rejected credentials (HTTP $code). If 2FA is ON, use an App Password."
    fi

    read -r -p "Re-enter pCloud credentials? [Y/n]: " ans; ans=${ans:-Y}
    [[ "$ans" =~ ^[Yy]$ ]] || err "Could not authenticate to pCloud WebDAV."
  done
}
