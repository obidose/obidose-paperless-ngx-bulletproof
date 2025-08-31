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

# Temp dir for fetched modules
TMP_DIR="/tmp/paperless-wiz.$$"
mkdir -p "$TMP_DIR"

say(){  echo -e "\e[1;34m[•]\e[0m $*"; }
ok(){   echo -e "\e[1;32m[✓]\e[0m $*"; }
warn(){ echo -e "\e[1;33m[!]\e[0m $*"; }
die(){  echo -e "\e[1;31m[x]\e[0m $*"; exit 1; }
need_root(){ [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }

fetch_or_die() {
  local url="$1" dest="$2"
  curl -fsSL "$url" -o "$dest" || die "Fetch failed: $url"
}

cleanup() { rm -rf "$TMP_DIR" 2>/dev/null || true; }

trap 'die "Installer failed at ${BASH_SOURCE[0]}:${LINENO} (exit $?)"' ERR
need_root

say "Fetching modules…"
for f in common deps pcloud files; do
  fetch_or_die "${GITHUB_RAW}/modules/${f}.sh" "${TMP_DIR}/${f}.sh"
done

# Normalize CRLF if present (no-op if dos2unix not installed)
if command -v dos2unix >/dev/null 2>&1; then
  dos2unix "${TMP_DIR}"/*.sh >/dev/null 2>&1 || true
fi

# Syntax check each module; show context on error
for m in "${TMP_DIR}"/*.sh; do
  if ! bash -n "$m" 2>/dev/null; then
    warn "Syntax error in $(basename "$m"); context:"
    nl -ba "$m" | sed -n '1,200p'
    die "Aborting due to syntax error in $(basename "$m")."
  fi
done

# Export a few vars some modules may expect
export GITHUB_RAW INSTANCE_NAME STACK_DIR DATA_ROOT COMPOSE_FILE ENV_FILE

# Some modules may reference variables not yet defined; relax nounset while sourcing
set +u
# shellcheck disable=SC1090
source "${TMP_DIR}/common.sh"
source "${TMP_DIR}/deps.sh"
source "${TMP_DIR}/pcloud.sh"
source "${TMP_DIR}/files.sh"
set -u

say "Starting Paperless-ngx setup wizard…"

# --- main flow ---
preflight_ubuntu                 # distro sanity
install_prereqs                  # apt update/upgrade + basics
ensure_user                      # creates 'docker' user if missing
install_docker                   # Docker Engine + Compose plugin
install_rclone                   # rclone for pCloud

setup_pcloud_remote_interactive  # login to pCloud first (EU→Global), creates remote
early_restore_or_continue        # if snapshots exist, offer early restore

load_env_defaults_from "${GITHUB_RAW}/env/.env.example"
pick_and_merge_preset   "${GITHUB_RAW}"       # choose traefik/direct/custom (or URL/local)
prompt_core_values                              # ask only for missing values

prepare_dirs                                   # create stack/data directories
write_env_file                                  # write .env based on answers/preset
fetch_compose_file   "${GITHUB_RAW}"           # fetch traefik/direct compose
install_ops_backup   "${GITHUB_RAW}"           # install ops/backup_to_pcloud.sh
install_cron_job                                    # add daily cron if not present
install_bulletproof_menu "${GITHUB_RAW}"       # install /usr/local/bin/bulletproof

maybe_offer_restore                             # optional late restore
bring_up_stack                                  # docker compose up -d
final_summary                                   # print URLs, next steps

ok "Done."
cleanup
