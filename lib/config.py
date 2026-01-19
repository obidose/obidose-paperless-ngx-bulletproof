"""
Paperless-NGX Bulletproof - Configuration Constants

Centralized configuration for paths, Docker images, and system defaults.
Edit this file to customize installation paths or Docker image versions.
"""

# ─── Installation Paths ───────────────────────────────────────────────────────

# Where the application is installed
INSTALL_DIR = "/usr/local/lib/paperless-bulletproof"

# Where the CLI symlink is created
CLI_SYMLINK = "/usr/local/bin/paperless"

# Where instance metadata is stored
CONFIG_DIR = "/etc/paperless-bulletproof"

# Default paths for instances (can be overridden per-instance)
DEFAULT_STACK_DIR = "/home/docker/paperless-setup"
DEFAULT_DATA_ROOT = "/home/docker/paperless"


# ─── Docker Images ────────────────────────────────────────────────────────────

# Main application
PAPERLESS_IMAGE = "ghcr.io/paperless-ngx/paperless-ngx:latest"

# Dependencies
REDIS_IMAGE = "redis:7-alpine"
POSTGRES_IMAGE_TEMPLATE = "postgres:{version}-alpine"  # {version} replaced by cfg.postgres_version
GOTENBERG_IMAGE = "gotenberg/gotenberg:8"
TIKA_IMAGE = "apache/tika:latest"


# ─── Service Endpoints ────────────────────────────────────────────────────────

REDIS_URL = "redis://redis:6379"
TIKA_ENDPOINT = "http://tika:9998"
GOTENBERG_ENDPOINT = "http://gotenberg:3000"


# ─── GitHub Repository ────────────────────────────────────────────────────────

GITHUB_OWNER = "obidose"
GITHUB_REPO = "obidose-paperless-ngx-bulletproof"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_ARCHIVE_URL = f"https://codeload.github.com/{GITHUB_OWNER}/{GITHUB_REPO}/tar.gz/refs/heads"


# ─── Default Values ───────────────────────────────────────────────────────────

DEFAULT_POSTGRES_VERSION = "16"
DEFAULT_HTTP_PORT = "8000"

# Default cron schedules
# Incremental every 6 hours, Full weekly on Sunday, Archive monthly on 1st
DEFAULT_CRON_INCR = "0 */6 * * *"      # Every 6 hours
DEFAULT_CRON_FULL = "30 3 * * 0"       # Sunday 3:30 AM
DEFAULT_CRON_ARCHIVE = "0 4 1 * *"     # 1st of month 4:00 AM

# Backup retention policy (smart tiered retention)
# All backups (full/incremental/archive) kept for full restore flexibility
DEFAULT_RETENTION_DAYS = "30"          # Keep ALL snapshots for 30 days
# Monthly archives kept longer for disaster recovery
DEFAULT_RETENTION_MONTHLY_DAYS = "180" # Keep monthly archives for 6 months


# ─── Backup Configuration ─────────────────────────────────────────────────────

DEFAULT_RCLONE_REMOTE = "pcloud"
BACKUP_PATH_TEMPLATE = "backups/paperless/{instance}"
SYSTEM_BACKUP_PATH = "backups/paperless-system"


# ─── Application Metadata ─────────────────────────────────────────────────────

APP_NAME = "Paperless-NGX Bulletproof"
APP_VERSION = "2.0.0"
APP_DESCRIPTION = "Production-ready multi-instance Paperless-NGX deployment system"
