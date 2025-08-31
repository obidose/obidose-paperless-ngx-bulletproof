#!/usr/bin/env bash
set -Eeuo pipefail

# ==========================================================
# Paperless-ngx Bulletproof Installer (one-liner wizard)
# Repo: obidose/obidose-paperless-ngx-bulletproof
# ==========================================================

# Where to fetch raw files (override if you fork)
GITHUB_RAW="${GITHUB_RAW:-https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main}"

# Default instance settings (can be overridden by presets/answers)
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"

# Compose + env file paths (on the server)
COMPOSE_FILE="${STACK_DIR}/docker-compose.yml"
ENV_FILE="${STACK_DIR}/.env"

# Modules temp dir
TMP_DIR="/tmp/paperless-wiz.$$"
mkdir -p "$TMP_DIR"

fetch() { curl -fsSL "$1" -o "$2"; }

say(){ echo -e "\e[1;34m[•]\e[0m $*"; }
ok(){ echo -e "\e[1;32m[✓]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }
die(){ echo -e "\e[1;31m[x]\e[0m $*"; exit 1; }

need_root(){ [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }
need_root

say "Fetching modules…"
fetch "${GITHUB_RAW}/modules/common.sh"  "${TMP_DIR}/common.sh"
fetch "${GITHUB_RAW}/modules/deps.sh"    "${TMP_DIR}/deps.sh"
fetch "${GITHUB_RAW}/modules/pcloud.sh"  "${TMP_DIR}/pcloud.sh"
fetch "${GITHUB_RAW}/modules/files.sh"   "${TMP_DIR}/files.sh"

# shellcheck disable=SC1090
source "${TMP_DIR}/common.sh"
source "${TMP_DIR}/deps.sh"
source "${TMP_DIR}/pcloud.sh"
source "${TMP_DIR}/files.sh"

say "Starting Paperless-ngx setup wizard…"

preflight_ubuntu
install_prereqs                # apt update/upgrade + basics
ensure_user                    # creates 'docker' user if missing
install_docker                 # installs docker engine + compose plugin
install_rclone                 # rclone for pCloud backups

# 1) Authenticate to pCloud first, so we can branch to restore immediately
setup_pcloud_remote_interactive

# 2) If backups exist, offer an early restore before any other prompts
early_restore_or_continue

# 3) Fresh install path continues here:
#    Load defaults/presets, prompt only for missing settings,
#    then write files and bring up the stack.
load_env_defaults_from "${GITHUB_RAW}/env/.env.example"
pick_and_merge_preset "${GITHUB_RAW}"
prompt_core_values

prepare_dirs
write_env_file
fetch_compose_file "${GITHUB_RAW}"
install_ops_backup "${GITHUB_RAW}"
install_cron_job
install_bulletproof_menu "${GITHUB_RAW}"

# (Late restore option as safety net)
maybe_offer_restore

bring_up_stack
final_summary
cleanup_tmp "${TMP_DIR}"
ok "Done."
