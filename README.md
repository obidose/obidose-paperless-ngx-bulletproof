# Paperless-NGX Bulletproof

A deployment and instance management system for [Paperless-NGX](https://github.com/paperless-ngx/paperless-ngx) with automated backups, disaster recovery, and multi-instance support.

Deploy and manage multiple isolated Paperless-NGX instances from a single unified interface with automated backups to pCloud and complete disaster recovery capability.

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
- Each instance gets its own domain, admin credentials, and settings

### Access Methods

Configure how each instance is accessed:

**Traefik HTTPS** - Automatic SSL via Let's Encrypt. Point your domain DNS to the server and Traefik handles certificates with auto-renewal.

**Cloudflare Tunnels** - No exposed ports needed. Cloudflare handles the tunnel connection; works behind NAT or restrictive firewalls.

**Tailscale** - Private network access. All instances accessible from your devices over Tailscale without exposing to the public internet.

**Direct HTTP** - Basic access on localhost (default port 8000). No HTTPS, suitable for development or behind another reverse proxy.

Each instance can use any combination of these methods simultaneously.

### Consume Folder Integration

Paperless needs an input folder for document importing. Multiple options are available:

**Syncthing** - Peer-to-peer folder sync. Documents sync automatically from your devices to the consume folder. Each instance runs its own Syncthing container with separate sync credentials.

**Samba** (SMB) - Traditional network file shares. Browse and drag-drop files over your local network or Tailscale connection.

**SFTP** - Secure file transfer. Import documents via SSH/SFTP with per-instance credentials.

Each instance can enable the consume methods it needs. Syncthing config and folder state are backed up and restored automatically.

### Backup System

Two backup types:

**Instance backups** - Per-instance snapshots:
- Full and incremental snapshots uploaded to pCloud
- Docker image version tracking for reproducible restores
- Point-in-time recovery from any snapshot
- Includes Syncthing config and consume folder

**Whole system backup** - Metadata and system state:
- Captures overall system configuration and all registered instances
- Triggers full backup of all instances when created
- Primary purpose: preserve system metadata (instance list, configurations, settings)
- On restore: recreates system structure and restores each instance from its LATEST available backup (not the backup from system backup time)
- System-level restoration on fresh hardware

Each instance snapshot includes:
- PostgreSQL database dump
- Incremental tarballs (media, data, export, Syncthing config)
- Environment and compose configuration
- Docker image versions
- Manifest with metadata and checksums

This allows full disaster recovery on new hardware: install the system, provide pCloud credentials, restore from system backup to recreate all instances with their latest data.

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

- Ubuntu 24.04 LTS (may work on other distros but tested on 24.04)
- 4GB RAM minimum (8GB+ for multiple instances)
- 20GB+ disk space
- Root/sudo access
- pCloud account for backups (other rclone providers supported: Dropbox, Google Drive, etc., but untested)

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
    → Name: personal
    → Domain: docs.mydomain.com
    → Access: Traefik + Tailscale
    → Consume: Syncthing
  → Add new instance  
    → Name: dad
    → Domain: docs-dad.mydomain.com
    → Access: Cloudflare Tunnel
    → Consume: Syncthing
```

Each instance runs independently with its own database and backups.

---

## Management Interface

```
╔══════════════════════════════════════════════════════════╗
║        Paperless-NGX Bulletproof Manager                 ║
╚══════════════════════════════════════════════════════════╝

Current Instance: personal [Running]

  1) Manage Instances
  2) Browse Backups
  3) System Backup/Restore
  4) Manage Traefik (HTTPS)
  5) Manage Cloudflare Tunnel
  6) Manage Tailscale
  7) Configure Backup Server
  8) Nuke Setup (Clean Start)
  0) Quit
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

The system uses a tiered backup schedule to balance storage efficiency with recovery flexibility:

| Type        | Default Schedule     | Purpose                              |
|-------------|----------------------|--------------------------------------|
| Incremental | Every 6 hours        | Frequent checkpoints, small size     |
| Full        | Weekly (Sun 03:30)   | Complete snapshot, faster restores   |
| Archive     | Monthly (1st, 04:00) | Long-term retention                  |

**Retention policy:**
- All backups kept for 30 days
- After 30 days, only monthly archives are kept (up to 180 days)
- Non-archive backups older than 30 days are automatically cleaned up

This means you get granular recovery options for recent work, but older history consolidates to monthly snapshots to save storage.

Backups go to: `pcloud:backups/paperless/{instance_name}/`

### Manual Backup

```bash
paperless
→ Manage Instances
→ [instance name]
→ Backup now
```

### Restore

```bash
paperless
→ Manage Instances
→ [instance name]
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

### Disaster Recovery

To recover on fresh hardware after complete system failure:

1. Install Ubuntu 24.04 LTS on new machine
2. Run the installer with your pCloud credentials:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py | sudo python3
   ```
3. During installation, provide the same pCloud OAuth token
4. After installation completes, launch the manager:
   ```bash
   paperless
   ```
5. Select "System Backup/Restore" and choose your system backup snapshot
6. The system will:
   - Download system metadata (instance list, configurations, settings)
   - Restore each instance from its LATEST available backup in pCloud
   - Recreate all instances with their most recent data
   - Restore Syncthing configs for consume folder sync

System backup preserves metadata. Instance data comes from the most recent instance backups, not from the backup created at system backup time.

---

## Configuration

### Presets

Choose during installation:

**Traefik** - Reverse proxy with automatic HTTPS via Let's Encrypt. Requires domain DNS pointing to server.

**Cloudflare Tunnel** - Automated tunnel with Cloudflare. No port forwarding needed.

**Direct** - Direct HTTP access on localhost (default port 8000). No HTTPS.

### Environment Variables

All settings in `/home/docker/[instance_name]-setup/.env`:

```bash
INSTANCE_NAME=personal
DATA_ROOT=/home/docker/personal
STACK_DIR=/home/docker/personal-setup

PAPERLESS_ADMIN_USER=admin
PAPERLESS_ADMIN_PASSWORD=...
PAPERLESS_URL=https://your-domain.com

POSTGRES_PASSWORD=...

# Access Methods
ENABLE_TRAEFIK=yes
ENABLE_CLOUDFLARED=no
ENABLE_TAILSCALE=yes

DOMAIN=docs.example.com
HTTP_PORT=8000

# Consume Folder
CONSUME_SYNCTHING_ENABLED=yes
CONSUME_SYNCTHING_SYNC_PORT=22000
CONSUME_SYNCTHING_GUI_PORT=8384

CONSUME_SAMBA_ENABLED=no
CONSUME_SFTP_ENABLED=no

# Backup schedule (cron format: minute hour day month weekday)
CRON_INCR_TIME=0 */6 * * *    # Every 6 hours
CRON_FULL_TIME=30 3 * * 0     # Sunday at 03:30
CRON_ARCHIVE_TIME=0 4 1 * *   # 1st of month at 04:00

# Retention
RETENTION_DAYS=30             # Keep all backups this long
RETENTION_MONTHLY_DAYS=180    # Keep monthly archives this long
```

### Multiple Instances

Instance metadata stored in `/etc/paperless-bulletproof/instances.json`

Add existing instance:
```bash
paperless
→ Manage Instances
→ Add new instance or restore existing
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
│   ├── traefik.py      # Traefik configuration
│   └── consume.py      # Consume folder setup (Syncthing/Samba/SFTP)
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
- **main**: Stable releases
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

### Syncthing Not Connecting
- Check Syncthing GUI at http://tailscale-ip:8384
- Verify device IDs match between instances
- Check consume folder permissions

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
cd /home/docker/[instance_name]-setup
docker compose down -v

# Optional: remove files
sudo rm -rf /home/docker/[instance_name]
sudo rm -rf /home/docker/[instance_name]-setup
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
- Configures optional services (Traefik, Cloudflare, Tailscale, Syncthing, Samba, SFTP)

**Manager** (`lib/manager.py`)
- Interactive TUI
- Multi-instance management
- Backup/restore operations
- Health monitoring
- Container control
- Access method configuration

**Modules** (`lib/modules/`)
- `backup.py`: Creates and uploads snapshots
- `restore.py`: Downloads and applies snapshots

**Utilities** (`lib/utils/`)
- `common.py`: Shared output and environment functions
- `selftest.py`: Stack health validation

---

## License

MIT License - See [LICENSE](LICENSE) file for details.

---

## Support

- [GitHub Issues](https://github.com/obidose/obidose-paperless-ngx-bulletproof/issues)
- [Paperless-NGX Project](https://github.com/paperless-ngx/paperless-ngx)
- In-app help via `paperless` → "About & Help"

