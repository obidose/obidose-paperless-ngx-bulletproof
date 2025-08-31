#!/usr/bin/env bash
set -Eeuo pipefail

# ==========================================================
# Paperless-ngx Bulletproof Installer (one-liner wizard)
# Repo: obidose/obidose-paperless-ngx-bulletproof
# ==========================================================

# Where to fetch raw files (override if you fork)
GITHUB_RAW="${GITHUB_RAW:-https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main}"

# Default instance paths (can be overridden later)
INSTANCE_NAME="${INSTANCE_NAME:-paperless}"
STACK_DIR="${STACK_DIR:-/home/docker/paperless-setup}"
DATA_ROOT="${DATA_ROOT:-/home/docker/paperless}"

# Compose + env file paths (on the server)
COMPOSE_FILE="${STACK_DIR}/docker-compose.yml"
ENV_FILE="${STACK_DIR}/.env"

# Temp dir for fetched modules
TMP_DIR="/tmp/paperless-wiz.$$"
mkdir -p "$TMP_DIR"

fetch() { curl -fsSL "$1" -o "$2"; }
say()   { echo -e "\e[1;34m[•]\e[0m $*"; }
ok()    { echo -e "\e[1;32m[✓]\e[0m $*"; }
warn()  { echo -e "\e[1;33m[!]\e[0m $*"; }
die()   { echo -e "\e[1;31m[x]\e[0m $*"; exit 1; }
need_root(){ [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }

cleanup_tmp() {
  local d="${1:-$TMP_DIR}"
  rm -rf "$d" 2>/dev/null || true
}

main() {
  need_root

  say "Fetching modules…"
  fetch "${GITHUB_RAW}/modules/common.sh" "${TMP_DIR}/common.sh"
  fetch "${GITHUB_RAW}/modules/deps.sh"   "${TMP_DIR}/deps.sh"
  fetch "${GITHUB_RAW}/modules/pcloud.sh" "${TMP_DIR}/pcloud.sh"
  fetch "${GITHUB_RAW}/modules/files.sh"  "${TMP_DIR}/files.sh"

  # shellcheck disable=SC1090
  source "${TMP_DIR}/common.sh"
  source "${TMP_DIR}/deps.sh"
  source "${TMP_DIR}/pcloud.sh"
  source "${TMP_DIR}/files.sh"

  say "Starting Paperless-ngx setup wizard…"

  # 0) Basic sanity
  preflight_ubuntu

  # 1) Dependencies (apt, docker, rclone)
  install_prereqs
  ensure_user
  install_docker
  install_rclone

  # 2) Authenticate to pCloud first so we can early-restore
  setup_pcloud_remote_interactive

  # 3) If backups exist, offer early restore before other prompts
  early_restore_or_continue

  # 4) Fresh install path: load defaults/presets, prompt for missing values
  load_env_defaults_from "${GITHUB_RAW}/env/.env.example"
  pick_and_merge_preset   "${GITHUB_RAW}"      # lets user choose traefik/direct/custom
  prompt_core_values                          # minimal questions with defaults

  # 5) Create directories, write env, fetch compose, install ops & cron & menu
  prepare_dirs
  write_env_file
  fetch_compose_file   "${GITHUB_RAW}"        # picks traefik/direct compose
  install_ops_backup   "${GITHUB_RAW}"        # installs backup_to_pcloud.sh
  install_cron_job
  install_bulletproof_menu "${GITHUB_RAW}"    # installs /usr/local/bin/bulletproof

  # 6) Late restore option (safety)
  maybe_offer_restore

  # 7) Bring up stack and summarize
  bring_up_stack
  final_summary

  ok "Done."
  cleanup_tmp
}

trap 'die "Installer failed at line $LINENO."' ERR
main "$@"
