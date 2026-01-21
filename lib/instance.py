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

    @property
    def env_file(self) -> Path:
        """Path to the instance's .env file."""
        return self.stack_dir / ".env"

    @property
    def compose_file(self) -> Path:
        """Path to the instance's docker-compose.yml file."""
        return self.stack_dir / "docker-compose.yml"

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
        """Get list of (mode, url) tuples for all access methods.
        
        URLs are returned in priority order:
        1. Cloudflare Tunnel (most secure, external access)
        2. Traefik HTTPS (secure, requires port 443)
        3. Tailscale (secure, private network)
        4. Direct HTTP (fallback)
        """
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
        
        # Priority 1: Cloudflare Tunnel (secure tunnel, no exposed ports)
        if "cloudflare" in modes and domain:
            urls.append(("Cloudflare Tunnel", f"https://{domain}"))
        
        # Priority 2: Traefik HTTPS (SSL termination)
        if "traefik" in modes and domain:
            urls.append(("HTTPS (Traefik)", f"https://{domain}"))
        
        # Priority 3: Tailscale (private network)
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
                        urls.append(("Tailscale", f"https://{ts_domain}:{port}"))
            except Exception:
                pass
        
        # Priority 4: Direct HTTP (always available as fallback)
        urls.append(("Direct HTTP", f"http://{local_ip}:{port}"))
        
        return urls

    def get_access_url(self) -> str:
        """Get the primary access URL."""
        urls = self.get_access_urls()
        if urls:
            return urls[0][1]
        port = self.get_env_value("HTTP_PORT", "8000")
        return f"http://localhost:{port}"

    def get_access_url_display(self) -> str:
        """Get the primary access URL with emoji and mode label for display.
        
        Returns a rich string like: ☁️  Cloudflare: https://docs.example.com
        """
        urls = self.get_access_urls()
        if not urls:
            port = self.get_env_value("HTTP_PORT", "8000")
            return f"🌐 Direct: http://localhost:{port}"
        
        mode, url = urls[0]
        return f"{self._mode_to_emoji(mode)} {self._mode_to_label(mode)}: {url}"

    @staticmethod
    def _mode_to_emoji(mode: str) -> str:
        """Get emoji for access mode."""
        if "Cloudflare" in mode:
            return "☁️ "
        elif "Traefik" in mode:
            return "🛡️"
        elif "Tailscale" in mode:
            return "🔐"
        else:
            return "🌐"

    @staticmethod
    def _mode_to_label(mode: str) -> str:
        """Get short label for access mode."""
        if "Cloudflare" in mode:
            return "Cloudflare"
        elif "Traefik" in mode:
            return "Traefik"
        elif "Tailscale" in mode:
            return "Tailscale"
        else:
            return "Direct"

    def get_access_urls_formatted(self) -> list[tuple[str, str]]:
        """Get list of (formatted_label, url) tuples with emojis for all access methods."""
        urls = self.get_access_urls()
        return [(f"{self._mode_to_emoji(mode)} {self._mode_to_label(mode)}", url) for mode, url in urls]


# ─── Port Utilities (Canonical Implementation) ───────────────────────────────
# All port checking should use these functions - do not duplicate elsewhere!

def is_port_available(port: int, check_existing_instances: bool = False) -> bool:
    """Check if a TCP port is available for binding.
    
    This is the canonical port availability check. Use this instead of 
    duplicating socket.bind() checks elsewhere.
    
    Args:
        port: Port number to check
        check_existing_instances: If True, also check ports used by existing instances
    """
    import socket
    
    # First check existing instances if requested
    if check_existing_instances:
        from pathlib import Path
        instances_base = Path("/home/docker")
        if instances_base.exists():
            for setup_dir in instances_base.glob("*-setup"):
                env_file = setup_dir / ".env"
                if env_file.exists():
                    try:
                        for line in env_file.read_text().splitlines():
                            for key in ("HTTP_PORT=", "CONSUME_SYNCTHING_GUI_PORT=", 
                                       "CONSUME_SYNCTHING_SYNC_PORT=", "CONSUME_SFTP_PORT="):
                                if line.startswith(key):
                                    port_val = line.split("=", 1)[1].strip()
                                    if port_val.isdigit() and int(port_val) == port:
                                        return False
                    except Exception:
                        pass
    
    # Then check OS-level availability
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', port))
            return True
    except OSError:
        return False


def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use. Inverse of is_port_available()."""
    return not is_port_available(port)


def find_available_port(
    start_port: int, 
    max_tries: int = 100,
    used_ports: list[int] | None = None,
    check_existing_instances: bool = False
) -> int:
    """Find an available port starting from start_port.
    
    Args:
        start_port: Port number to start searching from
        max_tries: Maximum number of ports to try
        used_ports: Optional list of ports to skip (already allocated)
        check_existing_instances: If True, also check ports used by existing instances
        
    Returns:
        Available port number, or start_port as fallback
    """
    # Convert to set for efficient lookups
    ports_to_skip: set[int] = set()
    if used_ports is not None:
        ports_to_skip = set(used_ports)
    
    # Optionally gather ports from existing instances
    if check_existing_instances:
        from pathlib import Path
        instances_base = Path("/home/docker")
        if instances_base.exists():
            for setup_dir in instances_base.glob("*-setup"):
                env_file = setup_dir / ".env"
                if env_file.exists():
                    try:
                        for line in env_file.read_text().splitlines():
                            for key in ("HTTP_PORT=", "CONSUME_SYNCTHING_GUI_PORT=", 
                                       "CONSUME_SYNCTHING_SYNC_PORT=", "CONSUME_SFTP_PORT="):
                                if line.startswith(key):
                                    port_val = line.split("=", 1)[1].strip()
                                    if port_val.isdigit():
                                        ports_to_skip.add(int(port_val))
                    except Exception:
                        pass
    
    for port in range(start_port, start_port + max_tries):
        if port in ports_to_skip:
            continue
        if is_port_available(port):
            return port
    return start_port  # Fallback


def get_next_available_port(start_port: int = 8000, as_string: bool = False) -> int | str:
    """Find the next available port, checking both OS and existing instances.
    
    This is a convenience wrapper that always checks existing instances.
    For simple OS-level checks, use find_available_port() directly.
    
    Args:
        start_port: Port number to start searching from
        as_string: If True, return as string (for backward compatibility)
        
    Returns:
        Available port as int or str depending on as_string parameter
    """
    port = find_available_port(start_port, check_existing_instances=True)
    return str(port) if as_string else port


def get_local_ip() -> str:
    """Get the local IP address of this machine.
    
    Uses a UDP socket trick to determine the local IP that would be used
    to reach external hosts, without actually sending any traffic.
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Doesn't actually connect, just determines routing
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def check_port_conflicts_and_fix(config_dict: dict, warn_func=warn, say_func=say) -> dict:
    """
    Check a config dictionary for port conflicts and fix them.
    
    Args:
        config_dict: Dictionary with port settings (HTTP_PORT, CONSUME_SYNCTHING_GUI_PORT, etc.)
        warn_func: Function to call for warnings
        say_func: Function to call for info messages
        
    Returns:
        Updated config dictionary with non-conflicting ports
    """
    # Check HTTP port
    http_port = int(config_dict.get("HTTP_PORT", "8000"))
    if not is_port_available(http_port):
        new_port = find_available_port(8000)
        warn_func(f"HTTP port {http_port} in use, using {new_port}")
        config_dict["HTTP_PORT"] = str(new_port)
    
    # Check Syncthing GUI port
    st_gui_port = int(config_dict.get("CONSUME_SYNCTHING_GUI_PORT", "8384"))
    if not is_port_available(st_gui_port):
        new_port = find_available_port(8384)
        warn_func(f"Syncthing GUI port {st_gui_port} in use, using {new_port}")
        config_dict["CONSUME_SYNCTHING_GUI_PORT"] = str(new_port)
    
    # Check Syncthing sync port
    st_sync_port = int(config_dict.get("CONSUME_SYNCTHING_SYNC_PORT", "22000"))
    if not is_port_available(st_sync_port):
        new_port = find_available_port(22000)
        warn_func(f"Syncthing sync port {st_sync_port} in use, using {new_port}")
        config_dict["CONSUME_SYNCTHING_SYNC_PORT"] = str(new_port)
    
    # Check SFTP port
    sftp_port = int(config_dict.get("CONSUME_SFTP_PORT", "2222"))
    if not is_port_available(sftp_port):
        new_port = find_available_port(2222)
        warn_func(f"SFTP port {sftp_port} in use, using {new_port}")
        config_dict["CONSUME_SFTP_PORT"] = str(new_port)
    
    return config_dict


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
    
    # Samba (consume input) - per-instance container
    common.cfg.consume_samba_enabled = instance.get_env_value("CONSUME_SAMBA_ENABLED", "false")
    common.cfg.consume_samba_share_name = instance.get_env_value("CONSUME_SAMBA_SHARE_NAME", "")
    common.cfg.consume_samba_username = instance.get_env_value("CONSUME_SAMBA_USERNAME", "")
    common.cfg.consume_samba_password = instance.get_env_value("CONSUME_SAMBA_PASSWORD", "")
    common.cfg.consume_samba_port = instance.get_env_value("CONSUME_SAMBA_PORT", "445")
    
    # SFTP (consume input)
    common.cfg.consume_sftp_enabled = instance.get_env_value("CONSUME_SFTP_ENABLED", "false")
    common.cfg.consume_sftp_username = instance.get_env_value("CONSUME_SFTP_USERNAME", "")
    common.cfg.consume_sftp_password = instance.get_env_value("CONSUME_SFTP_PASSWORD", "")
    common.cfg.consume_sftp_port = instance.get_env_value("CONSUME_SFTP_PORT", "2222")
    
    # Refresh computed paths
    common.cfg.refresh_paths()


def load_backup_env_config(backup_env: dict, check_port_conflicts: bool = True, skip_consume_folders: bool = False) -> None:
    """
    Load settings from a backup's .env dict into common.cfg.
    
    Used when restoring from backup where we have parsed the .env contents
    into a dictionary rather than reading from an Instance object.
    
    Args:
        backup_env: Dictionary of environment variables from backup
        check_port_conflicts: If True, check and fix port conflicts
        skip_consume_folders: If True, disable consume folders (for clones)
    """
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import common
    
    # Check and fix port conflicts before loading
    if check_port_conflicts:
        backup_env = check_port_conflicts_and_fix(backup_env.copy())
    
    # Core settings (admin, postgres, etc.)
    common.cfg.tz = backup_env.get("TZ", common.cfg.tz)
    common.cfg.puid = backup_env.get("PUID", common.cfg.puid)
    common.cfg.pgid = backup_env.get("PGID", common.cfg.pgid)
    common.cfg.paperless_admin_user = backup_env.get("PAPERLESS_ADMIN_USER", "admin")
    common.cfg.paperless_admin_password = backup_env.get("PAPERLESS_ADMIN_PASSWORD", "")
    common.cfg.postgres_db = backup_env.get("POSTGRES_DB", "paperless")
    common.cfg.postgres_user = backup_env.get("POSTGRES_USER", "paperless")
    common.cfg.postgres_password = backup_env.get("POSTGRES_PASSWORD", "")
    
    # Networking - use potentially updated port
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
    
    # Consume folder services - skip for clones (they need fresh setup)
    if skip_consume_folders:
        # Disable all consume folder services for clones
        common.cfg.consume_syncthing_enabled = "false"
        common.cfg.consume_syncthing_folder_id = ""
        common.cfg.consume_syncthing_folder_label = ""
        common.cfg.consume_syncthing_device_id = ""
        common.cfg.consume_syncthing_api_key = ""
        common.cfg.consume_syncthing_sync_port = "22000"
        common.cfg.consume_syncthing_gui_port = "8384"
        common.cfg.consume_samba_enabled = "false"
        common.cfg.consume_samba_share_name = ""
        common.cfg.consume_samba_username = ""
        common.cfg.consume_samba_password = ""
        common.cfg.consume_samba_port = "445"
        common.cfg.consume_sftp_enabled = "false"
        common.cfg.consume_sftp_username = ""
        common.cfg.consume_sftp_password = ""
        common.cfg.consume_sftp_port = "2222"
    else:
        # Syncthing - use potentially updated ports
        common.cfg.consume_syncthing_enabled = backup_env.get("CONSUME_SYNCTHING_ENABLED", "false")
        common.cfg.consume_syncthing_folder_id = backup_env.get("CONSUME_SYNCTHING_FOLDER_ID", "")
        common.cfg.consume_syncthing_folder_label = backup_env.get("CONSUME_SYNCTHING_FOLDER_LABEL", "")
        common.cfg.consume_syncthing_device_id = backup_env.get("CONSUME_SYNCTHING_DEVICE_ID", "")
        common.cfg.consume_syncthing_api_key = backup_env.get("CONSUME_SYNCTHING_API_KEY", "")
        common.cfg.consume_syncthing_sync_port = backup_env.get("CONSUME_SYNCTHING_SYNC_PORT", "22000")
        common.cfg.consume_syncthing_gui_port = backup_env.get("CONSUME_SYNCTHING_GUI_PORT", "8384")
        
        # Samba - per-instance container
        common.cfg.consume_samba_enabled = backup_env.get("CONSUME_SAMBA_ENABLED", "false")
        common.cfg.consume_samba_share_name = backup_env.get("CONSUME_SAMBA_SHARE_NAME", "")
        common.cfg.consume_samba_username = backup_env.get("CONSUME_SAMBA_USERNAME", "")
        common.cfg.consume_samba_password = backup_env.get("CONSUME_SAMBA_PASSWORD", "")
        common.cfg.consume_samba_port = backup_env.get("CONSUME_SAMBA_PORT", "445")
        
        # SFTP - use potentially updated port
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
            
            # Clean up consume services (Syncthing, Samba, SFTP)
            self._cleanup_consume_services(instance)
            
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
    
    def _cleanup_consume_services(self, instance: 'Instance') -> None:
        """Clean up consume services (Syncthing, Samba, SFTP) for an instance."""
        try:
            from lib.installer.consume import (
                load_consume_config, stop_syncthing_container,
                stop_samba, remove_sftp_user
            )
            
            config = load_consume_config(instance.env_file)
            
            # Stop and remove Syncthing container
            if config.syncthing.enabled:
                say(f"Removing Syncthing container for {instance.name}...")
                stop_syncthing_container(instance.name)
            
            # Stop and remove per-instance Samba container
            if config.samba.enabled:
                say(f"Removing Samba container for {instance.name}...")
                stop_samba(instance.name)
            
            # Remove SFTP user
            if config.sftp.enabled:
                say(f"Removing SFTP user for {instance.name}...")
                remove_sftp_user(config.sftp.username)
                
        except Exception as e:
            warn(f"Could not fully clean up consume services: {e}")

    def get_instance(self, name: str) -> Optional[Instance]:
        """Get an instance by name."""
        return self.instances.get(name)

    def list_instances(self) -> list[Instance]:
        """List all instances."""
        return list(self.instances.values())

    def get_instance_names(self) -> list[str]:
        """Get list of all instance names."""
        return list(self.instances.keys())
