#!/usr/bin/env bash
set -Eeuo pipefail

# ===== Pretty output (ASCII only) =====
say(){  echo -e "[*] $*"; }
ok(){   echo -e "[OK] $*"; }
warn(){ echo -e "[!] $*"; }
die(){  echo -e "[x] $*"; exit 1; }

trap 'code=$?; echo -e "[x] Installer failed at ${BASH_SOURCE[0]:-$0}:${LINENO} (exit ${code})"; exit $code' ERR

need_root(){ [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }
need_root

# ===== Repo base =====
GITHUB_RAW="${GITHUB_RAW:-https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main}"

# ===== Temp workspace =====
TMP_DIR="$(mktemp -d /tmp/paperless-wiz.XXXXXX)"
cleanup(){ rm -rf "$TMP_DIR"; }
trap cleanup EXIT

fetch(){ curl -fsSL "$1" -o "$2"; }

say "Fetching modules..."
fetch "${GITHUB_RAW}/modules/common.sh"  "${TMP_DIR}/common.sh"
fetch "${GITHUB_RAW}/modules/deps.sh"    "${TMP_DIR}/deps.sh"
fetch "${GITHUB_RAW}/modules/pcloud.sh"  "${TMP_DIR}/pcloud.sh"
fetch "${GITHUB_RAW}/modules/files.sh"   "${TMP_DIR}/files.sh"

# Normalize line endings just in case
command -v dos2unix >/dev/null 2>&1 && dos2unix "${TMP_DIR}/"*.sh >/dev/null 2>&1 || true

# ===== Source modules =====
# shellcheck disable=SC1090
source "${TMP_DIR}/common.sh"
source "${TMP_DIR}/deps.sh"
source "${TMP_DIR}/pcloud.sh"
source "${TMP_DIR}/files.sh"

say "Starting Paperless-ngx setup wizard..."

# ===== System deps =====
preflight_ubuntu
install_prereqs
ensure_user
install_docker
install_rclone

# ===== pCloud setup (OAuth preferred, auto region) =====
pcloud_auto_region_or_setup
pcloud_early_restore_or_continue   # will exit 0 on successful restore

# ===== Optional presets, then prompts =====
pick_and_merge_preset "${GITHUB_RAW}"
prompt_core_values
ensure_dir_tree

# ===== Write files & deploy =====
write_env_file
write_compose_file

# Bring up the stack even if helper function is absent
if declare -F bring_up_stack >/dev/null 2>&1; then
  bring_up_stack
else
  ( cd "$STACK_DIR" && docker compose pull && docker compose up -d )
fi

# ===== Backups (if helper exists) =====
if declare -F create_backup_job >/dev/null 2>&1; then
  create_backup_job
fi

ok "Done. Access your instance once containers are healthy."
