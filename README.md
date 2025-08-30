# Paperless-ngx Bulletproof Wizard

Interactive one-file installer that can **install**, **backup**, and **restore** Paperless-ngx using **pCloud** (WebDAV).  
No secrets in the repo—wizard asks at runtime.

## Requirements
- Ubuntu **24.04** (or 22.04) minimal (no Plesk)
- Root shell (`sudo -i`)
- If using HTTPS: your domain’s DNS → server IP, ports **80/443** open
- pCloud **2FA disabled** (WebDAV doesn’t support 2FA)

---

## Quick start (fresh server)

```bash
# Become root
sudo -i

# Optional: firewall (if you use ufw)
apt update -y && apt install -y ufw
ufw allow OpenSSH
ufw allow 80,443
ufw enable

# Get and run the wizard
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless-ngx-wizard.sh -o setup.sh
chmod +x setup.sh
bash ./setup.sh
```

The wizard will:
- Install Docker + Compose, rclone, cron
- Ask for timezone, instance name, data/stack paths (defaults OK)
- Ask for admin + DB passwords (pre-filled with strong randoms)
- Ask if you want HTTPS via Traefik (domain + email) or plain port 8000
- Ask for **pCloud email + password** (input hidden)
- Check pCloud for snapshots → restore **latest** (or let you pick), or do fresh install
- Set up daily backups (03:30)

When it finishes it shows your URL:
- Traefik **on** → `https://<your-domain>`
- Traefik **off** → `http://<server-ip>:8000`

---

## Common tasks

```bash
# See running containers
docker ps

# Trigger a backup now
/home/docker/paperless-setup/backup_to_pcloud.sh

# View backups on pCloud (replace <instance> if you changed it)
rclone lsd pcloud:backups/paperless/<instance>

# Update Paperless-ngx (pull latest images and restart)
cd /home/docker/paperless-setup
docker compose pull
docker compose up -d
```

---

## Disaster recovery / migration

New VPS? Just run the wizard again:

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/obidose/paperless-ngx-bulletproof/main/paperless-ngx-wizard.sh -o setup.sh
chmod +x setup.sh
bash ./setup.sh
```

- Enter pCloud creds when asked.
- Choose **Restore latest** (or select a snapshot).
- Done—stack + data are back.

---

## Uninstall

```bash
cd /home/docker/paperless-setup
docker compose down
# data remains in /home/docker/paperless
```

Remove everything (IRREVERSIBLE):
```bash
rm -rf /home/docker/paperless /home/docker/paperless-setup
```

---

## Troubleshooting

**pCloud 401 Unauthorized**
- Ensure **2FA is OFF** in pCloud.
- The wizard auto-tries both `webdav.pcloud.com` and `ewebdav.pcloud.com`.

**HTTPS not issuing**
- DNS A/AAAA for your domain points to server IP.
- Ports **80/443** open (provider + ufw).

**Port 80/443 in use**
- Choose **no** for Traefik; you’ll get `http://<ip>:8000`.

**Where things live**
- Stack: `/home/docker/paperless-setup` (`docker-compose.yml`, `.env`, backups)
- Data:  `/home/docker/paperless`
- rclone: `/root/.config/rclone/rclone.conf` (password stored **obscured**)
