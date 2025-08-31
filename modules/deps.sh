#!/usr/bin/env bash
set -Eeuo pipefail

install_prereqs(){
  log "Installing prerequisites…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get upgrade -y
  apt-get install -y ca-certificates curl gnupg lsb-release unzip tar cron \
                     software-properties-common dos2unix jq
  if grep -q "^#\?user_allow_other" /etc/fuse.conf 2>/dev/null; then
    sed -i 's/^#\?user_allow_other/user_allow_other/' /etc/fuse.conf || true
  fi
}

ensure_user(){
  if ! id -u docker >/dev/null 2>&1; then
    log "Creating 'docker' user (uid/gid 1001)…"
    groupadd -g 1001 docker 2>/dev/null || true
    useradd -m -u 1001 -g 1001 -s /bin/bash docker
  fi
}

install_docker(){
  if ! command -v docker >/dev/null 2%; then
    log "Installing Docker Engine + Compose plugin…"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
  else
    log "Docker already installed."
  fi
  usermod -aG docker docker || true
}

install_rclone(){
  if ! command -v rclone >/dev/null 2>&1; then
    log "Installing rclone…"
    curl -fsSL https://rclone.org/install.sh | bash
  else
    log "rclone already installed."
  fi
}
