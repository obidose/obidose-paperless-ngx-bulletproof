# Paperless‑ngx Bulletproof Installer

A backup-centric, "batteries‑included" setup for **Paperless‑ngx** on Ubuntu 22.04/24.04 with:
- Docker + Docker Compose
- Optional **Traefik** reverse proxy + Let's Encrypt (HTTPS)
- **pCloud** off‑site backup storage (OAuth, region auto‑detect)
- Easy **backup / restore / safe upgrades / status** via the `bulletproof` CLI
- Cron‑based nightly snapshots with retention

> Designed for backup reliability: all operations center around secure pCloud backup storage with automated snapshots and easy restoration.

---

## Bulletproof CLIrless‑ngx Bulletproof Installer

A one‑shot, “batteries‑included” setup for **Paperless‑ngx** on Ubuntu 22.04/24.04 with:
- Docker + Docker Compose
- Optional **Traefik** reverse proxy + Let’s Encrypt (HTTPS)
- **pCloud** off‑site b## Bulletproof CLI

The `bulletproof` command provides comprehensive management for **multiple** Paperless‑ngx instances running simultaneously. Each instance operates independently with its own:

- Docker stack and data directories
- Backup schedule and retention settings  
- Network ports and domain configuration
- Database and media storage

### Multi-Instance Overview

Running `bulletproof` with no arguments shows a dashboard of all instances:

```
=== Paperless-ngx Instances ===
 # NAME                 STAT SCHEDULE
 1 production           up   Full: every Sunday at 03:30, Incr: every day at 00:00
 2 testing              down Full: every Saturday at 02:00, Incr: every 6h
 3 archive              up   Full: monthly 1 at 04:00, Archive: day 1 every month at 05:00
```

### Management Features

From the main menu you can:

- **Backup** individual instances or all at once (Full/Incremental/Archive)
- **Add instances** from scratch or by restoring from existing backups
- **Start/Stop/Delete** individual or all instances
- **Explore backups** to browse, verify, and selectively restore snapshots

### Per-Instance Management

Select any instance to access detailed management:

- **Start/Stop** the instance
- **Backup** with custom scheduling  
- **View snapshots** and restore from specific backups
- **Upgrade** Paperless-ngx safely (backup → pull images → restart)
- **View logs** and check status
- **Rename** or **delete** the instance
- **Configure backup schedule** (times, retention, archival)

### Backup Independence

Each instance maintains its own backup chain in pCloud at:
```
pcloud:backups/paperless/${INSTANCE_NAME}/
```

This allows you to:
- Run different backup schedules per instance
- Restore instances selectively  
- Share backups between different servers
- Maintain separate retention policies

Manual backups prompt for **Full**, **Incremental**, or **Archive** modes when no specific mode is provided.e** (OAuth, region auto‑detect)
- Easy **backup / restore / safe upgrades / status** via the `bulletproof` CLI
- Cron‑based nightly snapshots with retention

> Designed for minimal input: you provide a couple of answers, the script handles the rest.

---

## Contents

- [What it sets up](#what-it-sets-up)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [pCloud auth (OAuth)](#pcloud-auth-oauth)
- [Presets](#presets)
- [Interactive wizard](#interactive-wizard)
- [Backup & snapshots](#backup--snapshots)
- [Restore](#restore)
- [Bulletproof CLI](#bulletproof-cli)
- [Troubleshooting](#troubleshooting)
- [Uninstall / remove stack](#uninstall--remove-stack)

---

## What it sets up

- A Docker Compose stack for Paperless‑ngx and dependencies:
  - **PostgreSQL**, **Redis**, **Gotenberg**, **Tika**, and optionally **Traefik**
- Persistent data tree at (defaults):
  - `/home/docker/paperless` — data, media, export, db, etc.
  - `/home/docker/paperless-setup` — compose files, `.env`, helper scripts
- **rclone** remote named `pcloud:` configured via OAuth and **auto‑switch** to the correct pCloud API region
- `backup.py` script and `bulletproof` CLI placed into the stack dir
-  Cron job for nightly snapshots with retention
- `bulletproof` command for managing multiple instances, backups, safe upgrades,
  listing snapshots, restores, health, and logs

---

## Requirements

- Ubuntu **22.04** or **24.04**
- Run as **root** (or prefix commands with `sudo`)
- A pCloud account  
  - OAuth is used (no app password required)

> DNS should already point to your host **if** you choose Traefik + HTTPS, so Let’s Encrypt can issue certs.

---

## Quick start

The installer is lightweight and simply installs the bulletproof CLI with prerequisites. All actual functionality (pCloud setup, instance management, backups) is handled by the enhanced bulletproof CLI itself.

```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/install.py | sudo python3 -
```

This will:
1. Install Docker, rclone, and other prerequisites
2. Download and install the bulletproof CLI 
3. Launch the enhanced bulletproof interface automatically

All subsequent management is done through the `bulletproof` command.

### Fresh Ubuntu Installation

On a fresh Ubuntu 22.04/24.04 host, the installer will:

1. **Install prerequisites** (Docker, rclone, etc.)
2. **Download and install** the bulletproof CLI
3. **Launch bulletproof** for all setup and management

The **bulletproof** CLI then handles:
- **pCloud setup** using OAuth token (recommended) or WebDAV - **required for all operations**
- **Checking for existing backups** in your pCloud storage
- **Instance creation** with guided configuration and automatic backup setup
- **Backup and restore operations** - the core functionality
- **Multi-instance management** with independent backup chains

### Subsequent Runs

Once installed, simply run:
```bash
bulletproof
```

This launches the enhanced CLI interface that provides:
- **Multi-instance dashboard** showing all your Paperless-ngx instances
- **Smart pCloud setup** - prominently offered when not configured, with clear status indicators
- **Instance creation** wizard for new setups
- **Backup exploration** to restore from existing backups
- **Complete management** of all instances

When you first run `bulletproof` after installation, if pCloud isn't set up yet, you'll see:
- Clear status showing "pCloud setup required" 
- "Set up pCloud" as the primary option
- Alternative options that explain pCloud requirements

### Enhanced CLI Interface

The bulletproof CLI now features a modern, visually appealing interface with:
- **Colorized output** with status indicators and icons
- **Tabular instance display** with status and backup schedules
- **Interactive menus** with clear options and descriptions
- **Smart pCloud integration** with automatic region detection
- **Comprehensive backup management** with multiple restore options

### Dev branch testing

To test development versions, specify the branch:

```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/install.py \
  | BP_BRANCH=dev sudo -E python3 - --branch dev
```

The installer will download the bulletproof CLI from the specified branch.

### Installation Flow

The installation process is now streamlined:

1. **Run the installer** - Downloads and installs bulletproof CLI with prerequisites
2. **Launch bulletproof** - Automatically starts the enhanced CLI interface
3. **Set up pCloud** - Interactive OAuth or WebDAV configuration
4. **Choose your action**:
   - **Create new instance** - Guided setup wizard for fresh installations
   - **Restore from backup** - Browse and restore existing instances
   - **Explore backups** - Advanced backup management and verification

**Creating a new instance** walks you through:
- Instance name and basic configuration
- Directory paths and admin credentials
- HTTPS/Traefik setup (optional)
- Backup schedule configuration
- Automatic Docker stack creation and startup

> The bulletproof CLI command is available globally after installation:
> ```bash
> bulletproof --help          # See all available commands
> bulletproof                 # Launch interactive multi-instance manager
> bulletproof create          # Create a new instance
> bulletproof setup-pcloud    # Configure pCloud backup storage
> ```

---

## pCloud auth (OAuth)

During install you’ll see:

- **Paste OAuth token JSON (recommended)**  
  Use a machine **with a browser** and run:
  ```bash
  rclone authorize "pcloud"
  ```
  Copy the printed JSON and paste it back into the installer.

- **Headless OAuth helper**  
  The installer guides you through running `rclone authorize "pcloud"` on another machine and pasting the JSON.

- **Legacy WebDAV** (best‑effort only)  
  Use only if OAuth isn’t possible. Some networks/regions block EU WebDAV; OAuth avoids this.

The installer **auto‑detects EU/Global** pCloud API and configures the `pcloud:` remote accordingly.

---

## Presets

You can load defaults before answering prompts:

- `presets/traefik.env` – enable Traefik + HTTPS (you’ll enter domain + email)
- `presets/direct.env` – run Paperless‑ngx bound to a local HTTP port
- Provide a **URL** or a **local .env** file
- Or **Skip**

Presets are **merged** into the run so you can still change anything interactively.

---

## Interactive wizard

You’ll be prompted for:

- **Timezone** (e.g., `Europe/London`)
- **Instance name** (default: `paperless`)
- **Data root** (default: `/home/docker/paperless`)
- **Stack dir** (default: `/home/docker/paperless-setup`)
- **Admin user / password** for Paperless‑ngx
- **Postgres password**
- **Enable Traefik with HTTPS?** (`yes`/`no`; `y` is accepted)
  - If **yes**: **Domain** and **Let’s Encrypt email**

The wizard writes:
- `.env` → `/home/docker/paperless-setup/.env`
- `docker-compose.yml` (Traefik on/off version)
- Helper script: `backup.py`
- Installs `bulletproof` CLI

Then it runs: `docker compose up -d` and performs a quick self-test

---

## Backup & snapshots

Automated cron jobs upload snapshots to pCloud:
- **Weekly full** backup at a scheduled time
- **Daily incremental** backups chaining to the last full
- Optional **monthly archive** snapshot kept separately
- Remote: `pcloud:backups/paperless/${INSTANCE_NAME}`
 - Snapshot naming: `YYYY-MM-DD_HH-MM-SS`
 - Full snapshots are self-contained; incrementals reference their parent
- Includes:
  - Encrypted `.env` (if enabled) or plain `.env`
  - `compose.snapshot.yml` (set `INCLUDE_COMPOSE_IN_BACKUP=no` to skip)
  - Tarballs of `media`, `data`, `export` (incremental)
  - Postgres SQL dump
  - Paperless-NGX version
  - `manifest.yaml` with versions, file sizes + SHA-256 checksums, host info, mode & parent
  - Integrity checks: archives are listed and the DB dump is test-restored; a `status.ok`/`status.fail` file records the result
 - Retention: keep last **N** full snapshots and **M** incrementals (`KEEP_FULLS`, `KEEP_INCS`)
  Pruning removes older full snapshots along with their incremental chains; for the latest full snapshot only the newest `KEEP_INCS` incrementals are retained.

 You can also trigger a backup manually (see **Bulletproof CLI**). Manual backups prompt for **Full** or **Incremental**.

During installation you're guided through choosing the full/incremental cadence
and whether to enable a monthly archive. Adjust these later with
`bulletproof schedule`.
Times may be entered as `HH:MM` or `HHMM` (e.g., `2330` for 23:30); invalid
values prompt again with an error.

---

## Restore

### From installer (early restore)
If snapshots exist, the installer can **restore first**:
- Select the latest or a specific snapshot
- Decrypt `.env` if needed (passphrase file or prompt)
- Restore data archives and DB, then start stack

### From the CLI
Use **Bulletproof** to pick a snapshot and restore it at any time.

A self-test runs after the stack is back up.

> Restores will stop the stack as needed and bring it back after import.

The restore process walks any incremental chain automatically, applies the
snapshot's `docker-compose.yml` by default (`USE_COMPOSE_SNAPSHOT=no` skips it)
and lets you choose between the snapshot's Paperless‑NGX version or the latest
image.

---

## Bulletproof CLI

`bulletproof` now manages **multiple** Paperless‑ngx instances. Running it with
no arguments launches an overview showing status and backup schedules.

From the menu you can:

- Back up one instance or all at once
- Add new instances from scratch or by cloning an existing snapshot (remote
  backups are listed so you don’t need to remember names)
- Explore backup folders, inspect snapshots, and verify their integrity before
  restoring
- Start or stop every instance, or wipe them all (remote backups remain)
- Drop into a per‑instance menu for upgrades, logs, scheduling, restore, rename,
  or delete

Manual backups still prompt for **Full** or **Incremental** when no mode is
provided.

---

## Troubleshooting

- **OAuth token fails**  
  Make sure you paste the **exact** JSON from `rclone authorize "pcloud"` using the **same rclone version** if possible. If the token keeps failing validation, try generating a fresh token or use WebDAV instead.

- **pCloud region detection**  
  The system automatically tests both pCloud regions (Global/US and Europe). If OAuth fails for both regions, your account might be in a different region or have specific restrictions. Try WebDAV (option 3) which works regardless of region.

- **WebDAV timeouts / 401**  
  Prefer **OAuth**. WebDAV endpoints can be region‑/network‑sensitive, but often work when OAuth fails due to network restrictions.

- **HTTPS not issuing**  
  Confirm DNS points to this host and ports 80/443 are reachable. Traefik will retry challenges.

- **Backup shows “No snapshots found”**
  Run `bulletproof`, choose your instance, then run a **Backup** followed by
  **Snapshots**. Verify the path shown matches
  `pcloud:backups/paperless/${INSTANCE_NAME}`. Check rclone with
  `rclone about pcloud:`.

- **Running without root**  
  Use `sudo` for the installer and for `bulletproof` if your Docker requires it.

---

## Uninstall / remove stack

```bash
cd /home/docker/paperless-setup
docker compose down -v     # stop and remove containers + volumes
# Remove data only if you really want to wipe everything:
# rm -rf /home/docker/paperless
```

Your off‑site snapshots remain in **pCloud**.

---

## Notes

- Installer is idempotent: safe to rerun to pick up fixes.
- You can change `.env` and run `docker compose up -d` anytime.
- The `bulletproof` CLI is a Python script; read it to see what it does.
