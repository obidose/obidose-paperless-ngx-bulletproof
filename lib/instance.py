#!/usr/bin/env python3
"""
Instance management for Paperless-NGX Bulletproof.

Provides Instance dataclass, InstanceManager, and config loading helpers.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lib.ui import Colors, colorize, say, ok, warn, error


# ─── Instance Data Class ──────────────────────────────────────────────────────

@dataclass
class Instance:
    """Represents a Paperless-NGX instance."""
    name: str
    stack_dir: Path
    data_root: Path
    created_at: str = ""
    labels: dict = field(default_factory=dict)

    def is_running(self) -> bool:
        """Check if the instance containers are running."""
        compose_file = self.stack_dir / "docker-compose.yml"
        if not compose_file.exists():
            return False
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "ps", "-q"],
            capture_output=True, text=True, check=False
        )
        return bool(result.stdout.strip())

    def get_env_value(self, key: str, default: str = "") -> str:
        """Get a value from the instance's .env file."""
        env_file = self.stack_dir / ".env"
        if not env_file.exists():
            return default
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
        return default

    def get_access_modes(self) -> list[str]:
        """Get the list of active access modes for this instance."""
        modes = []
        
        # Check Direct HTTP - always available if containers running
        port = self.get_env_value("HTTP_PORT", "8000")
        if port:
            modes.append("direct")
        
        # Check Traefik HTTPS
        enable_traefik = self.get_env_value("ENABLE_TRAEFIK", "no")
        if enable_traefik.lower() == "yes":
            # Verify Traefik container is actually running
            try:
                result = subprocess.run(
                    ["docker", "ps", "--filter", "name=traefik", "--format", "{{.Names}}"],
                    capture_output=True, text=True, check=False
                )
                if "traefik" in result.stdout:
                    modes.append("traefik")
            except Exception:
                pass
        
        # Check Cloudflare Tunnel
        enable_cloudflare = self.get_env_value("ENABLE_CLOUDFLARED", "no")
        if enable_cloudflare.lower() == "yes":
            # Verify cloudflared service is running
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", f"cloudflared-{self.name}"],
                    capture_output=True, text=True, check=False
                )
                if result.stdout.strip() == "active":
                    modes.append("cloudflare")
            except Exception:
                pass
        
        # Check Tailscale
        enable_tailscale = self.get_env_value("ENABLE_TAILSCALE", "no")
        if enable_tailscale.lower() == "yes":
            # Check if Tailscale is serving this instance's port
            try:
                result = subprocess.run(
                    ["tailscale", "serve", "status", "--json"],
                    capture_output=True, text=True, check=False
                )
                if result.returncode == 0 and port in result.stdout:
                    modes.append("tailscale")
            except Exception:
                pass
        
        return modes

    def get_access_mode(self) -> str:
        """Get the primary access mode description."""
        modes = self.get_access_modes()
        if not modes:
            return "Not configured"
        return ", ".join(modes)

    def get_access_urls(self) -> list[tuple[str, str]]:
        """Get list of (mode, url) tuples for all access methods."""
        urls = []
        port = self.get_env_value("HTTP_PORT", "8000")
        domain = self.get_env_value("DOMAIN", "")
        modes = self.get_access_modes()
        
        # Get local IP for direct access
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        except Exception:
            local_ip = "localhost"
        
        # Direct HTTP always available
        if "direct" in modes or not modes:
            urls.append(("Direct HTTP", f"http://{local_ip}:{port}"))
        
        # Traefik HTTPS
        if "traefik" in modes and domain:
            urls.append(("HTTPS (Traefik)", f"https://{domain}"))
        
        # Cloudflare Tunnel
        if "cloudflare" in modes and domain:
            urls.append(("Cloudflare Tunnel", f"https://{domain}"))
        
        # Tailscale
        if "tailscale" in modes:
            try:
                result = subprocess.run(
                    ["tailscale", "status", "--json"],
                    capture_output=True, text=True, check=False
                )
                if result.returncode == 0:
                    import json as json_mod
                    status = json_mod.loads(result.stdout)
                    ts_domain = status.get("Self", {}).get("DNSName", "").rstrip(".")
                    if ts_domain:
                        urls.append(("Tailscale", f"https://{ts_domain}"))
            except Exception:
                pass
        
        return urls

    def get_access_url(self) -> str:
        """Get the primary access URL."""
        urls = self.get_access_urls()
        if urls:
            return urls[0][1]
        port = self.get_env_value("HTTP_PORT", "8000")
        return f"http://localhost:{port}"


# ─── Config Loading Helpers ───────────────────────────────────────────────────

def load_instance_config(instance: Instance) -> None:
    """
    Load all settings from an instance's .env file into common.cfg.
    
    This centralizes config loading to ensure all settings are loaded consistently,
    especially for operations that regenerate config files.
    """
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import common
    
    # Core identity
    common.cfg.instance_name = instance.name
    common.cfg.stack_dir = str(instance.stack_dir)
    common.cfg.data_root = str(instance.data_root)
    
    # Networking
    common.cfg.http_port = instance.get_env_value("HTTP_PORT", "8000")
    common.cfg.domain = instance.get_env_value("DOMAIN", "")
    common.cfg.letsencrypt_email = instance.get_env_value("LETSENCRYPT_EMAIL", "")
    common.cfg.enable_traefik = instance.get_env_value("ENABLE_TRAEFIK", "no")
    common.cfg.enable_cloudflared = instance.get_env_value("ENABLE_CLOUDFLARED", "no")
    common.cfg.enable_tailscale = instance.get_env_value("ENABLE_TAILSCALE", "no")
    
    # Database
    common.cfg.postgres_db = instance.get_env_value("POSTGRES_DB", "paperless")
    common.cfg.postgres_user = instance.get_env_value("POSTGRES_USER", "paperless")
    common.cfg.postgres_password = instance.get_env_value("POSTGRES_PASSWORD", "")
    
    # Admin credentials
    common.cfg.paperless_admin_user = instance.get_env_value("PAPERLESS_ADMIN_USER", "admin")
    common.cfg.paperless_admin_password = instance.get_env_value("PAPERLESS_ADMIN_PASSWORD", "")
    
    # User/Group IDs
    common.cfg.puid = instance.get_env_value("PUID", "1000")
    common.cfg.pgid = instance.get_env_value("PGID", "1000")
    common.cfg.tz = instance.get_env_value("TZ", "UTC")
    
    # Rclone settings
    common.cfg.rclone_remote_name = instance.get_env_value("RCLONE_REMOTE_NAME", "pcloud")
    common.cfg.rclone_remote_path = instance.get_env_value("RCLONE_REMOTE_PATH", f"backups/paperless/{instance.name}")
    
    # Backup schedule
    common.cfg.cron_incr_time = instance.get_env_value("CRON_INCR_TIME", "0 */6 * * *")
    common.cfg.cron_full_time = instance.get_env_value("CRON_FULL_TIME", "30 3 * * 0")
    common.cfg.cron_archive_time = instance.get_env_value("CRON_ARCHIVE_TIME", "0 4 1 * *")
    common.cfg.retention_days = instance.get_env_value("RETENTION_DAYS", "30")
    common.cfg.retention_monthly_days = instance.get_env_value("RETENTION_MONTHLY_DAYS", "180")
    
    # Syncthing (consume input)
    common.cfg.consume_syncthing_enabled = instance.get_env_value("CONSUME_SYNCTHING_ENABLED", "false")
    common.cfg.consume_syncthing_folder_id = instance.get_env_value("CONSUME_SYNCTHING_FOLDER_ID", "")
    common.cfg.consume_syncthing_folder_label = instance.get_env_value("CONSUME_SYNCTHING_FOLDER_LABEL", "")
    common.cfg.consume_syncthing_device_id = instance.get_env_value("CONSUME_SYNCTHING_DEVICE_ID", "")
    common.cfg.consume_syncthing_api_key = instance.get_env_value("CONSUME_SYNCTHING_API_KEY", "")
    common.cfg.consume_syncthing_sync_port = instance.get_env_value("CONSUME_SYNCTHING_SYNC_PORT", "22000")
    common.cfg.consume_syncthing_gui_port = instance.get_env_value("CONSUME_SYNCTHING_GUI_PORT", "8384")
    
    # Samba (consume input)
    common.cfg.consume_samba_enabled = instance.get_env_value("CONSUME_SAMBA_ENABLED", "false")
    common.cfg.consume_samba_share_name = instance.get_env_value("CONSUME_SAMBA_SHARE_NAME", "")
    common.cfg.consume_samba_username = instance.get_env_value("CONSUME_SAMBA_USERNAME", "")
    common.cfg.consume_samba_password = instance.get_env_value("CONSUME_SAMBA_PASSWORD", "")
    
    # SFTP (consume input)
    common.cfg.consume_sftp_enabled = instance.get_env_value("CONSUME_SFTP_ENABLED", "false")
    common.cfg.consume_sftp_username = instance.get_env_value("CONSUME_SFTP_USERNAME", "")
    common.cfg.consume_sftp_password = instance.get_env_value("CONSUME_SFTP_PASSWORD", "")
    common.cfg.consume_sftp_port = instance.get_env_value("CONSUME_SFTP_PORT", "2222")
    
    # Refresh computed paths
    common.cfg.refresh_paths()


def load_backup_env_config(backup_env: dict) -> None:
    """
    Load settings from a backup's .env dict into common.cfg.
    
    Used when restoring from backup where we have parsed the .env contents
    into a dictionary rather than reading from an Instance object.
    """
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import common
    
    # Core settings (admin, postgres, etc.)
    common.cfg.tz = backup_env.get("TZ", common.cfg.tz)
    common.cfg.puid = backup_env.get("PUID", common.cfg.puid)
    common.cfg.pgid = backup_env.get("PGID", common.cfg.pgid)
    common.cfg.paperless_admin_user = backup_env.get("PAPERLESS_ADMIN_USER", "admin")
    common.cfg.paperless_admin_password = backup_env.get("PAPERLESS_ADMIN_PASSWORD", "")
    common.cfg.postgres_db = backup_env.get("POSTGRES_DB", "paperless")
    common.cfg.postgres_user = backup_env.get("POSTGRES_USER", "paperless")
    common.cfg.postgres_password = backup_env.get("POSTGRES_PASSWORD", "")
    
    # Networking
    common.cfg.http_port = backup_env.get("HTTP_PORT", "8000")
    common.cfg.domain = backup_env.get("DOMAIN", "")
    common.cfg.letsencrypt_email = backup_env.get("LETSENCRYPT_EMAIL", "")
    common.cfg.enable_traefik = backup_env.get("ENABLE_TRAEFIK", "no")
    common.cfg.enable_cloudflared = backup_env.get("ENABLE_CLOUDFLARED", "no")
    common.cfg.enable_tailscale = backup_env.get("ENABLE_TAILSCALE", "no")
    
    # Backup and retention
    common.cfg.retention_days = backup_env.get("RETENTION_DAYS", "30")
    common.cfg.retention_monthly_days = backup_env.get("RETENTION_MONTHLY_DAYS", "180")
    common.cfg.cron_incr_time = backup_env.get("CRON_INCR_TIME", "0 */6 * * *")
    common.cfg.cron_full_time = backup_env.get("CRON_FULL_TIME", "30 3 * * 0")
    common.cfg.cron_archive_time = backup_env.get("CRON_ARCHIVE_TIME", "0 4 1 * *")
    
    # Syncthing
    common.cfg.consume_syncthing_enabled = backup_env.get("CONSUME_SYNCTHING_ENABLED", "false")
    common.cfg.consume_syncthing_folder_id = backup_env.get("CONSUME_SYNCTHING_FOLDER_ID", "")
    common.cfg.consume_syncthing_folder_label = backup_env.get("CONSUME_SYNCTHING_FOLDER_LABEL", "")
    common.cfg.consume_syncthing_device_id = backup_env.get("CONSUME_SYNCTHING_DEVICE_ID", "")
    common.cfg.consume_syncthing_api_key = backup_env.get("CONSUME_SYNCTHING_API_KEY", "")
    common.cfg.consume_syncthing_sync_port = backup_env.get("CONSUME_SYNCTHING_SYNC_PORT", "22000")
    common.cfg.consume_syncthing_gui_port = backup_env.get("CONSUME_SYNCTHING_GUI_PORT", "8384")
    
    # Samba
    common.cfg.consume_samba_enabled = backup_env.get("CONSUME_SAMBA_ENABLED", "false")
    common.cfg.consume_samba_share_name = backup_env.get("CONSUME_SAMBA_SHARE_NAME", "")
    common.cfg.consume_samba_username = backup_env.get("CONSUME_SAMBA_USERNAME", "")
    common.cfg.consume_samba_password = backup_env.get("CONSUME_SAMBA_PASSWORD", "")
    
    # SFTP
    common.cfg.consume_sftp_enabled = backup_env.get("CONSUME_SFTP_ENABLED", "false")
    common.cfg.consume_sftp_username = backup_env.get("CONSUME_SFTP_USERNAME", "")
    common.cfg.consume_sftp_password = backup_env.get("CONSUME_SFTP_PASSWORD", "")
    common.cfg.consume_sftp_port = backup_env.get("CONSUME_SFTP_PORT", "2222")


# ─── Instance Manager ─────────────────────────────────────────────────────────

class InstanceManager:
    """Manages multiple Paperless-NGX instances."""
    
    def __init__(self, config_dir: Path = Path("/etc/paperless-bulletproof")):
        self.config_dir = config_dir
        self.config_file = config_dir / "instances.json"
        self.instances: dict[str, Instance] = {}
        self.load_instances()

    def load_instances(self) -> None:
        """Load instances from config file and scan for orphans."""
        # Load registered instances
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text())
                for name, info in data.get("instances", {}).items():
                    stack_dir = Path(info.get("stack_dir", f"/home/docker/{name}-setup"))
                    data_root = Path(info.get("data_root", f"/home/docker/{name}"))
                    
                    # Only add if the instance actually exists
                    if stack_dir.exists() or data_root.exists():
                        self.instances[name] = Instance(
                            name=name,
                            stack_dir=stack_dir,
                            data_root=data_root,
                            created_at=info.get("created_at", ""),
                            labels=info.get("labels", {})
                        )
            except (json.JSONDecodeError, KeyError) as e:
                warn(f"Error loading instances config: {e}")
        
        # Scan for orphan instances (exist on disk but not in config)
        docker_home = Path("/home/docker")
        if docker_home.exists():
            for setup_dir in docker_home.glob("*-setup"):
                # Check if it's a valid Paperless setup directory
                if (setup_dir / "docker-compose.yml").exists() or (setup_dir / ".env").exists():
                    name = setup_dir.name.replace("-setup", "")
                    if name not in self.instances:
                        # Found an orphan - add it
                        data_root = docker_home / name
                        self.instances[name] = Instance(
                            name=name,
                            stack_dir=setup_dir,
                            data_root=data_root,
                            created_at="",
                            labels={"orphan": "true"}
                        )
                        warn(f"Found unregistered instance: {name}")
        
        # Save to ensure orphans are now registered
        if self.instances:
            self.save_instances()

    def save_instances(self) -> None:
        """Save instances to config file."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "instances": {
                name: {
                    "stack_dir": str(inst.stack_dir),
                    "data_root": str(inst.data_root),
                    "created_at": inst.created_at,
                    "labels": inst.labels
                }
                for name, inst in self.instances.items()
            }
        }
        self.config_file.write_text(json.dumps(data, indent=2))

    def add_instance(self, name: str, stack_dir: Path, data_root: Path) -> Instance:
        """Add a new instance to the manager."""
        from datetime import datetime, timezone
        instance = Instance(
            name=name,
            stack_dir=stack_dir,
            data_root=data_root,
            created_at=datetime.now(timezone.utc).isoformat()
        )
        self.instances[name] = instance
        self.save_instances()
        return instance

    def remove_instance(self, name: str, delete_files: bool = True) -> None:
        """Remove an instance from the manager."""
        if name not in self.instances:
            warn(f"Instance '{name}' not found")
            return
        
        instance = self.instances[name]
        
        if delete_files:
            # Stop containers first
            compose_file = instance.stack_dir / "docker-compose.yml"
            if compose_file.exists():
                say(f"Stopping containers for {name}...")
                subprocess.run(
                    ["docker", "compose", "-f", str(compose_file), "down", "-v"],
                    capture_output=True, check=False
                )
            
            # Remove cloudflared service if it exists
            service_file = Path(f"/etc/systemd/system/cloudflared-{name}.service")
            if service_file.exists():
                say(f"Removing cloudflared service for {name}...")
                subprocess.run(["systemctl", "stop", f"cloudflared-{name}"], capture_output=True, check=False)
                subprocess.run(["systemctl", "disable", f"cloudflared-{name}"], capture_output=True, check=False)
                service_file.unlink()
                subprocess.run(["systemctl", "daemon-reload"], capture_output=True, check=False)
            
            # Remove Tailscale serve if configured
            try:
                port = instance.get_env_value("HTTP_PORT", "8000")
                subprocess.run(
                    ["tailscale", "serve", "off", f":{port}"],
                    capture_output=True, check=False
                )
            except Exception:
                pass
            
            # Remove cron jobs
            try:
                result = subprocess.run(
                    ["crontab", "-l"],
                    capture_output=True, text=True, check=False
                )
                if result.returncode == 0:
                    lines = result.stdout.splitlines()
                    new_lines = [l for l in lines if name not in l]
                    if len(new_lines) != len(lines):
                        subprocess.run(
                            ["crontab", "-"],
                            input="\n".join(new_lines) + "\n",
                            text=True, check=False
                        )
            except Exception:
                pass
            
            # Delete directories
            if instance.stack_dir.exists():
                say(f"Removing {instance.stack_dir}...")
                shutil.rmtree(instance.stack_dir)
            
            if instance.data_root.exists():
                say(f"Removing {instance.data_root}...")
                shutil.rmtree(instance.data_root)
        
        # Remove from registry
        del self.instances[name]
        self.save_instances()
        ok(f"Instance '{name}' removed")

    def get_instance(self, name: str) -> Optional[Instance]:
        """Get an instance by name."""
        return self.instances.get(name)

    def list_instances(self) -> list[Instance]:
        """List all instances."""
        return list(self.instances.values())

    def get_instance_names(self) -> list[str]:
        """Get list of all instance names."""
        return list(self.instances.keys())
