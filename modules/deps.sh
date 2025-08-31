#!/usr/bin/env bash
set -Eeuo pipefail

install_prereqs(){
  log "Installing prerequisites..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg lsb-release unzip tar cron \
                     software-properties-common
  if [ -f /etc/fuse.conf ] && grep -q "^#\?user_allow_other" /etc/fuse.conf; then
    sed -i 's/^#\?user_allow_other/user_allow_other/' /etc/fuse.conf || true
  fi
}

ensure_user(){
  if ! id -u "$DOCKER_USER" >/dev/null 2>&1; then
    log "Creating user $DOCKER_USER (UID=$DOCKER_UID, GID=$DOCKER_GID)"
    groupadd -g "$DOCKER_GID" "$DOCKER_USER" 2>/dev/null || true
    useradd -m -u "$DOCKER_UID" -g "$DOCKER_GID" -s /bin/bash "$DOCKER_USER"
  fi
}

install_docker(){
  if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker Engine + Compose plugin..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
  else
    log "Docker already installed."
  fi
  usermod -aG docker "$DOCKER_USER" || true
}

install_rclone(){
  if ! command -v rclone >/dev/null 2>&1; then
    log "Installing rclone..."
    curl -fsSL https://rclone.org/install.sh | bash
  else
    log "rclone already installed."
  fi
}
