# Paperless‑ngx Bulletproof Installer

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

The installer pulls code from the `main` branch by default. Provide a branch
name or commit SHA with the `--branch` flag (or `BP_BRANCH` environment
variable) to test other versions. The installer uses this value for the
repository tarball and any preset files, so everything comes from the same
branch.

```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/install.py | sudo python3 -
```

### Fresh Ubuntu Installation

On a fresh Ubuntu 22.04/24.04 host, the installer will:

1. **Install prerequisites** (Docker, rclone, etc.)
2. **Guide you through pCloud setup** using OAuth token (recommended) or WebDAV
3. **Check for existing backups** in your pCloud storage
4. **Present options** based on what it finds:

**If remote backups are found:**
- **Restore all backups** - Automatically restores all instances from your latest backups
- **Install new instance** - Create a fresh instance alongside any existing backups  
- **Launch Bulletproof CLI** - Advanced management for selective restore/management
- **Quit**

**If no backups are found:**
- **Install new instance** - Start fresh with a new Paperless-ngx setup
- **Launch Bulletproof CLI** - Advanced management options
- **Quit**

### Subsequent Runs

If you run the installer again on a system where **bulletproof** CLI is already installed, it will:

1. **Update the CLI** to the latest version
2. **Verify pCloud connection** (prompts for setup if needed)  
3. **Present the same options** as a fresh install

This makes it easy to restore additional instances, create new ones, or manage existing setups without reinstalling everything.

### Dev branch example

```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/install.py \
  | BP_BRANCH=dev sudo -E python3 - --branch dev
```

The env var and flag ensure everything comes from `dev`.

### Installation Flow

When you choose **"Install new instance"**, the installer will:
1. Offer **presets** (Traefik + HTTPS or Direct HTTP)
2. Prompt for basics (timezone, instance name, paths, admin credentials, domain/email if using HTTPS)
3. Create the Docker Compose stack and start services
4. Install backup scripts and cron jobs
5. Perform a self-test to verify everything is working

> If you ever need to refresh the CLI manually:
> ```bash
> BRANCH=${BRANCH:-main}
> curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/$BRANCH/tools/bulletproof.py \
>   | sudo tee /usr/local/bin/bulletproof >/dev/null && sudo chmod +x /usr/local/bin/bulletproof
> ```

Use the same `BRANCH` value when refreshing the CLI so it matches the version
you're testing. After verifying your changes on a VPS, merge them into `main`
(or tag a release) and run the installer without specifying a branch for
production.

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
  Make sure you paste the **exact** JSON from `rclone authorize "pcloud"` using the **same rclone version** if possible.

- **EU vs Global**  
  The installer tests pCloud API endpoints and picks the right one automatically.

- **WebDAV timeouts / 401**  
  Prefer **OAuth**. WebDAV endpoints can be region‑/network‑sensitive.

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
