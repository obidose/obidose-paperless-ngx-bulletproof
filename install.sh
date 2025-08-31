#!/usr/bin/env bash
set -Eeuo pipefail

# ===== Pretty output (ASCII only) =====
COLOR_BLUE="\e[1;34m"; COLOR_GREEN="\e[1;32m"; COLOR_YELLOW="\e[1;33m"; COLOR_RED="\e[1;31m"; COLOR_OFF="\e[0m"
say(){  echo -e "${COLOR_BLUE}[*]${COLOR_OFF} $*"; }
ok(){   echo -e "${COLOR_GREEN}[ok]${COLOR_OFF} $*"; }
warn(){ echo -e "${COLOR_YELLOW}[!]${COLOR_OFF} $*"; }
die(){  echo -e "${COLOR_RED}[x]${COLOR_OFF} $*"; exit 1; }
log(){  say "$@"; }

# Robust trap (works with set -u even if BASH_SOURCE is unset)
trap 'code=$?; file=${BASH_SOURCE[0]:-$0}; line=${LINENO:-?}; echo -e "${COLOR_RED}[x]${COLOR_OFF} Installer failed at ${file}:${line} (exit ${code})"; exit $code' ERR

need_root(){ [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }
need_root

# ===== Repo base =====
GITHUB_RAW="${GITHUB_RAW:-https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main}"

# ===== Temp workspace =====
TMP_DIR="$(mktemp -d /tmp/paperless-wiz.XXXXXX)"
cleanup(){ rm -rf "$TMP_DIR"; }
trap cleanup EXIT

fetch(){ curl -fsSL "$1" -o "$2"; }

say "[*] Fetching modules..."
fetch "${GITHUB_RAW}/modules/common.sh"  "${TMP_DIR}/common.sh"
fetch "${GITHUB_RAW}/modules/deps.sh"    "${TMP_DIR}/deps.sh"
fetch "${GITHUB_RAW}/modules/pcloud.sh"  "${TMP_DIR}/pcloud.sh"
fetch "${GITHUB_RAW}/modules/files.sh"   "${TMP_DIR}/files.sh"
fetch "${GITHUB_RAW}/modules/backup.sh"  "${TMP_DIR}/backup.sh"

# Normalize line endings if available (no-op if dos2unix missing)
command -v dos2unix >/dev/null 2>&1 && dos2unix "${TMP_DIR}/"*.sh >/dev/null 2>&1 || true

# ===== Source modules =====
# shellcheck disable=SC1090
source "${TMP_DIR}/common.sh"
source "${TMP_DIR}/deps.sh"
source "${TMP_DIR}/pcloud.sh"
source "${TMP_DIR}/files.sh"
source "${TMP_DIR}/backup.sh"

# Helper: check function existence
fn_exists(){ declare -F "$1" >/dev/null 2>&1; }

say "[*] Starting Paperless-ngx setup wizard..."

# 0) System deps
preflight_ubuntu
install_prereqs
ensure_user
install_docker
install_rclone

# 1) pCloud connect first so we can auto-restore if backups exist
if fn_exists ensure_pcloud_remote_or_menu; then
  ensure_pcloud_remote_or_menu
elif fn_exists setup_pcloud_remote_interactive && fn_exists early_restore_or_continue; then
  # Back-compat with older pcloud.sh versions
  setup_pcloud_remote_interactive
  early_restore_or_continue
else
  die "pCloud module not loaded correctly (missing ensure_pcloud_remote_or_menu)."
fi

# If an early restore succeeded, that function exits 0 already; otherwise we continue.

# 2) Optional presets, then core prompts
pick_and_merge_preset "${GITHUB_RAW}"
prompt_core_values

# 3) Ensure directories, write files, launch stack
ensure_dir_tree
write_env_file
write_compose_file
bring_up_stack

# 4) Install/refresh cron job for backups (module handles idempotency)
install_cron_backup

# 5) Install Bulletproof CLI (menu + one-shot commands).
CLI_URL="${GITHUB_RAW}/tools/bulletproof.sh"
CLI_DST="/usr/local/bin/bulletproof"
if curl -fsSL "$CLI_URL" -o "$CLI_DST"; then
  chmod +x "$CLI_DST" || true
  ok "Bulletproof CLI installed at ${CLI_DST}"
else
  warn "Bulletproof CLI not found at ${CLI_URL} (skipping). You can add it later with:
  curl -fsSL ${CLI_URL} -o ${CLI_DST} && chmod +x ${CLI_DST}"
fi

# --- Install helper scripts into stack dir ---
say "Installing backup helper into ${STACK_DIR}/backup.sh"
install -d "${STACK_DIR}"
[ -f "${STACK_DIR}/backup.sh" ] && cp -a "${STACK_DIR}/backup.sh" "${STACK_DIR}/backup.sh.bak.$(date +%s)" || true
install -m 0755 "${TMP_DIR}/backup.sh" "${STACK_DIR}/backup.sh"
curl -fsSL "https://raw.githubusercontent.com/${GITHUB_REPO}/main/modules/restore.sh" \
  -o "${STACK_DIR}/restore.sh" && chmod +x "${STACK_DIR}/restore.sh"

echo
ok "Setup complete."
echo "Next steps:"
echo "  - If using Traefik + HTTPS, point DNS to this host and wait for certs."
echo "  - CLI: run 'bulletproof' for the backup/restore menu (if installed)."
echo "  - Or: 'bulletproof backup', 'bulletproof list', 'bulletproof restore'."
