# Paperless‑ngx Bulletproof Installer

A one‑shot, “batteries‑included” setup for **Paperless‑ngx** on Ubuntu 22.04/24.04 with:
- Docker + Docker Compose
- Optional **Traefik** reverse proxy + Let’s Encrypt (HTTPS)
- **pCloud** off‑site backups via **rclone** (OAuth, region auto‑detect)
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
- `backup.py` and `restore.py` scripts placed into the stack dir
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

### Dev branch example

```bash
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/install.py \
  | BP_BRANCH=dev sudo -E python3 - --branch dev
```

The env var and flag ensure everything comes from `dev`.

The installer will:
1. Install/upgrade Docker, rclone, and prerequisites
2. Help you connect **pCloud** (OAuth recommended)
3. Offer **presets** (Traefik or Direct)
4. Prompt for a few basics (timezone, instance name, paths, credentials, HTTPS domain/email)
5. Start the stack
6. Install the **bulletproof** helper CLI

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
- Helper scripts: `backup.py`, `restore.py`
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

`bulletproof` now manages **multiple** Paperless‑ngx instances. Running it with no
arguments launches an instance overview showing status and backup schedules.

From the menu you can:

- Add, delete, or rename instances
- Back up a single instance or all at once
- Drop into a per‑instance menu for snapshots, restores, upgrades, logs, and
  scheduling

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
