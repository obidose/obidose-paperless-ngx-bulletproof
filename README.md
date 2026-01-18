# Paperless-NGX Bulletproof

A production-ready deployment system for Paperless-NGX with multi-instance support, automated backups, and disaster recovery.

Designed for managing multiple Paperless-NGX instances (personal, family, clients) from a single command.

---

## Quick Start

### Installation

**Stable release:**
```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py | sudo python3
```

**Development version:**
```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/paperless.py | sudo python3 - --branch dev
```

The installer handles Docker, rclone, and pCloud configuration automatically.

### After Installation

```bash
paperless
```

---

## Features

### Multi-Instance Management
- Run isolated instances with separate databases, media, and configuration
- Switch between instances from a unified interface
- Independent backup and restore per instance

### Backup System
- Full and incremental snapshots uploaded to pCloud
- Docker image version tracking for reproducible restores
- Point-in-time recovery from any snapshot
- System-level backup for disaster recovery

### Safe Updates
- Automatic backup before upgrade
- Health checks after update
- Easy rollback to previous snapshot

### Health Monitoring
- 13-point health check covering system and stack
- Container, database, Redis, Django, and HTTP validation
- Container name verification to catch misconfigurations

---

## Requirements

- Ubuntu 22.04 or 24.04 LTS
- 4GB RAM minimum (8GB+ for multiple instances)
- 20GB+ disk space
- Root/sudo access
- pCloud account for backups (free 10GB available)

---

## First Run

### Fresh Machine

1. Installs Docker, rclone, and dependencies
2. Connects to pCloud (paste OAuth token when prompted)
3. Launches the manager

From the manager:
- **Add Instance** to create new or restore from backup
- Each instance gets its own domain, settings, and users

### Example: Family Setup

```
paperless
→ Instances
  → Add new instance
    → Name: paperless-personal
    → Domain: docs.mydomain.com
  → Add new instance  
    → Name: paperless-dad
    → Domain: docs-dad.mydomain.com
```

Each instance runs independently with its own database and backups.

---

## Management Interface

```
╔══════════════════════════════════════════════════════════╗
║        Paperless-NGX Bulletproof Manager                ║
╚══════════════════════════════════════════════════════════╝

Current Instance: paperless [Running]

  1) Setup new instance
  2) Select/switch instance
  3) Backup management
  4) Restore from backup
  5) System health check
  6) Instance management
  7) Container operations
  q) Quit
```

---

## pCloud Setup

During installation, configure pCloud using OAuth:

1. On any machine with a browser:
   ```bash
   rclone authorize "pcloud"
   ```
2. Copy the JSON token
3. Paste into the installer

The installer auto-detects EU vs Global regions.

---

## Backup & Restore

### Automated Backups

Configured during installation:
- Full backups: Weekly (default Sunday 3:30 AM)
- Incremental: Daily (default midnight)
- Archive: Optional monthly

Backups go to: `pcloud:backups/paperless/{instance_name}/`

Each snapshot includes:
- PostgreSQL database dump
- Incremental tarballs (media, data, export)
- Environment and compose configuration
- Docker image versions
- Manifest with metadata and checksums

### Manual Backup

```bash
paperless
→ Backup management
→ Choose full/incremental/archive
```

### Restore

```bash
paperless
→ Restore from backup
→ Select snapshot
→ Confirm
```

Restore process:
1. Stops containers
2. Downloads snapshot chain from pCloud
3. Applies incrementals in order
4. Restores database
5. Restarts containers
6. Runs health check

---

## Configuration

### Presets

Choose during installation:

**Traefik** - Reverse proxy with automatic HTTPS via Let's Encrypt. Requires domain DNS pointing to server.

**Direct** - Direct HTTP access on localhost (default port 8000). No HTTPS.

### Environment Variables

All settings in `/home/docker/paperless-setup/.env`:

```bash
INSTANCE_NAME=paperless
DATA_ROOT=/home/docker/paperless
STACK_DIR=/home/docker/paperless-setup

PAPERLESS_ADMIN_USER=admin
PAPERLESS_ADMIN_PASSWORD=...
PAPERLESS_URL=https://your-domain.com

POSTGRES_PASSWORD=...

ENABLE_TRAEFIK=yes
DOMAIN=paperless.example.com
LETSENCRYPT_EMAIL=admin@example.com
HTTP_PORT=8000

RCLONE_REMOTE_NAME=pcloud
RCLONE_REMOTE_PATH=backups/paperless/paperless
RETENTION_DAYS=30

CRON_FULL_TIME=30 3 * * 0
CRON_INCR_TIME=0 0 * * *
```

### Multiple Instances

Instance metadata stored in `/etc/paperless-bulletproof/instances.json`

Add existing instance:
```bash
paperless
→ Instance management
→ Add existing instance
```

---

## Project Structure

```
paperless.py              # Entry point
lib/
├── manager.py           # Interactive TUI
├── config.py            # Configuration constants
├── installer/           # Installation modules
│   ├── common.py       # Configuration and helpers
│   ├── deps.py         # System dependencies
│   ├── files.py        # File generation
│   ├── pcloud.py       # Backup configuration
│   ├── cloudflared.py  # Cloudflare tunnel setup
│   ├── tailscale.py    # Tailscale setup
│   └── traefik.py      # Traefik configuration
├── modules/
│   ├── backup.py       # Snapshot creation
│   └── restore.py      # Snapshot restoration
└── utils/
    ├── common.py       # Shared utilities
    └── selftest.py     # Health checks
compose/
├── docker-compose-direct.yml
└── docker-compose-traefik.yml
presets/
├── direct.env
└── traefik.env
```

---

## Development

```bash
git clone https://github.com/obidose/obidose-paperless-ngx-bulletproof.git
cd obidose-paperless-ngx-bulletproof
sudo python3 paperless.py --branch dev
```

Branches:
- **main**: Stable
- **dev**: Active development

---

## Troubleshooting

### pCloud OAuth Fails
- Paste the complete JSON token
- Try the same rclone version
- Use WebDAV as fallback

### HTTPS Not Working
- Check DNS points to server
- Verify ports 80/443 are open
- Check Traefik logs via manager

### No Snapshots Found
- Run a manual backup first
- Verify rclone: `rclone lsd pcloud:`
- Check remote path in .env

### Containers Won't Start
- Check Docker: `docker ps -a`
- View logs via manager
- Run health check

### Backup Fails
- Check disk space: `df -h`
- Verify pCloud: `rclone about pcloud:`
- Review backup.log in stack directory

---

## Uninstall

Remove stack (keeps backups):

```bash
cd /home/docker/paperless-setup
docker compose down -v

# Optional: remove files
sudo rm -rf /home/docker/paperless
sudo rm -rf /home/docker/paperless-setup
sudo rm /usr/local/bin/paperless
sudo rm -rf /usr/local/lib/paperless-bulletproof
sudo rm -rf /etc/paperless-bulletproof
```

pCloud backups remain intact.

---

## Architecture

**Entry Point** (`paperless.py`)
- Detects fresh vs existing installation
- Bootstraps from GitHub if needed
- Launches installer or manager

**Installer** (`lib/installer/`)
- Installs Docker, rclone, dependencies
- Configures pCloud
- Generates docker-compose.yml and .env
- Sets up cron jobs

**Manager** (`lib/manager.py`)
- Interactive TUI
- Multi-instance management
- Backup/restore operations
- Health monitoring
- Container control

**Modules** (`lib/modules/`)
- `backup.py`: Creates and uploads snapshots
- `restore.py`: Downloads and applies snapshots

**Utilities** (`lib/utils/`)
- `common.py`: Shared output and environment functions
- `selftest.py`: Stack health validation

---

## License

MIT License

---

## Support

- [GitHub Issues](https://github.com/obidose/obidose-paperless-ngx-bulletproof/issues)
- In-app help via `paperless` → "About & Help"
