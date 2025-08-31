#!/usr/bin/env bash
set -Eeuo pipefail

ensure_rclone_conf_dir(){ mkdir -p /root/.config/rclone; }

create_pcloud_remote(){
  local user="$1"; local pass_plain="$2"; local remote_name="$3"; local host="$4"
  local obscured; obscured=$(rclone obscure "$pass_plain")
  rclone config delete "$remote_name" >/dev/null 2>&1 || true
  # Use vendor=other for maximum compatibility
  rclone config create "$remote_name" webdav vendor other \
    url "$host" user "$user" pass "$obscured" >/dev/null
}

test_pcloud_endpoint(){
  local user="$1"; local pass="$2"; local host="$3"
  curl -sS -u "$user:$pass" -X PROPFIND -H "Depth: 0" \
    -o /dev/null -w "%{http_code}" \
    --data '<propfind xmlns="DAV:"><allprop/></propfind>' "$host" || echo "000"
}

setup_pcloud_remote_interactive(){
  log "Connect to pCloud via WebDAV (if 2FA is ON, use an App Password)."
  ensure_rclone_conf_dir
  local pc_user pc_pass host_eu host_global
  pc_user=$(prompt "pCloud login email")
  pc_pass=$(prompt_secret_once "pCloud password (or App Password)")

  host_eu="https://ewebdav.pcloud.com"
  host_global="https://webdav.pcloud.com"

  log "Trying EU WebDAV endpoint…"
  local code; code=$(test_pcloud_endpoint "$pc_user" "$pc_pass" "$host_eu")
  if [ "$code" = "207" ]; then
    create_pcloud_remote "$pc_user" "$pc_pass" "$RCLONE_REMOTE_NAME" "$host_eu"
    ok "Connected to pCloud at $host_eu"
    return 0
  fi

  warn "EU endpoint failed (HTTP $code). Trying Global…"
  code=$(test_pcloud_endpoint "$pc_user" "$pc_pass" "$host_global")
  if [ "$code" = "207" ]; then
    create_pcloud_remote "$pc_user" "$pc_pass" "$RCLONE_REMOTE_NAME" "$host_global"
    ok "Connected to pCloud at $host_global"
    return 0
  fi

  warn "Both endpoints failed; running rclone -vv for hints:"
  rclone -vv lsd "${RCLONE_REMOTE_NAME}:" || true
  err "Could not authenticate to pCloud via WebDAV. Check email/password and 2FA (use an App Password if 2FA is ON)."
}
