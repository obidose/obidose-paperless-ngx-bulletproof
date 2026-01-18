# Paperless-NGX Bulletproof

One-command setup and management for Paperless-NGX with automated backups to pCloud.

## Quick Start

**One command does everything** - works on fresh or existing installations:

```bash
# From main branch (stable)
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py | sudo python3 -

# From dev branch (testing)
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/paperless.py | BP_BRANCH=dev sudo -E python3 - --branch dev
```

After installation, manage with: `paperless`

---

## What You Get

- **Paperless-NGX** with PostgreSQL, Redis, Gotenberg, Tika
- **Optional Traefik** with automatic HTTPS (Let's Encrypt)
- **Automated backups** to pCloud (full + incremental + optional archive)
- **Easy restore** from any snapshot with automatic chain resolution
- **Multi-instance** management
- **Health monitoring** and container management
- **Interactive TUI** for all operations

---

## Requirements

- Ubuntu 22.04 or 24.04
- Root access (run with `sudo`)
- pCloud account for backups

That's it! The installer handles Docker, rclone, and everything else.

---

## First Run

The unified command detects your environment and guides you:

### On Fresh Machine
```
Welcome to Paperless-NGX Bulletproof!

Options:
  1) Quick setup (guided installation)
  2) Advanced options (manual configuration)  
  3) Restore from existing backup

Choose [1-3]:
```

### On Existing Installation
Launches the interactive management TUI with all features.

---

## Management Interface

After installation, run: `paperless`

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

### Features

**Backup Management**
- Manual backups (full, incremental, archive)
- View all snapshots with metadata
- Configure automated schedules
- Retention policies

**Restore Operations**
- Interactive snapshot selection
- Automatic incremental chain resolution
- Safety confirmations
- Clone to new instances

**Multi-Instance**
- Track unlimited instances
- Switch between instances
- Add/remove instances
- Independent management

**Health Monitoring**
- 8-point system health check
- Docker and container status
- Data integrity validation
- Remote connectivity tests

**Container Operations**
- Start/stop/restart
- View logs in real-time
- Safe upgrades with auto-backup
- Pull latest images

---

## pCloud Setup

During installation, configure pCloud using **OAuth** (recommended):

1. On any machine with a browser, run:
   ```bash
   rclone authorize "pcloud"
   ```

2. Copy the JSON token that's printed

3. Paste it into the installer when prompted

The installer auto-detects EU vs Global regions and configures accordingly.

**Alternative**: Legacy WebDAV (OAuth is much more reliable)

---

## Backup & Restore

### Automated Backups

Configured during installation:
- **Full backups**: Weekly (configurable)
- **Incremental**: Daily (configurable)
- **Archive**: Monthly (optional)

All backups upload to: `pcloud:backups/paperless/{instance_name}/`

Each snapshot includes:
- PostgreSQL database dump
- Incremental tarballs (media, data, export)
- Environment configuration
- Docker compose file
- Manifest with metadata and checksums
- Integrity verification (archives tested, DB restored to temp container)

### Manual Backups

```bash
paperless
# → Option 3 (Backup management)
# → Choose full/incremental/archive
```

### Restore

```bash
paperless
# → Option 4 (Restore from backup)
# → Select snapshot (or "latest")
# → Confirm restoration
```

The restore process:
1. Stops containers
2. Downloads snapshot chain from pCloud
3. Applies incrementals in order
4. Restores database
5. Restarts containers
6. Runs health check

---

## Configuration

### Presets

During installation, choose a preset or skip:

**Traefik** (`presets/traefik.env`)
- Traefik reverse proxy
- Automatic HTTPS with Let's Encrypt
- Requires domain pointing to your server

**Direct** (`presets/direct.env`)
- Direct HTTP access
- Bind to localhost port (default 8000)
- No HTTPS

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

ENABLE_TRAEFIK=yes  # or "no"
DOMAIN=paperless.example.com
LETSENCRYPT_EMAIL=admin@example.com
HTTP_PORT=8000  # if ENABLE_TRAEFIK=no

RCLONE_REMOTE_NAME=pcloud
RCLONE_REMOTE_PATH=backups/paperless/paperless
RETENTION_DAYS=30

CRON_FULL_TIME=30 3 * * 0     # Weekly Sunday 3:30 AM
CRON_INCR_TIME=0 0 * * *      # Daily midnight
CRON_ARCHIVE_TIME=            # Disabled (or set cron)
```

### Multiple Instances

The manager tracks all instances in `/etc/paperless-bulletproof/instances.json`

To add an existing instance:
```bash
paperless
# → Option 6 (Instance management)
# → Option 2 (Add existing instance)
```

---

## File Structure

```
/usr/local/bin/
  └── paperless@ → /usr/local/lib/paperless-bulletproof/paperless.py

/usr/local/lib/paperless-bulletproof/
  ├── paperless.py           # Main entry point
  ├── paperless_manager.py   # TUI manager
  ├── installer/             # Installation modules
  │   ├── common.py
  │   ├── deps.py
  │   ├── files.py
  │   └── pcloud.py
  └── utils/
      └── selftest.py

/etc/paperless-bulletproof/
  └── instances.json         # Instance tracking

/home/docker/paperless-setup/
  ├── .env
  ├── docker-compose.yml
  ├── backup.py             # Backup script
  ├── restore.py            # Restore script
  └── backup.log

/home/docker/paperless/
  ├── data/
  ├── media/
  ├── export/
  ├── consume/
  ├── db/
  └── tika-cache/
```

---

## Troubleshooting

### pCloud OAuth Fails
- Ensure you paste the **complete JSON** token
- Use the same rclone version if possible
- Try WebDAV as fallback

### HTTPS Not Working
- Verify DNS points to your server
- Check ports 80/443 are accessible
- Review Traefik logs: `paperless` → Option 7 → View logs → traefik

### No Snapshots Found
- Run a manual backup first: `paperless` → Option 3
- Verify rclone: `rclone lsd pcloud:`
- Check remote path in .env file

### Containers Won't Start
- Check Docker: `docker ps -a`
- View logs: `paperless` → Option 7 → View logs
- Run health check: `paperless` → Option 5

### Backup Fails
- Check disk space: `df -h`
- Verify pCloud connection: `rclone about pcloud:`
- Review backup.log: `cat /home/docker/paperless-setup/backup.log`

---

## Uninstall

To remove the stack (keeps backups):

```bash
cd /home/docker/paperless-setup
docker compose down -v  # Removes containers and volumes

# Optional: Remove files
sudo rm -rf /home/docker/paperless
sudo rm -rf /home/docker/paperless-setup
sudo rm /usr/local/bin/paperless
sudo rm -rf /usr/local/lib/paperless-bulletproof
sudo rm -rf /etc/paperless-bulletproof
```

Your pCloud backups remain intact for future restoration.

---

## Architecture

**Entry Point** (`paperless.py`)
- Detects fresh vs existing installation
- Bootstraps from GitHub if needed
- Launches installer or manager

**Installer** (`installer/`)
- Installs Docker, rclone, dependencies
- Configures pCloud
- Creates docker-compose.yml and .env
- Sets up cron jobs

**Manager** (`paperless_manager.py`)
- Interactive TUI
- Multi-instance management
- Backup/restore operations
- Health monitoring
- Container control

**Modules** (`modules/`)
- `backup.py`: Snapshot creation and upload
- `restore.py`: Snapshot download and restoration

---

## Development

### Project Structure

```
paperless.py              # Main entry (bootstraps, detects state)
paperless_manager.py      # TUI manager (classes for UI)
install.py                # Legacy compat (redirects to paperless.py)

installer/
  ├── common.py           # Shared utilities, config
  ├── deps.py             # Install Docker, rclone
  ├── files.py            # Generate compose/env files
  └── pcloud.py           # pCloud configuration

modules/
  ├── backup.py           # Backup operations
  └── restore.py          # Restore operations

utils/
  └── selftest.py         # Post-install validation

presets/
  ├── traefik.env         # Traefik preset
  └── direct.env          # Direct HTTP preset

compose/
  ├── docker-compose-traefik.yml    # Template with Traefik
  └── docker-compose-direct.yml     # Template direct HTTP
```

### Testing Dev Branch

```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/paperless.py \
  | BP_BRANCH=dev sudo -E python3 - --branch dev
```

The `--branch dev` and `BP_BRANCH=dev` ensure everything pulls from dev branch.

---

## License

MIT

## Support

Issues and PRs welcome on GitHub: [obidose/obidose-paperless-ngx-bulletproof](https://github.com/obidose/obidose-paperless-ngx-bulletproof)
