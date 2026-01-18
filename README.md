# 📄 Paperless-NGX Bulletproof

**A production-ready, multi-instance Paperless-NGX deployment system with automated backups, disaster recovery, and zero-downtime updates.**

Perfect for managing multiple Paperless-NGX instances (personal, family, clients) from a single command with enterprise-grade reliability.

---

## 🚀 Quick Start

### One-Command Installation

#### Stable Release (main branch)
```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py > /tmp/paperless.py && sudo python3 /tmp/paperless.py
```

#### Development Version (dev branch)
```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/paperless.py > /tmp/paperless.py && sudo python3 /tmp/paperless.py --branch dev
```

That's it! The installer will:
- ✅ Install Docker & Docker Compose
- ✅ Configure rclone with pCloud backup
- ✅ Set up the management system
- ✅ Launch the interactive manager

### After Installation

Simply run:
```bash
paperless
```

---

## ✨ Features

### 🏢 Multi-Instance Management
- **Isolated Instances**: Each with own database, media, and configuration
- **Family & Client Support**: Run separate instances for different users/organizations
- **Easy Switching**: Manage all instances from one unified interface

### 💾 Enterprise-Grade Backups
- **Automated Snapshots**: One-command backup of entire instance
- **Docker Version Tracking**: Capture exact image versions for reproducible restores
- **Point-in-Time Recovery**: Browse and restore from any previous snapshot
- **System-Level Backup**: Disaster recovery for multi-instance configurations

### 🔄 Zero-Downtime Updates
- **Safe Update Workflow**: Automatic backup before upgrade
- **Health Checks**: Verify services after update
- **Rollback Ready**: Restore previous snapshot if needed

### 🎨 Professional Interface
- **Clean TUI**: Box-bordered menus with color coding
- **Visual Hierarchy**: Important information prominently displayed
- **Intuitive Navigation**: Numeric menu system (0=back, 1-9=options)

---

## 📋 Requirements

- **OS**: Ubuntu 22.04 or 24.04 LTS
- **RAM**: 4GB minimum (8GB+ recommended for multiple instances)
- **Disk**: 20GB+ available space
- **Access**: Root/sudo privileges for installation
- **Backup**: pCloud account (free 10GB available)

---

## First Run

The installation is smart and guides you through:

### Fresh Machine
1. **Installs base system** (Docker, rclone - fully automated)
2. **Connects to pCloud** (paste OAuth token - takes 30 seconds)
3. **Launches manager** with overview of system status

From the manager menu:
- **Add Instance** → Create fresh or restore from backup
- Each instance can have its own domain, settings, users
- All instances share the same base system but are isolated

### Example: Family Setup
```
paperless               # Launch manager
→ Instances
  → Add new instance
    → Name: paperless-personal
    → Domain: docs.mydomain.com
  → Add new instance  
    → Name: paperless-dad
    → Domain: docs-dad.mydomain.com
```

Each instance:
- Runs independently
- Has its own database, media, documents
- Backed up separately to pCloud
- Can be restored independently

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
- 13-point comprehensive health check
- System checks: Docker, files, directories, rclone
- Stack checks: containers, database, Redis, Django, HTTP
- Container name verification (catches project mismatches)
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

## 📁 Project Structure

```
paperless.py              # Single entry point (CLI)
lib/                      # All Python modules
├── manager.py           # Interactive TUI manager
├── installer/           # Installation modules
│   ├── common.py       # Configuration & helpers
│   ├── deps.py         # System dependencies
│   ├── files.py        # File generation
│   └── pcloud.py       # Backup configuration
├── modules/             # Core functionality
│   ├── backup.py       # Snapshot creation
│   └── restore.py      # Snapshot restoration
└── utils/               # Utilities
    └── selftest.py     # Health checks
compose/                  # Docker Compose templates
├── docker-compose-direct.yml
└── docker-compose-traefik.yml
presets/                  # Environment presets
├── direct.env
└── traefik.env
```

---

## 🛠️ Development

### Repository
```bash
git clone https://github.com/obidose/obidose-paperless-ngx-bulletproof.git
cd obidose-paperless-ngx-bulletproof

# Test locally
sudo python3 paperless.py --branch dev
```

### Branches
- **main**: Stable, production-ready
- **dev**: Latest features, active development

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

## 🤝 Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Test on fresh Ubuntu install
4. Submit pull request

---

## 📝 License

MIT License

---

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/obidose/obidose-paperless-ngx-bulletproof/issues)
- **Documentation**: This README and inline help (`paperless` → "About & Help")

---

**Made with ❤️ for reliable document management**
