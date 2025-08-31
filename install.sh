#!/usr/bin/env bash
set -Eeuo pipefail

# ===== Pretty output =====
COLOR_BLUE="\e[1;34m"; COLOR_GREEN="\e[1;32m"; COLOR_YELLOW="\e[1;33m"; COLOR_RED="\e[1;31m"; COLOR_OFF="\e[0m"
say(){ echo -e "${COLOR_BLUE}[•]${COLOR_OFF} $*"; }
ok(){  echo -e "${COLOR_GREEN}[✓]${COLOR_OFF} $*"; }
warn(){echo -e "${COLOR_YELLOW}[!]${COLOR_OFF} $*"; }
die(){ echo -e "${COLOR_RED}[x]${COLOR_OFF} $*"; exit 1; }
log(){ say "$@"; }

trap 'code=$?; echo -e "${COLOR_RED}[x]${COLOR_OFF} Installer failed at ${BASH_SOURCE[0]}:${LINENO} (exit ${code})"; exit $code' ERR

need_root(){ [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }
need_root

# ===== Repo base =====
GITHUB_RAW="${GITHUB_RAW:-https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main}"

# ===== Temp workspace =====
TMP_DIR="$(mktemp -d /tmp/paperless-wiz.XXXXXX)"
cleanup(){ rm -rf "$TMP_DIR"; }
trap cleanup EXIT

fetch(){ curl -fsSL "$1" -o "$2"; }

say "Fetching modules…"
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

say "Starting Paperless-ngx setup wizard…"

# 0) Sanity + dependencies
preflight_ubuntu
install_prereqs
ensure_user
install_docker
install_rclone

# 1) Authenticate to pCloud first so we can restore immediately if backups exist
setup_pcloud_remote_interactive

# 2) Offer early restore
early_restore_or_continue   # exits on success; otherwise continues fresh path

# 3) Let user optionally load a preset .env (repo/local/URL), then prompt for missing values
pick_and_merge_preset "${GITHUB_RAW}"
prompt_core_values
compute_paths

# 4) Prepare filesystem + write env/compose + backup tooling
prepare_dirs
write_env_file
write_compose_file
write_backup_script
install_cron

# 5) Bring up the stack
bring_up

ok "Done. Access Paperless at: ${PAPERLESS_URL:-http://localhost:8000}"
