# Paperless-ngx • Bulletproof (One-liner wizard + backup/restore)

**Fresh install or disaster recovery in minutes.**  
Single command spins up Paperless-ngx (Postgres, Redis, Tika, Gotenberg) with HTTPS (Traefik) or simple port.  
Includes nightly backups to **pCloud via rclone** and a `bulletproof` menu for backups/restores.

---

## TL;DR (fresh server)

Run these on a brand-new Ubuntu 22.04/24.04 server (as root):

```bash
sudo -i
bash -c "$(curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/install.sh)"
```

The wizard will:

- Install Docker + Compose, rclone, cron
- **First** connect to pCloud (EU→Global WebDAV)
- If backups exist, offer an **early restore** path
- Otherwise proceed to presets + minimal prompts
- Bring the stack up and install the `bulletproof` menu

---

## Backups

- Daily cron runs `ops/backup_to_pcloud.sh`
- Full snapshots: `media.tar.gz`, `data.tar.gz`, `export.tar.gz`, `postgres.sql`
- Manifest: `manifest.json`
- **Optional** `.env` snapshot:
  - `ENV_BACKUP_MODE = none | plain | openssl`
  - `openssl` uses AES-256-CBC with `-pbkdf2`, passphrase file at `ENV_BACKUP_PASSPHRASE_FILE` (default `/root/.paperless_env_pass`)
- **Optional** `compose.snapshot.yml` included
- Snapshots live at: `pcloud:backups/paperless/<instance>/<YYYY-MM-DD_HHMMSS>/`

---

## Restore

Just run the installer again on a new server — it logs into pCloud first,
finds snapshots, and offers to restore **before any prompts**. If the snapshot contains `.env` and `compose.snapshot.yml`, it will rebuild exactly.

You can also use the menu:

```bash
bulletproof
```

---

## Presets & customization

- `env/.env.example` documents all variables
- `presets/traefik.env` and `presets/direct.env` provide common defaults
- At install, you may load:
  - repo preset (traefik/direct)
  - a custom URL or local `.env`
- The wizard only asks for missing/placeholder values

---

## Repository Layout

```
.
├── install.sh
├── bulletproof.sh
├── compose/
│   ├── docker-compose-traefik.yml
│   └── docker-compose-direct.yml
├── env/
│   └── .env.example
├── presets/
│   ├── traefik.env
│   └── direct.env
├── modules/
│   ├── common.sh
│   ├── deps.sh
│   ├── pcloud.sh
│   └── files.sh
├── ops/
│   └── backup_to_pcloud.sh
└── README.md
```

---

## Requirements

- Ubuntu 22.04 / 24.04
- Root (or sudo)
- DNS A/AAAA records pointed to your server if using Traefik (open 80/443)

---

## Security notes

- No secrets live in Git.
- For backups, choose `.env` mode:
  - `none`: simplest; manual re-entry on DR
  - `plain`: fastest rebuild; keep remote private
  - `openssl`: safer; **don’t lose the passphrase** file
- Tip: store `/root/.paperless_env_pass` in a secure password manager.

---

## Updating

Use the menu:

```bash
bulletproof  # option 7: Update stack (pull + up -d)
```

Or manually:

```bash
cd /home/docker/paperless-setup
docker compose pull
docker compose up -d
```

---

Happy filing! 📄✨
