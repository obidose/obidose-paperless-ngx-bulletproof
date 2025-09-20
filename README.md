# Paperless-ngx Bulletproof

A modern, backup-centric management system for **Paperless-ngx** with multi-instance support, comprehensive backup automation, and enhanced diagnostics.

- Docker + Docker Compose

- 🏗️ **Modular architecture** - clean separation of UI, cloud storage, instance management, and backup operations- Optional **Traefik** reverse proxy + Let's Encrypt (HTTPS)

- 🔄 **Multi-instance support** - manage multiple Paperless-ngx instances from a single dashboard- **pCloud** off‑site backup storage (OAuth, region auto‑detect)

- ☁️ **Universal cloud storage** - works with any rclone-supported provider (pCloud, Google Drive, Dropbox, etc.)- Easy **backup / restore / safe upgrades / status** via the `bulletproof` CLI

- 🔐 **Enhanced authentication** - OAuth with automatic region detection and WebDAV fallback- Cron‑based nightly snapshots with retention

- 🎨 **Modern CLI interface** - beautiful, intuitive command-line experience with visual feedback

- 📦 **Docker + Docker Compose** integration> Designed for backup reliability: all operations center around secure pCloud backup storage with automated snapshots and easy restoration.

- 🔒 **Optional Traefik** reverse proxy with Let's Encrypt HTTPS

- 🛡️ **Comprehensive backup system** - automated snapshots with retention policies---

- ⚡ **Simplified installation** - lightweight installer that downloads and configures everything

## Bulletproof CLIrless‑ngx Bulletproof Installer

> **Backup-First Philosophy**: Every operation centers around reliable cloud backup storage with automated snapshots and effortless restoration.

A one‑shot, “batteries‑included” setup for **Paperless‑ngx** on Ubuntu 22.04/24.04 with:

---- Docker + Docker Compose

- Optional **Traefik** reverse proxy + Let’s Encrypt (HTTPS)

## 🚀 Quick Start- **pCloud** off‑site b## Bulletproof CLI



### InstallationThe `bulletproof` command provides comprehensive management for **multiple** Paperless‑ngx instances running simultaneously. Each instance operates independently with its own:



```bash- Docker stack and data directories

# Download and run the installer- Backup schedule and retention settings  

curl -fsSL https://raw.githubusercontent.com/obidose/paperless-ngx-bulletproof/main/install.py | python3- Network ports and domain configuration

```- Database and media storage



The installer will:### Multi-Instance Overview

1. Install system prerequisites (Docker, Docker Compose, rclone)

2. Download the latest bulletproof CLIRunning `bulletproof` with no arguments shows a dashboard of all instances:

3. Launch the setup wizard

```

### First Instance Setup=== Paperless-ngx Instances ===

 # NAME                 STAT SCHEDULE

```bash 1 production           up   Full: every Sunday at 03:30, Incr: every day at 00:00

# Launch the bulletproof dashboard 2 testing              down Full: every Saturday at 02:00, Incr: every 6h

bulletproof 3 archive              up   Full: monthly 1 at 04:00, Archive: day 1 every month at 05:00

``````



Follow the interactive setup to:### Management Features

1. Configure cloud storage (first-time only)

2. Create your first Paperless-ngx instanceFrom the main menu you can:

3. Set up automated backups

- **Backup** individual instances or all at once (Full/Incremental/Archive)

---- **Add instances** from scratch or by restoring from existing backups

- **Start/Stop/Delete** individual or all instances

## 🏗️ Architecture Overview- **Explore backups** to browse, verify, and selectively restore snapshots



The system is built with a clean modular architecture:### Per-Instance Management



### Core ModulesSelect any instance to access detailed management:



```- **Start/Stop** the instance

tools/- **Backup** with custom scheduling  

├── bulletproof.py      # Main CLI entry point and orchestration- **View snapshots** and restore from specific backups

├── ui.py              # Visual interface, colors, formatting, user input- **Upgrade** Paperless-ngx safely (backup → pull images → restart)

├── cloud_storage.py   # Cloud storage setup and rclone management  - **View logs** and check status

├── instance.py        # Instance lifecycle, discovery, operations- **Rename** or **delete** the instance

├── backup_restore.py  # Backup creation, snapshot management, restore- **Configure backup schedule** (times, retention, archival)

└── bulletproof_old.py # Previous monolithic version (backup)

```### Backup Independence



### Module ResponsibilitiesEach instance maintains its own backup chain in pCloud at:

```

**`ui.py`** - User Interfacepcloud:backups/paperless/${INSTANCE_NAME}/

- Color schemes and visual formatting```

- Interactive prompts and input handling

- Table printing and menu systemsThis allows you to:

- Consistent messaging (success, warnings, errors)- Run different backup schedules per instance

- Restore instances selectively  

**`cloud_storage.py`** - Cloud Storage Management- Share backups between different servers

- Universal rclone configuration for any cloud provider- Maintain separate retention policies

- OAuth authentication with automatic region detection

- WebDAV fallback for providers requiring credentialsManual backups prompt for **Full**, **Incremental**, or **Archive** modes when no specific mode is provided.e** (OAuth, region auto‑detect)

- Connection testing and validation- Easy **backup / restore / safe upgrades / status** via the `bulletproof` CLI

- Cron‑based nightly snapshots with retention

**`instance.py`** - Instance Management

- Multi-instance discovery and tracking> Designed for minimal input: you provide a couple of answers, the script handles the rest.

- Docker Compose lifecycle (start, stop, logs)

- Directory structure creation and management---

- Instance configuration and environment handling

## Contents

**`backup_restore.py`** - Backup & Restore Operations

- Snapshot creation with multiple backup modes- [What it sets up](#what-it-sets-up)

- Remote backup exploration and verification- [Requirements](#requirements)

- Complete restore workflows with data integrity checks- [Quick start](#quick-start)

- Backup retention and cleanup policies- [pCloud auth (OAuth)](#pcloud-auth-oauth)

- [Presets](#presets)

**`bulletproof.py`** - Main CLI Orchestration- [Interactive wizard](#interactive-wizard)

- Argument parsing and command routing- [Backup & snapshots](#backup--snapshots)

- Multi-instance dashboard and navigation- [Restore](#restore)

- Single-instance management interfaces- [Bulletproof CLI](#bulletproof-cli)

- Integration between all modules- [Troubleshooting](#troubleshooting)

- [Uninstall / remove stack](#uninstall--remove-stack)

---

---

## 🎯 Features

## What it sets up

### Multi-Instance Management

- A Docker Compose stack for Paperless‑ngx and dependencies:

Manage multiple Paperless-ngx instances from a unified dashboard:  - **PostgreSQL**, **Redis**, **Gotenberg**, **Tika**, and optionally **Traefik**

- Persistent data tree at (defaults):

```  - `/home/docker/paperless` — data, media, export, db, etc.

╔══════════════════════════════════════════════════════════╗  - `/home/docker/paperless-setup` — compose files, `.env`, helper scripts

║                    Paperless-ngx Bulletproof             ║- **rclone** remote named `pcloud:` configured via OAuth and **auto‑switch** to the correct pCloud API region

║                Multi-Instance Management                 ║- `backup.py` script and `bulletproof` CLI placed into the stack dir

╚══════════════════════════════════════════════════════════╝-  Cron job for nightly snapshots with retention

- `bulletproof` command for managing multiple instances, backups, safe upgrades,

  #  Name              Status          Schedule  listing snapshots, restores, health, and logs

──────────────────────────────────────────────────────────────

  1  production        ● Running       weekly on Sunday at 3:30 AM---

  2  testing           ● Stopped       daily at midnight  

  3  archive           ● Running       monthly on 1st at 4:00 AM## Requirements



Multi-Instance Actions:- Ubuntu **22.04** or **24.04**

  c - Create new instance- Run as **root** (or prefix commands with `sudo`)

  s - Start all instances  - A pCloud account  

  d - Stop all instances  - OAuth is used (no app password required)

  e - Explore backups

  o - Configure cloud storage> DNS should already point to your host **if** you choose Traefik + HTTPS, so Let’s Encrypt can issue certs.

  q - Quit

```---



### Universal Cloud Storage## Quick start



Works with **any** rclone-supported cloud provider:The installer is lightweight and simply installs the bulletproof CLI with prerequisites. All actual functionality (pCloud setup, instance management, backups) is handled by the enhanced bulletproof CLI itself.



- **pCloud** (OAuth + WebDAV with region auto-detection)```bash

- **Google Drive** (OAuth)curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/install.py | sudo python3 -

- **Dropbox** (OAuth)```

- **OneDrive** (OAuth)

- **Amazon S3** (Access keys)This will:

- **And 40+ other providers**1. Install Docker, rclone, and other prerequisites

2. Download and install the bulletproof CLI 

The system automatically detects the best authentication method and guides you through setup.3. Launch the enhanced bulletproof interface automatically



### Intelligent Backup SystemAll subsequent management is done through the `bulletproof` command.



Three backup modes with automatic retention:### Fresh Ubuntu Installation



- **📱 Incremental** - Fast daily backups of changes onlyOn a fresh Ubuntu 22.04/24.04 host, the installer will:

- **📦 Full** - Complete weekly snapshots for reliability  

- **🗄️ Archive** - Monthly long-term storage with cleanup1. **Install prerequisites** (Docker, rclone, etc.)

2. **Download and install** the bulletproof CLI

### Modern CLI Experience3. **Launch bulletproof** for all setup and management



- 🎨 **Rich visual feedback** with colors and iconsThe **bulletproof** CLI then handles:

- 📊 **Formatted tables** for instance and snapshot listings- **pCloud setup** using OAuth token (recommended) or WebDAV - **required for all operations**

- 🖱️ **Interactive menus** with clear navigation- **Checking for existing backups** in your pCloud storage

- ⚡ **Context-aware commands** that adapt to your situation- **Instance creation** with guided configuration and automatic backup setup

- 🔍 **Detailed diagnostics** for troubleshooting- **Backup and restore operations** - the core functionality

- **Multi-instance management** with independent backup chains

---

### Subsequent Runs

## 📋 System Requirements

Once installed, simply run:

- **Ubuntu 22.04** or **24.04** LTS```bash

- **2GB RAM** minimum (4GB recommended)bulletproof

- **20GB storage** minimum (more for document storage)```

- **Internet connection** for downloads and cloud storage

- **Root/sudo access** for initial setupThis launches the enhanced CLI interface that provides:

- **Multi-instance dashboard** showing all your Paperless-ngx instances

---- **Smart pCloud setup** - prominently offered when not configured, with clear status indicators

- **Instance creation** wizard for new setups

## 🎮 Usage Guide- **Backup exploration** to restore from existing backups

- **Complete management** of all instances

### Creating Instances

When you first run `bulletproof` after installation, if pCloud isn't set up yet, you'll see:

**From Scratch:**- Clear status showing "pCloud setup required" 

```bash- "Set up pCloud" as the primary option

bulletproof create- Alternative options that explain pCloud requirements

```

### Enhanced CLI Interface

**From Existing Backup:**

```bashThe bulletproof CLI now features a modern, visually appealing interface with:

bulletproof create- **Colorized output** with status indicators and icons

# Choose instance name that matches existing backup- **Tabular instance display** with status and backup schedules

# System will detect backup and offer restore option- **Interactive menus** with clear options and descriptions

```- **Smart pCloud integration** with automatic region detection

- **Comprehensive backup management** with multiple restore options

### Managing Instances

### Dev branch testing

**Single Instance Operations:**

```bashTo test development versions, specify the branch:

bulletproof --instance production backup full

bulletproof --instance production status  ```bash

bulletproof --instance production logscurl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/install.py \

```  | BP_BRANCH=dev sudo -E python3 - --branch dev

```

**Interactive Management:**

```bashThe installer will download the bulletproof CLI from the specified branch.

bulletproof --instance production

# Opens instance-specific management menu### Installation Flow

```

The installation process is now streamlined:

### Backup Operations

1. **Run the installer** - Downloads and installs bulletproof CLI with prerequisites

**Manual Backups:**2. **Launch bulletproof** - Automatically starts the enhanced CLI interface

```bash3. **Set up pCloud** - Interactive OAuth or WebDAV configuration

bulletproof backup full        # Complete backup4. **Choose your action**:

bulletproof backup incr        # Incremental backup     - **Create new instance** - Guided setup wizard for fresh installations

bulletproof backup archive     # Archive with cleanup   - **Restore from backup** - Browse and restore existing instances

```   - **Explore backups** - Advanced backup management and verification



**Restore Operations:****Creating a new instance** walks you through:

```bash- Instance name and basic configuration

bulletproof restore                    # Interactive selection- Directory paths and admin credentials

bulletproof restore snapshot-name     # Specific snapshot- HTTPS/Traefik setup (optional)

```- Backup schedule configuration

- Automatic Docker stack creation and startup

**Exploring Backups:**

```bash> The bulletproof CLI command is available globally after installation:

bulletproof                   # Main menu → "Explore backups"> ```bash

```> bulletproof --help          # See all available commands

> bulletproof                 # Launch interactive multi-instance manager

### Cloud Storage Setup> bulletproof create          # Create a new instance

> bulletproof setup-pcloud    # Configure pCloud backup storage

**Initial Configuration:**> ```

```bash

bulletproof setup-pcloud      # Generic cloud storage setup---

```

## pCloud auth (OAuth)

**Reconfigure Storage:**

```bashDuring install you’ll see:

bulletproof                   # Main menu → "Configure cloud storage"

```- **Paste OAuth token JSON (recommended)**  

  Use a machine **with a browser** and run:

---  ```bash

  rclone authorize "pcloud"

## 🔧 Configuration  ```

  Copy the printed JSON and paste it back into the installer.

### Instance Structure

- **Headless OAuth helper**  

Each instance creates:  The installer guides you through running `rclone authorize "pcloud"` on another machine and pasting the JSON.

```

/home/docker/- **Legacy WebDAV** (best‑effort only)  

├── instance-name/           # Data directory  Use only if OAuth isn’t possible. Some networks/regions block EU WebDAV; OAuth avoids this.

│   ├── data/               # Paperless data

│   ├── media/              # Document storageThe installer **auto‑detects EU/Global** pCloud API and configures the `pcloud:` remote accordingly.

│   ├── export/             # Export location

│   └── pgdata/             # PostgreSQL database---

└── instance-name-setup/    # Stack directory

    ├── docker-compose.yml  # Docker configuration## Presets

    ├── .env               # Environment variables

    └── backup.py          # Backup scriptYou can load defaults before answering prompts:

```

- `presets/traefik.env` – enable Traefik + HTTPS (you’ll enter domain + email)

### Backup Location- `presets/direct.env` – run Paperless‑ngx bound to a local HTTP port

- Provide a **URL** or a **local .env** file

Cloud backups stored at:- Or **Skip**

```

{cloud-provider}:backups/paperless/{instance-name}/Presets are **merged** into the run so you can still change anything interactively.

├── 2024-03-15-full/        # Full backup snapshot

├── 2024-03-16-incr/        # Incremental snapshot---

├── 2024-03-17-incr/        # Incremental snapshot

└── 2024-03-22-archive/     # Archive snapshot## Interactive wizard

```

You’ll be prompted for:

### Environment Variables

- **Timezone** (e.g., `Europe/London`)

Key configuration in `.env`:- **Instance name** (default: `paperless`)

```bash- **Data root** (default: `/home/docker/paperless`)

# Instance identification- **Stack dir** (default: `/home/docker/paperless-setup`)

INSTANCE_NAME=production- **Admin user / password** for Paperless‑ngx

DATA_ROOT=/home/docker/production- **Postgres password**

STACK_DIR=/home/docker/production-setup- **Enable Traefik with HTTPS?** (`yes`/`no`; `y` is accepted)

  - If **yes**: **Domain** and **Let’s Encrypt email**

# Paperless configuration

PAPERLESS_TIME_ZONE=UTCThe wizard writes:

PAPERLESS_ADMIN_USER=admin- `.env` → `/home/docker/paperless-setup/.env`

PAPERLESS_ADMIN_PASSWORD=secure-password- `docker-compose.yml` (Traefik on/off version)

- Helper script: `backup.py`

# Database configuration- Installs `bulletproof` CLI

POSTGRES_PASSWORD=database-password

Then it runs: `docker compose up -d` and performs a quick self-test

# Backup configuration  

RCLONE_REMOTE_NAME=pcloud---

RCLONE_REMOTE_PATH=backups/paperless/production

REMOTE=pcloud:backups/paperless/production## Backup & snapshots



# Backup schedule (cron format)Automated cron jobs upload snapshots to pCloud:

CRON_FULL_TIME=30 3 * * 0      # Sunday 3:30 AM- **Weekly full** backup at a scheduled time

CRON_INCR_TIME=0 0 * * *       # Daily midnight- **Daily incremental** backups chaining to the last full

CRON_ARCHIVE_TIME=0 4 1 * *    # 1st of month 4:00 AM- Optional **monthly archive** snapshot kept separately

- Remote: `pcloud:backups/paperless/${INSTANCE_NAME}`

# HTTPS configuration (optional) - Snapshot naming: `YYYY-MM-DD_HH-MM-SS`

DOMAIN=paperless.example.com - Full snapshots are self-contained; incrementals reference their parent

EMAIL=admin@example.com- Includes:

TRAEFIK_ENABLED=yes  - Encrypted `.env` (if enabled) or plain `.env`

```  - `compose.snapshot.yml` (set `INCLUDE_COMPOSE_IN_BACKUP=no` to skip)

  - Tarballs of `media`, `data`, `export` (incremental)

---  - Postgres SQL dump

  - Paperless-NGX version

## 🔍 Troubleshooting  - `manifest.yaml` with versions, file sizes + SHA-256 checksums, host info, mode & parent

  - Integrity checks: archives are listed and the DB dump is test-restored; a `status.ok`/`status.fail` file records the result

### Common Issues - Retention: keep last **N** full snapshots and **M** incrementals (`KEEP_FULLS`, `KEEP_INCS`)

  Pruning removes older full snapshots along with their incremental chains; for the latest full snapshot only the newest `KEEP_INCS` incrementals are retained.

**"Cloud storage remote not configured"**

```bash You can also trigger a backup manually (see **Bulletproof CLI**). Manual backups prompt for **Full** or **Incremental**.

bulletproof setup-pcloud

# Follow OAuth or WebDAV setup wizardDuring installation you're guided through choosing the full/incremental cadence

```and whether to enable a monthly archive. Adjust these later with

`bulletproof schedule`.

**"Instance not found"**Times may be entered as `HH:MM` or `HHMM` (e.g., `2330` for 23:30); invalid

```bashvalues prompt again with an error.

bulletproof                  # Check instance list

bulletproof create           # Create new instance---

```

## Restore

**"Docker permission denied"**

```bash### From installer (early restore)

sudo usermod -aG docker $USERIf snapshots exist, the installer can **restore first**:

# Log out and back in- Select the latest or a specific snapshot

```- Decrypt `.env` if needed (passphrase file or prompt)

- Restore data archives and DB, then start stack

**"rclone not found"**

```bash### From the CLI

curl https://rclone.org/install.sh | sudo bashUse **Bulletproof** to pick a snapshot and restore it at any time.

```

A self-test runs after the stack is back up.

### Diagnostics

> Restores will stop the stack as needed and bring it back after import.

**System Check:**

```bashThe restore process walks any incremental chain automatically, applies the

bulletproof doctorsnapshot's `docker-compose.yml` by default (`USE_COMPOSE_SNAPSHOT=no` skips it)

```and lets you choose between the snapshot's Paperless‑NGX version or the latest

image.

**Instance Status:**

```bash---

bulletproof --instance name status

```## Bulletproof CLI



**Backup Verification:**`bulletproof` now manages **multiple** Paperless‑ngx instances. Running it with

```bashno arguments launches an overview showing status and backup schedules.

bulletproof                  # Main menu → "Explore backups"

```From the menu you can:



### Getting Help- Back up one instance or all at once

- Add new instances from scratch or by cloning an existing snapshot (remote

**Command Help:**  backups are listed so you don’t need to remember names)

```bash- Explore backup folders, inspect snapshots, and verify their integrity before

bulletproof --help  restoring

bulletproof backup --help- Start or stop every instance, or wipe them all (remote backups remain)

```- Drop into a per‑instance menu for upgrades, logs, scheduling, restore, rename,

  or delete

**Interactive Guidance:**

```bashManual backups still prompt for **Full** or **Incremental** when no mode is

bulletproof                  # Interactive dashboardprovided.

```

---

---

## Troubleshooting

## 🗂️ File Organization

- **OAuth token fails**  

### Project Structure  Make sure you paste the **exact** JSON from `rclone authorize "pcloud"` using the **same rclone version** if possible. If the token keeps failing validation, try generating a fresh token or use WebDAV instead.

```

📁 obidose-paperless-ngx-bulletproof/- **pCloud region detection**  

├── 📄 install.py                    # Lightweight installer  The system automatically tests both pCloud regions (Global/US and Europe). If OAuth fails for both regions, your account might be in a different region or have specific restrictions. Try WebDAV (option 3) which works regardless of region.

├── 📄 README.md                     # This documentation

├── 📁 compose/                      # Docker Compose templates- **WebDAV timeouts / 401**  

│   ├── docker-compose-direct.yml   # Direct access template  Prefer **OAuth**. WebDAV endpoints can be region‑/network‑sensitive, but often work when OAuth fails due to network restrictions.

│   └── docker-compose-traefik.yml  # Traefik HTTPS template

├── 📁 tools/                        # Main application modules- **HTTPS not issuing**  

│   ├── 🐍 bulletproof.py          # Main CLI entry point  Confirm DNS points to this host and ports 80/443 are reachable. Traefik will retry challenges.

│   ├── 🎨 ui.py                   # User interface utilities

│   ├── ☁️ cloud_storage.py        # Cloud storage management- **Backup shows “No snapshots found”**

│   ├── 🏠 instance.py             # Instance lifecycle management  Run `bulletproof`, choose your instance, then run a **Backup** followed by

│   └── 💾 backup_restore.py       # Backup and restore operations  **Snapshots**. Verify the path shown matches

├── 📁 modules/                      # Support modules  `pcloud:backups/paperless/${INSTANCE_NAME}`. Check rclone with

│   └── backup.py                   # Backup execution script  `rclone about pcloud:`.

├── 📁 presets/                      # Configuration presets

│   ├── direct.env                  # Direct access preset- **Running without root**  

│   └── traefik.env                 # Traefik HTTPS preset  Use `sudo` for the installer and for `bulletproof` if your Docker requires it.

└── 📁 utils/                        # Utility scripts

    ├── env.py                      # Environment helpers---

    └── selftest.py                 # System validation

```## Uninstall / remove stack



---```bash

cd /home/docker/paperless-setup

## 📝 Migration Notesdocker compose down -v     # stop and remove containers + volumes

# Remove data only if you really want to wipe everything:

### From Previous Versions# rm -rf /home/docker/paperless

```

If upgrading from the monolithic version:

Your off‑site snapshots remain in **pCloud**.

1. **Backup Current Setup:**

   ```bash---

   # Current system automatically backed up as bulletproof_old.py

   ```## Notes



2. **Configuration Preserved:**- Installer is idempotent: safe to rerun to pick up fixes.

   - All existing instances continue working- You can change `.env` and run `docker compose up -d` anytime.

   - Backup schedules maintained- The `bulletproof` CLI is a Python script; read it to see what it does.

   - Cloud storage configuration preserved

3. **New Features Available:**
   - Multi-instance dashboard
   - Enhanced cloud storage support
   - Improved error handling and diagnostics
   - Better visual interface

### Module Benefits

The new modular architecture provides:

- **🧹 Cleaner Code:** Each module has a single responsibility
- **🔧 Easier Maintenance:** Updates isolated to specific functionality
- **🚀 Better Performance:** Reduced memory usage and faster imports
- **🧪 Enhanced Testing:** Individual modules can be tested separately
- **📈 Future Extensibility:** Easy to add new cloud providers or features

---

## 🤝 Contributing

Contributions welcome! The modular structure makes it easy to:

- **Add new cloud providers** in `cloud_storage.py`
- **Enhance UI elements** in `ui.py`  
- **Extend backup features** in `backup_restore.py`
- **Improve instance management** in `instance.py`

### Development Setup

```bash
git clone https://github.com/obidose/paperless-ngx-bulletproof.git
cd paperless-ngx-bulletproof/tools
python bulletproof.py --help
```

---

## 📄 License

MIT License - see LICENSE file for details.

---

**Made with ❤️ for the Paperless-ngx community**