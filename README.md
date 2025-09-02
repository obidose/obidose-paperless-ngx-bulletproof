# Paperless‑ngx Bulletproof Installer

A one‑shot, “batteries‑included” setup for **Paperless‑ngx** on Ubuntu 24.04 (may work others but untested) with:
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
- `backup.sh` and `restore.sh` scripts placed into the stack dir
-  Cron job for nightly snapshots with retention
- `bulletproof` command for backups, safe upgrades, listing snapshots, restores, health, and logs

---

## Requirements

- Ubuntu **22.04** or **24.04**
- Run as **root** (or prefix commands with `sudo`)
- A pCloud account  
  - OAuth is used (no app password required)

> DNS should already point to your host **if** you choose Traefik + HTTPS, so Let’s Encrypt can issue certs.

---

## Quick start

```bash
# Run as root (or sudo)
bash -c "$(curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/install.sh)"
```

The installer will:
1. Install/upgrade Docker, rclone, and prerequisites
2. Help you connect **pCloud** (OAuth recommended)
3. Offer **presets** (Traefik or Direct)
4. Prompt for a few basics (timezone, instance name, paths, credentials, HTTPS domain/email)
5. Start the stack
6. Install the **bulletproof** helper CLI

> If you ever need to refresh the CLI manually:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/tools/bulletproof.sh \
>   -o /usr/local/bin/bulletproof && chmod +x /usr/local/bin/bulletproof
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
- Helper scripts: `backup.sh`, `restore.sh`
- Installs `bulletproof` CLI

Then it runs: `docker compose up -d`

---

## Backup & snapshots

Nightly cron (configurable) runs `backup.sh auto` and uploads incrementals to pCloud:
- Remote: `pcloud:backups/paperless/${INSTANCE_NAME}`
- Snapshot naming: `YYYYMMDD-HHMMSS`
- Monthly snapshots are **full**; weekly/daily contain only changed files and point to their parent in `manifest.yaml`
- Includes:
  - Encrypted `.env` (if enabled) or plain `.env`
  - `compose.snapshot.yml` (set `INCLUDE_COMPOSE_IN_BACKUP=no` to skip)
  - Tarballs of `media`, `data`, `export` (incremental)
  - Postgres SQL dump
  - Paperless-NGX version
  - `manifest.yaml` with versions, file sizes + SHA-256 checksums, host info, retention class, mode & parent
- Retention: keep last **N** days (configurable) and tag snapshots as **daily**, **weekly**, or **monthly** (auto by date)

You can also trigger a backup manually (see **Bulletproof CLI**).

---

## Restore

### From installer (early restore)
If snapshots exist, the installer can **restore first**:
- Select the latest or a specific snapshot
- Decrypt `.env` if needed (passphrase file or prompt)
- Restore data archives and DB, then start stack

### From the CLI
Use **Bulletproof** to pick a snapshot and restore it at any time.

> Restores will stop the stack as needed and bring it back after import.

The restore process walks any incremental chain automatically, applies the
snapshot's `docker-compose.yml` by default (`USE_COMPOSE_SNAPSHOT=no` skips it)
and lets you choose between the snapshot's Paperless‑NGX version or the latest
image.

---

## Bulletproof CLI

A tiny helper wrapped around the installed scripts.

```bash
bulletproof          # interactive menu
bulletproof backup [class]   # run a snapshot now (daily|weekly|monthly|auto|full)
bulletproof list     # list snapshot folders on pCloud
bulletproof manifest # show manifest for a snapshot
bulletproof restore  # guided restore (choose snapshot)
bulletproof upgrade  # backup + pull images + up -d with rollback
bulletproof status   # container & health overview
bulletproof logs     # tail paperless logs
bulletproof doctor   # quick checks (disk, rclone, DNS/HTTP)
```

**Upgrade** runs a backup, pulls new images, restarts the stack, and rolls back automatically if the health check fails.

**Status** shows:
- `docker compose ps` (state/ports)
- `docker stats --no-stream` (CPU/MEM)
- `df -h` for disk
- rclone remote health

**Doctor** runs common checks and prints actionable tips.

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
  Run `bulletproof backup` then `bulletproof list`. Verify the path shown matches
  `pcloud:backups/paperless/${INSTANCE_NAME}`. Check rclone with `rclone about pcloud:`.

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
- The `bulletproof` CLI is just a shell script; read it to see what it does.
