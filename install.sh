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

# Basic UI helpers (modules will reuse if not already defined)
say(){  echo -e "\e[1;34m[•]\e[0m $*"; }
ok(){   echo -e "\e[1;32m[✓]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }
die(){  echo -e "\e[1;31m[x]\e[0m $*"; exit 1; }

# Error reporting: try to surface the module + line that failed
trap 'code=$?; line=${BASH_LINENO[0]:-0}; src=${BASH_SOURCE[1]:-$0}; die "Installer failed at ${src}:${line} (exit ${code})"' ERR

need_root(){ [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }
need_root

say "Fetching modules…"
fetch "${GITHUB_RAW}/modules/common.sh"  "${TMP_DIR}/common.sh"
fetch "${GITHUB_RAW}/modules/deps.sh"    "${TMP_DIR}/deps.sh"
fetch "${GITHUB_RAW}/modules/pcloud.sh"  "${TMP_DIR}/pcloud.sh"
fetch "${GITHUB_RAW}/modules/files.sh"   "${TMP_DIR}/files.sh"

# Ensure modules exist & are non-empty
for m in common deps pcloud files; do
  [ -s "${TMP_DIR}/${m}.sh" ] || die "Failed to fetch modules/${m}.sh"
done

# shellcheck disable=SC1090
source "${TMP_DIR}/common.sh"
source "${TMP_DIR}/deps.sh"
source "${TMP_DIR}/pcloud.sh"
source "${TMP_DIR}/files.sh"

# >>> Important fix: generate any missing secrets AFTER sourcing modules <<<
# (avoids subshells during source-time under 'set -Eeuo pipefail')
ensure_runtime_defaults

say "Starting Paperless-ngx setup wizard…"

# 0) Basic OS sanity + deps
preflight_ubuntu
install_prereqs                # apt update/upgrade + basics
ensure_user                    # creates 'docker' user if missing
install_docker                 # installs docker engine + compose plugin
install_rclone                 # rclone for pCloud backups

# 1) Authenticate to pCloud first, so we can branch to restore immediately
setup_pcloud_remote_interactive

# 2) If backups exist, offer an early restore before any other prompts
#    (Will bring up stack after restore; otherwise returns to continue fresh install)
early_restore_or_continue

# 3) Optional presets to pre-fill defaults (from repo/local/URL)
pick_and_merge_preset "$GITHUB_RAW"

# 4) Ask for core values (accept Enter for defaults)
prompt_core_values

# 5) Recompute derived paths & ensure dir tree
compute_paths
ensure_dir_tree

# 6) Write config & helper files
write_env_file
write_compose_file
write_backup_script
install_backup_cron

# 7) Start stack (if not already running from a restore)
bring_up_stack

# 8) Final status
show_status

ok "All done! Visit your Paperless-ngx at the URL shown above."
