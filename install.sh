cat > install.sh <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

say()  { echo "[*] $*"; }
ok()   { echo "[OK] $*"; }
warn() { echo "[!] $*"; }
die()  { echo "[x] $*"; exit 1; }
log()  { say "$@"; }

trap 'code=$?; echo "[x] Installer failed at ${BASH_SOURCE[0]}:${LINENO} (exit ${code})"; exit $code' ERR
[ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."

GITHUB_RAW="${GITHUB_RAW:-https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main}"
TMP_DIR="$(mktemp -d /tmp/paperless-wiz.XXXXXX)"
cleanup(){ rm -rf "$TMP_DIR"; }
trap cleanup EXIT

fetch(){ curl -fsSL "$1" -o "$2"; }

say "Fetching modules..."
fetch "${GITHUB_RAW}/modules/common.sh"  "${TMP_DIR}/common.sh"
fetch "${GITHUB_RAW}/modules/deps.sh"    "${TMP_DIR}/deps.sh"
fetch "${GITHUB_RAW}/modules/pcloud.sh"  "${TMP_DIR}/pcloud.sh"
fetch "${GITHUB_RAW}/modules/files.sh"   "${TMP_DIR}/files.sh"

command -v dos2unix >/dev/null 2>&1 && dos2unix "${TMP_DIR}/"*.sh >/dev/null 2>&1 || true

# shellcheck disable=SC1090
source "${TMP_DIR}/common.sh"
source "${TMP_DIR}/deps.sh"
source "${TMP_DIR}/pcloud.sh"
source "${TMP_DIR}/files.sh"

say "Starting Paperless-ngx setup wizard..."
preflight_ubuntu
install_prereqs
ensure_user
install_docker
install_rclone
setup_pcloud_remote_interactive
early_restore_or_continue
pick_and_merge_preset "${GITHUB_RAW}"
prompt_core_values
ensure_dir_tree
write_env_file
write_compose_file
docker_compose_up
install_backup_cron
ok "All done. Visit your Paperless-ngx URL shown above."
EOF

git add install.sh
git commit -m "Fix install.sh: ASCII-only, no mojibake symbols"
