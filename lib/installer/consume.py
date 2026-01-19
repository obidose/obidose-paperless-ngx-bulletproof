"""
Consume Folder Management - Multi-method document input for Paperless-NGX.

Supports three independent input methods:
1. Syncthing (per-instance, peer-to-peer sync)
2. Samba (Tailscale + SMB file shares)
3. SFTP (Tailscale + SSH file transfer)

Each method can be enabled independently per instance.
Security model:
- Syncthing: Complete isolation via per-instance containers
- Samba/SFTP: Tailscale network + per-share credentials
"""
import json
import os
import secrets
import string
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .common import say, ok, warn, error, randpass


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class SyncthingConfig:
    """Syncthing configuration for an instance."""
    enabled: bool = False
    folder_id: str = ""
    folder_label: str = ""
    device_id: str = ""
    api_key: str = ""
    web_ui_port: int = 8384
    sync_port: int = 22000
    
    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "folder_id": self.folder_id,
            "folder_label": self.folder_label,
            "device_id": self.device_id,
            "api_key": self.api_key,
            "web_ui_port": self.web_ui_port,
            "sync_port": self.sync_port,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SyncthingConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SambaConfig:
    """Samba configuration for an instance."""
    enabled: bool = False
    share_name: str = ""
    username: str = ""
    password: str = ""
    
    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "share_name": self.share_name,
            "username": self.username,
            "password": self.password,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SambaConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SFTPConfig:
    """SFTP configuration for an instance."""
    enabled: bool = False
    username: str = ""
    password: str = ""
    port: int = 2222
    
    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "username": self.username,
            "password": self.password,
            "port": self.port,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SFTPConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ConsumeConfig:
    """Complete consume folder configuration for an instance."""
    syncthing: SyncthingConfig = field(default_factory=SyncthingConfig)
    samba: SambaConfig = field(default_factory=SambaConfig)
    sftp: SFTPConfig = field(default_factory=SFTPConfig)
    
    def to_dict(self) -> dict:
        return {
            "syncthing": self.syncthing.to_dict(),
            "samba": self.samba.to_dict(),
            "sftp": self.sftp.to_dict(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ConsumeConfig':
        return cls(
            syncthing=SyncthingConfig.from_dict(data.get("syncthing", {})),
            samba=SambaConfig.from_dict(data.get("samba", {})),
            sftp=SFTPConfig.from_dict(data.get("sftp", {})),
        )
    
    def has_any_enabled(self) -> bool:
        return self.syncthing.enabled or self.samba.enabled or self.sftp.enabled
    
    def enabled_methods(self) -> list[str]:
        methods = []
        if self.syncthing.enabled:
            methods.append("syncthing")
        if self.samba.enabled:
            methods.append("samba")
        if self.sftp.enabled:
            methods.append("sftp")
        return methods


# ─── Helper Functions ─────────────────────────────────────────────────────────

def generate_secure_password(length: int = 16) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_folder_id() -> str:
    """Generate a unique Syncthing folder ID."""
    return str(uuid.uuid4())[:8] + "-" + str(uuid.uuid4())[:4]


def get_next_available_port(start_port: int, used_ports: Optional[list[int]] = None) -> int:
    """Find the next available port starting from start_port."""
    import socket
    if used_ports is None:
        used_ports = []
    
    port = start_port
    while port < 65535:
        if port in used_ports:
            port += 1
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                port += 1
    return start_port


# ─── Syncthing Management ─────────────────────────────────────────────────────

def is_syncthing_available() -> bool:
    """Check if Syncthing can be used (Docker available)."""
    try:
        result = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            check=False,
            timeout=5
        )
        return result.returncode == 0
    except:
        return False


def get_syncthing_device_id(instance_name: str) -> Optional[str]:
    """Get the device ID of a running Syncthing container."""
    container_name = f"syncthing-{instance_name}"
    try:
        result = subprocess.run(
            ["docker", "exec", container_name, "syncthing", "--device-id"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass
    return None


def get_syncthing_api_key(config_dir: Path) -> Optional[str]:
    """Get the API key from Syncthing's config.xml."""
    config_file = config_dir / "config.xml"
    if config_file.exists():
        try:
            content = config_file.read_text()
            # Simple regex extraction for API key
            import re
            match = re.search(r'<apikey>([^<]+)</apikey>', content)
            if match:
                return match.group(1)
        except:
            pass
    return None


def configure_syncthing_auto_accept(instance_name: str, config: SyncthingConfig, 
                                     config_dir: Path) -> bool:
    """
    Configure Syncthing to auto-accept new devices and share the consume folder.
    
    This enables the "reversed" workflow where users add the server from their
    local Syncthing rather than needing access to the server's Web UI.
    """
    import time
    import urllib.request
    import urllib.error
    
    container_name = f"syncthing-{instance_name}"
    api_base = f"http://localhost:{config.web_ui_port}/rest"
    
    # Wait for API to be available
    api_key = None
    for attempt in range(30):
        api_key = get_syncthing_api_key(config_dir)
        if api_key:
            break
        time.sleep(1)
    
    if not api_key:
        warn("Could not get Syncthing API key - auto-accept not configured")
        return False
    
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Wait for API to be ready
        for attempt in range(30):
            try:
                req = urllib.request.Request(f"{api_base}/system/status", headers=headers)
                urllib.request.urlopen(req, timeout=5)
                break
            except:
                time.sleep(1)
        
        # Get current config
        req = urllib.request.Request(f"{api_base}/config", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            current_config = json.loads(response.read().decode())
        
        # Enable auto-accept for new devices (they'll be added automatically)
        # Set defaults for new devices to auto-accept and share the consume folder
        if "defaults" not in current_config:
            current_config["defaults"] = {}
        if "device" not in current_config["defaults"]:
            current_config["defaults"]["device"] = {}
        
        # Auto-accept new devices
        current_config["defaults"]["device"]["autoAcceptFolders"] = True
        current_config["defaults"]["device"]["untrusted"] = False
        
        # Ensure the consume folder exists in config and is set to share with new devices
        folder_exists = False
        for folder in current_config.get("folders", []):
            if folder.get("id") == config.folder_id:
                folder_exists = True
                # Ensure folder is shared with all devices
                break
        
        if not folder_exists:
            # Add the consume folder
            current_config.setdefault("folders", []).append({
                "id": config.folder_id,
                "label": config.folder_label,
                "path": "/var/syncthing/data/consume",
                "type": "sendreceive",
                "devices": [{"deviceID": config.device_id}] if config.device_id else [],
                "rescanIntervalS": 60,
                "fsWatcherEnabled": True,
                "fsWatcherDelayS": 10,
                "autoNormalize": True,
            })
        
        # Set GUI to allow remote access (for Tailscale users who can reach it)
        if "gui" in current_config:
            current_config["gui"]["address"] = "0.0.0.0:8384"
        
        # Push updated config
        config_data = json.dumps(current_config).encode()
        req = urllib.request.Request(
            f"{api_base}/config", 
            data=config_data, 
            headers=headers,
            method="PUT"
        )
        urllib.request.urlopen(req, timeout=10)
        
        ok("Syncthing configured for auto-accept")
        return True
        
    except Exception as e:
        warn(f"Could not configure Syncthing auto-accept: {e}")
        return False


def add_device_to_syncthing(instance_name: str, config: SyncthingConfig,
                            config_dir: Path, remote_device_id: str, 
                            device_name: str = "User Device") -> bool:
    """
    Add a remote device to Syncthing and share the consume folder with it.
    
    This can be used if auto-accept doesn't work or for manual setup.
    """
    import urllib.request
    
    api_key = get_syncthing_api_key(config_dir)
    if not api_key:
        error("Could not get Syncthing API key")
        return False
    
    api_base = f"http://localhost:{config.web_ui_port}/rest"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Get current config
        req = urllib.request.Request(f"{api_base}/config", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            current_config = json.loads(response.read().decode())
        
        # Add device if not exists
        device_exists = any(
            d.get("deviceID") == remote_device_id 
            for d in current_config.get("devices", [])
        )
        
        if not device_exists:
            current_config.setdefault("devices", []).append({
                "deviceID": remote_device_id,
                "name": device_name,
                "addresses": ["dynamic"],
                "autoAcceptFolders": True,
            })
        
        # Share consume folder with device
        for folder in current_config.get("folders", []):
            if folder.get("id") == config.folder_id:
                folder_devices = folder.setdefault("devices", [])
                if not any(d.get("deviceID") == remote_device_id for d in folder_devices):
                    folder_devices.append({"deviceID": remote_device_id})
                break
        
        # Push updated config
        config_data = json.dumps(current_config).encode()
        req = urllib.request.Request(
            f"{api_base}/config",
            data=config_data,
            headers=headers,
            method="PUT"
        )
        urllib.request.urlopen(req, timeout=10)
        
        ok(f"Added device '{device_name}' to Syncthing")
        return True
        
    except Exception as e:
        error(f"Failed to add device to Syncthing: {e}")
        return False


def create_syncthing_config(instance_name: str, consume_path: Path, 
                            config_dir: Path, ports: Optional[tuple[int, int]] = None) -> SyncthingConfig:
    """Create a new Syncthing configuration for an instance."""
    if ports is None:
        web_ui_port = get_next_available_port(8384)
        sync_port = get_next_available_port(22000)
    else:
        web_ui_port, sync_port = ports
    
    config = SyncthingConfig(
        enabled=True,
        folder_id=generate_folder_id(),
        folder_label=f"{instance_name} Consume",
        device_id="",  # Will be set after container starts
        api_key=generate_secure_password(32),
        web_ui_port=web_ui_port,
        sync_port=sync_port,
    )
    
    return config


def write_syncthing_compose_snippet(instance_name: str, config: SyncthingConfig,
                                     consume_path: Path, config_dir: Path) -> str:
    """Generate Docker Compose service definition for Syncthing."""
    return f"""
  syncthing-{instance_name}:
    image: syncthing/syncthing:latest
    container_name: syncthing-{instance_name}
    hostname: syncthing-{instance_name}
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - {config_dir}:/var/syncthing/config
      - {consume_path}:/var/syncthing/data/consume
    ports:
      - "{config.web_ui_port}:8384"
      - "{config.sync_port}:22000/tcp"
      - "{config.sync_port}:22000/udp"
      - "{config.sync_port + 27}:21027/udp"
    restart: unless-stopped
    networks:
      - paperless
"""


def start_syncthing_container(instance_name: str, config: SyncthingConfig,
                               consume_path: Path, config_dir: Path) -> bool:
    """Start a per-instance Syncthing container."""
    say(f"Starting Syncthing container for {instance_name}...")
    
    # Ensure directories exist
    config_dir.mkdir(parents=True, exist_ok=True)
    consume_path.mkdir(parents=True, exist_ok=True)
    
    container_name = f"syncthing-{instance_name}"
    
    # Stop existing container if running
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        check=False
    )
    
    try:
        # Run container
        result = subprocess.run([
            "docker", "run", "-d",
            "--name", container_name,
            "--hostname", container_name,
            "-e", "PUID=1000",
            "-e", "PGID=1000",
            "-v", f"{config_dir}:/var/syncthing/config",
            "-v", f"{consume_path}:/var/syncthing/data/consume",
            "-p", f"{config.web_ui_port}:8384",
            "-p", f"{config.sync_port}:22000/tcp",
            "-p", f"{config.sync_port}:22000/udp",
            "-p", f"{config.sync_port + 27}:21027/udp",
            "--restart", "unless-stopped",
            "syncthing/syncthing:latest"
        ], capture_output=True, text=True, check=True)
        
        ok(f"Syncthing container started: {container_name}")
        
        # Wait for it to initialize and get device ID
        import time
        for _ in range(30):
            time.sleep(1)
            device_id = get_syncthing_device_id(instance_name)
            if device_id:
                config.device_id = device_id
                ok(f"Syncthing device ID: {device_id}")
                
                # Configure auto-accept for incoming connections
                configure_syncthing_auto_accept(instance_name, config, config_dir)
                return True
        
        warn("Syncthing started but could not retrieve device ID")
        return True
        
    except subprocess.CalledProcessError as e:
        error(f"Failed to start Syncthing: {e}")
        return False


def stop_syncthing_container(instance_name: str) -> bool:
    """Stop and remove a Syncthing container."""
    container_name = f"syncthing-{instance_name}"
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            check=True
        )
        ok(f"Syncthing container stopped: {container_name}")
        return True
    except:
        return False


def get_syncthing_status(instance_name: str) -> dict:
    """Get status of Syncthing container."""
    container_name = f"syncthing-{instance_name}"
    try:
        result = subprocess.run(
            ["docker", "inspect", container_name, "--format", "{{.State.Status}}"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            status = result.stdout.strip()
            return {
                "running": status == "running",
                "status": status,
                "container": container_name
            }
    except:
        pass
    return {"running": False, "status": "not found", "container": container_name}


# ─── Samba Management ─────────────────────────────────────────────────────────

SAMBA_CONFIG_DIR = Path("/etc/paperless-bulletproof/samba")
SAMBA_CONTAINER_NAME = "paperless-samba"


def is_samba_available() -> bool:
    """Check if Samba container is running."""
    try:
        result = subprocess.run(
            ["docker", "inspect", SAMBA_CONTAINER_NAME, "--format", "{{.State.Running}}"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except:
        return False


def create_samba_config(instance_name: str) -> SambaConfig:
    """Create a new Samba configuration for an instance."""
    return SambaConfig(
        enabled=True,
        share_name=f"{instance_name}-consume",
        username=f"paperless-{instance_name}",
        password=generate_secure_password(16),
    )


def write_samba_share_config(instance_name: str, config: SambaConfig, consume_path: Path) -> str:
    """Generate Samba share configuration block."""
    return f"""
[{config.share_name}]
   path = {consume_path}
   valid users = {config.username}
   read only = no
   browseable = yes
   guest ok = no
   force create mode = 0644
   force directory mode = 0755
   comment = Paperless {instance_name} consume folder
"""


def regenerate_samba_config(instances_config: dict[str, ConsumeConfig], 
                            data_roots: dict[str, Path]) -> bool:
    """Regenerate the complete smb.conf from all instance configurations."""
    SAMBA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Global config
    config = """[global]
   workgroup = WORKGROUP
   server string = Paperless-NGX File Server
   security = user
   map to guest = never
   passdb backend = tdbsam
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no
   
"""
    
    # Add share for each instance with Samba enabled
    for instance_name, consume_config in instances_config.items():
        if consume_config.samba.enabled:
            consume_path = data_roots.get(instance_name, Path(f"/home/docker/{instance_name}")) / "consume"
            config += write_samba_share_config(instance_name, consume_config.samba, consume_path)
    
    config_file = SAMBA_CONFIG_DIR / "smb.conf"
    config_file.write_text(config)
    
    return True


def add_samba_user(username: str, password: str) -> bool:
    """Add a user to the Samba container."""
    if not is_samba_available():
        warn("Samba container not running")
        return False
    
    try:
        # Add system user
        subprocess.run(
            ["docker", "exec", SAMBA_CONTAINER_NAME, 
             "adduser", "-D", "-H", username],
            capture_output=True,
            check=False
        )
        
        # Set Samba password
        result = subprocess.run(
            ["docker", "exec", "-i", SAMBA_CONTAINER_NAME,
             "sh", "-c", f"echo -e '{password}\\n{password}' | smbpasswd -a -s {username}"],
            capture_output=True,
            check=False
        )
        
        return result.returncode == 0
    except:
        return False


def remove_samba_user(username: str) -> bool:
    """Remove a user from the Samba container."""
    if not is_samba_available():
        return True
    
    try:
        subprocess.run(
            ["docker", "exec", SAMBA_CONTAINER_NAME, "smbpasswd", "-x", username],
            capture_output=True,
            check=False
        )
        subprocess.run(
            ["docker", "exec", SAMBA_CONTAINER_NAME, "deluser", username],
            capture_output=True,
            check=False
        )
        return True
    except:
        return False


def reload_samba_config() -> bool:
    """Reload Samba configuration."""
    if not is_samba_available():
        return False
    
    try:
        subprocess.run(
            ["docker", "exec", SAMBA_CONTAINER_NAME, "smbcontrol", "all", "reload-config"],
            capture_output=True,
            check=True
        )
        return True
    except:
        return False


def start_samba_container(data_root_base: Path = Path("/home/docker")) -> bool:
    """Start the shared Samba container."""
    say("Starting Samba container...")
    
    SAMBA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Ensure config file exists
    config_file = SAMBA_CONFIG_DIR / "smb.conf"
    if not config_file.exists():
        config_file.write_text("""[global]
   workgroup = WORKGROUP
   server string = Paperless-NGX File Server
   security = user
   map to guest = never
   passdb backend = tdbsam
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no
""")
    
    # Stop existing container
    subprocess.run(
        ["docker", "rm", "-f", SAMBA_CONTAINER_NAME],
        capture_output=True,
        check=False
    )
    
    try:
        result = subprocess.run([
            "docker", "run", "-d",
            "--name", SAMBA_CONTAINER_NAME,
            "-p", "445:445",
            "-v", f"{SAMBA_CONFIG_DIR}/smb.conf:/etc/samba/smb.conf:ro",
            "-v", f"{data_root_base}:/home/docker",
            "--restart", "unless-stopped",
            "dperson/samba"
        ], capture_output=True, text=True, check=True)
        
        ok("Samba container started")
        return True
    except subprocess.CalledProcessError as e:
        error(f"Failed to start Samba: {e}")
        return False


def stop_samba_container() -> bool:
    """Stop the Samba container."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", SAMBA_CONTAINER_NAME],
            capture_output=True,
            check=True
        )
        ok("Samba container stopped")
        return True
    except:
        return False


# ─── SFTP Management ──────────────────────────────────────────────────────────

SFTP_CONFIG_DIR = Path("/etc/paperless-bulletproof/sftp")
SFTP_CONTAINER_NAME = "paperless-sftp"


def is_sftp_available() -> bool:
    """Check if SFTP container is running."""
    try:
        result = subprocess.run(
            ["docker", "inspect", SFTP_CONTAINER_NAME, "--format", "{{.State.Running}}"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except:
        return False


def create_sftp_config(instance_name: str, port: Optional[int] = None) -> SFTPConfig:
    """Create a new SFTP configuration for an instance."""
    if port is None:
        port = 2222
    
    return SFTPConfig(
        enabled=True,
        username=f"paperless-{instance_name}",
        password=generate_secure_password(16),
        port=port,
    )


def get_sftp_users_string(instances_config: dict[str, ConsumeConfig],
                          data_roots: dict[str, Path]) -> str:
    """Generate SFTP_USERS environment variable."""
    users = []
    for instance_name, consume_config in instances_config.items():
        if consume_config.sftp.enabled:
            cfg = consume_config.sftp
            data_root = data_roots.get(instance_name, Path(f"/home/docker/{instance_name}"))
            # Format: user:password:uid:gid:dir
            users.append(f"{cfg.username}:{cfg.password}:1000:1000:{instance_name}")
    return ",".join(users)


def start_sftp_container(instances_config: dict[str, ConsumeConfig],
                          data_roots: dict[str, Path],
                          port: int = 2222) -> bool:
    """Start the shared SFTP container."""
    say("Starting SFTP container...")
    
    SFTP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Stop existing container
    subprocess.run(
        ["docker", "rm", "-f", SFTP_CONTAINER_NAME],
        capture_output=True,
        check=False
    )
    
    # Build users string
    users_str = get_sftp_users_string(instances_config, data_roots)
    if not users_str:
        warn("No SFTP users configured")
        return False
    
    try:
        # Build volume mounts for each instance consume folder
        volumes = []
        for instance_name, consume_config in instances_config.items():
            if consume_config.sftp.enabled:
                data_root = data_roots.get(instance_name, Path(f"/home/docker/{instance_name}"))
                consume_path = data_root / "consume"
                username = consume_config.sftp.username
                volumes.extend(["-v", f"{consume_path}:/home/{username}/consume"])
        
        cmd = [
            "docker", "run", "-d",
            "--name", SFTP_CONTAINER_NAME,
            "-p", f"{port}:22",
            *volumes,
            "--restart", "unless-stopped",
            "atmoz/sftp",
            users_str
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        ok("SFTP container started")
        return True
    except subprocess.CalledProcessError as e:
        error(f"Failed to start SFTP: {e}")
        return False


def stop_sftp_container() -> bool:
    """Stop the SFTP container."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", SFTP_CONTAINER_NAME],
            capture_output=True,
            check=True
        )
        ok("SFTP container stopped")
        return True
    except:
        return False


def restart_sftp_with_config(instances_config: dict[str, ConsumeConfig],
                              data_roots: dict[str, Path],
                              port: int = 2222) -> bool:
    """Restart SFTP container with updated configuration."""
    stop_sftp_container()
    return start_sftp_container(instances_config, data_roots, port)


# ─── Consume Config Persistence ───────────────────────────────────────────────

def load_consume_config(instance_env_file: Path) -> ConsumeConfig:
    """Load consume configuration from instance .env file."""
    config = ConsumeConfig()
    
    if not instance_env_file.exists():
        return config
    
    env_vars = {}
    for line in instance_env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env_vars[key.strip()] = value.strip()
    
    # Syncthing
    config.syncthing.enabled = env_vars.get("CONSUME_SYNCTHING_ENABLED", "").lower() == "true"
    config.syncthing.folder_id = env_vars.get("CONSUME_SYNCTHING_FOLDER_ID", "")
    config.syncthing.folder_label = env_vars.get("CONSUME_SYNCTHING_FOLDER_LABEL", "")
    config.syncthing.device_id = env_vars.get("CONSUME_SYNCTHING_DEVICE_ID", "")
    config.syncthing.api_key = env_vars.get("CONSUME_SYNCTHING_API_KEY", "")
    config.syncthing.web_ui_port = int(env_vars.get("CONSUME_SYNCTHING_WEB_UI_PORT", "8384"))
    config.syncthing.sync_port = int(env_vars.get("CONSUME_SYNCTHING_SYNC_PORT", "22000"))
    
    # Samba
    config.samba.enabled = env_vars.get("CONSUME_SAMBA_ENABLED", "").lower() == "true"
    config.samba.share_name = env_vars.get("CONSUME_SAMBA_SHARE_NAME", "")
    config.samba.username = env_vars.get("CONSUME_SAMBA_USERNAME", "")
    config.samba.password = env_vars.get("CONSUME_SAMBA_PASSWORD", "")
    
    # SFTP
    config.sftp.enabled = env_vars.get("CONSUME_SFTP_ENABLED", "").lower() == "true"
    config.sftp.username = env_vars.get("CONSUME_SFTP_USERNAME", "")
    config.sftp.password = env_vars.get("CONSUME_SFTP_PASSWORD", "")
    config.sftp.port = int(env_vars.get("CONSUME_SFTP_PORT", "2222"))
    
    return config


def save_consume_config(config: ConsumeConfig, instance_env_file: Path) -> bool:
    """Save consume configuration to instance .env file."""
    if not instance_env_file.exists():
        return False
    
    lines = instance_env_file.read_text().splitlines()
    
    # Define consume config keys and their values
    consume_vars = {
        # Syncthing
        "CONSUME_SYNCTHING_ENABLED": str(config.syncthing.enabled).lower(),
        "CONSUME_SYNCTHING_FOLDER_ID": config.syncthing.folder_id,
        "CONSUME_SYNCTHING_FOLDER_LABEL": config.syncthing.folder_label,
        "CONSUME_SYNCTHING_DEVICE_ID": config.syncthing.device_id,
        "CONSUME_SYNCTHING_API_KEY": config.syncthing.api_key,
        "CONSUME_SYNCTHING_WEB_UI_PORT": str(config.syncthing.web_ui_port),
        "CONSUME_SYNCTHING_SYNC_PORT": str(config.syncthing.sync_port),
        # Samba
        "CONSUME_SAMBA_ENABLED": str(config.samba.enabled).lower(),
        "CONSUME_SAMBA_SHARE_NAME": config.samba.share_name,
        "CONSUME_SAMBA_USERNAME": config.samba.username,
        "CONSUME_SAMBA_PASSWORD": config.samba.password,
        # SFTP
        "CONSUME_SFTP_ENABLED": str(config.sftp.enabled).lower(),
        "CONSUME_SFTP_USERNAME": config.sftp.username,
        "CONSUME_SFTP_PASSWORD": config.sftp.password,
        "CONSUME_SFTP_PORT": str(config.sftp.port),
    }
    
    # Update existing lines or mark for addition
    existing_keys = set()
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in consume_vars:
                new_lines.append(f"{key}={consume_vars[key]}")
                existing_keys.add(key)
                continue
        new_lines.append(line)
    
    # Add consume section if needed
    if not existing_keys:
        new_lines.append("")
        new_lines.append("# Consume Input Methods")
    
    # Add missing keys
    for key, value in consume_vars.items():
        if key not in existing_keys:
            new_lines.append(f"{key}={value}")
    
    instance_env_file.write_text("\n".join(new_lines) + "\n")
    return True


# ─── Setup Guides Generation ─────────────────────────────────────────────────

def generate_syncthing_guide(instance_name: str, config: SyncthingConfig,
                              tailscale_ip: Optional[str] = None) -> str:
    """Generate setup guide for Syncthing."""
    host = tailscale_ip or "your-server-ip"
    
    # Format device ID for display (or show waiting message)
    if config.device_id and config.device_id != "Starting up...":
        device_id_display = config.device_id
        device_id_status = "✓ Ready"
    else:
        device_id_display = "(Server starting up - run guide again in 30 seconds)"
        device_id_status = "⏳ Starting..."
    
    return f"""
╭──────────────────────────────────────────────────────────────╮
│                  SYNCTHING SETUP GUIDE                       │
│                  Instance: {instance_name:<30}│
╰──────────────────────────────────────────────────────────────╯

┌─────────────────────────────────────────────────────────────┐
│  📋 SERVER DEVICE ID (copy this):                           │
│                                                             │
│  {device_id_display:<55} │
│                                                             │
│  Status: {device_id_status:<50}│
└─────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣  INSTALL SYNCTHING ON YOUR COMPUTER/PHONE
    ─────────────────────────────────────────
    Download from: https://syncthing.net/downloads/
    
    • Windows: Download and run the installer
    • Mac: brew install syncthing  (or download DMG)
    • Linux: sudo apt install syncthing
    • Android/iOS: Search "Syncthing" in your app store
    
    Then open Syncthing on your device (usually http://localhost:8384)

2️⃣  ADD THE PAPERLESS SERVER TO YOUR SYNCTHING
    ───────────────────────────────────────────
    On YOUR computer/phone's Syncthing:
    
    • Click "+ Add Remote Device"
    • Paste the SERVER DEVICE ID shown above
    • Name it: "Paperless Server" (or anything you like)
    • Click Save

3️⃣  WAIT FOR THE SERVER TO ACCEPT
    ──────────────────────────────
    The Paperless server will automatically accept your connection.
    You should see "Paperless Server" appear as connected (green).
    
    ⏱️  This may take 30-60 seconds.

4️⃣  ACCEPT THE SHARED FOLDER
    ─────────────────────────
    Once connected, your Syncthing will show a notification:
    
    "Paperless Server wants to share folder '{config.folder_label}'"
    
    • Click "Add" to accept
    • Choose where to save files on YOUR device
      (e.g., ~/Documents/Paperless-Inbox)
    • Click Save

5️⃣  START SYNCING!
    ───────────────
    • Drop PDF/documents into your local folder
    • They'll automatically sync to Paperless
    • Paperless will process and delete the originals

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 CONNECTION DETAILS
────────────────────
Server Device ID: {device_id_display}
Folder ID:        {config.folder_id}
Folder Name:      {config.folder_label}
Sync Port:        {config.sync_port} (TCP/UDP)

💡 TIPS
───────
• Make sure port {config.sync_port} is open on the server firewall
• First sync may take a moment to establish
• Files are encrypted during transfer
• No account or cloud service needed!

🔧 TROUBLESHOOTING
──────────────────
• "Device ID not found": Wait 30 sec and view guide again
• "Connection refused": Check firewall allows port {config.sync_port}
• "Disconnected": Both devices must be online simultaneously
"""


def generate_samba_guide(instance_name: str, config: SambaConfig,
                          tailscale_ip: Optional[str] = None) -> str:
    """Generate setup guide for Samba."""
    host = tailscale_ip or "your-server-ip"
    
    return f"""
╭──────────────────────────────────────────────────────────────╮
│                  SAMBA (SMB) SETUP GUIDE                     │
│                  Instance: {instance_name:<30}│
╰──────────────────────────────────────────────────────────────╯

┌─────────────────────────────────────────────────────────────┐
│  📋 CREDENTIALS (save these):                               │
│                                                             │
│  Server:   {host:<47} │
│  Share:    {config.share_name:<47} │
│  Username: {config.username:<47} │
│  Password: {config.password:<47} │
└─────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🪟 WINDOWS - Connect from File Explorer
    ────────────────────────────────────
    1. Open File Explorer
    2. Click in the address bar and type:
       \\\\{host}\\{config.share_name}
    3. Press Enter
    4. When prompted for credentials:
       • Username: {config.username}
       • Password: {config.password}
    5. Check "Remember my credentials"
    
    💡 To add as a permanent drive letter:
       • Right-click "This PC" → "Map network drive"
       • Choose a letter (e.g., P: for Paperless)
       • Enter: \\\\{host}\\{config.share_name}
       • Check "Connect using different credentials"

🍎 MAC - Connect from Finder
    ─────────────────────────
    1. Open Finder
    2. Press Cmd+K (or menu: Go → Connect to Server)
    3. Enter: smb://{host}/{config.share_name}
    4. Click Connect
    5. Enter credentials when prompted

🐧 LINUX - Connect via File Manager
    ─────────────────────────────────
    GUI:  Files → Other Locations → "Connect to Server"
          Enter: smb://{host}/{config.share_name}
    
    Terminal:
          sudo mount -t cifs //{host}/{config.share_name} /mnt/paperless \\
            -o username={config.username},password={config.password}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ DONE!
    Drag and drop PDF/documents into the network folder.
    Paperless will automatically process them.

🔧 TROUBLESHOOTING
──────────────────
• "Network path not found": Check server IP and firewall (port 445)
• "Access denied": Double-check username and password
• Slow connection: Samba works best on local/Tailscale networks
"""


def generate_sftp_guide(instance_name: str, config: SFTPConfig,
                         tailscale_ip: Optional[str] = None) -> str:
    """Generate setup guide for SFTP."""
    host = tailscale_ip or "your-server-ip"
    
    return f"""
╭──────────────────────────────────────────────────────────────╮
│                     SFTP SETUP GUIDE                         │
│                  Instance: {instance_name:<30}│
╰──────────────────────────────────────────────────────────────╯

┌─────────────────────────────────────────────────────────────┐
│  📋 CREDENTIALS (save these):                               │
│                                                             │
│  Host:     {host:<47} │
│  Port:     {config.port:<47} │
│  Username: {config.username:<47} │
│  Password: {config.password:<47} │
└─────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🪟 WINDOWS - Using WinSCP or FileZilla
    ────────────────────────────────────
    1. Download WinSCP: https://winscp.net/
       (or FileZilla: https://filezilla-project.org/)
    
    2. Create a new connection:
       • Protocol: SFTP
       • Host: {host}
       • Port: {config.port}
       • Username: {config.username}
       • Password: {config.password}
    
    3. Connect, then navigate to the /consume folder
    4. Drag and drop files to upload

🍎 MAC / 🐧 LINUX - Command Line
    ─────────────────────────────
    Connect:
        sftp -P {config.port} {config.username}@{host}
    
    Enter password when prompted: {config.password}
    
    Upload files:
        cd consume
        put document.pdf
        put *.pdf              # upload all PDFs in current folder

📱 MOBILE - Any SFTP App
    ─────────────────────
    Recommended apps: Termius, FE File Explorer, Solid Explorer
    
    • Host: {host}
    • Port: {config.port}
    • Username: {config.username}
    • Password: {config.password}
    • Navigate to /consume and upload files

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ DONE!
    Upload PDF/documents to the consume folder.
    Paperless will automatically process them.

🔧 TROUBLESHOOTING
──────────────────
• "Connection refused": Check firewall allows port {config.port}
• "Permission denied": Verify username and password
• "Host key verification": Accept the server's fingerprint on first connect
"""


# ─── Full Status Report ───────────────────────────────────────────────────────

def get_consume_status(instance_name: str, config: ConsumeConfig) -> dict:
    """Get status of all consume methods for an instance."""
    status = {
        "syncthing": {
            "enabled": config.syncthing.enabled,
            "running": False,
            "device_id": config.syncthing.device_id,
        },
        "samba": {
            "enabled": config.samba.enabled,
            "available": is_samba_available(),
        },
        "sftp": {
            "enabled": config.sftp.enabled,
            "available": is_sftp_available(),
        },
    }
    
    if config.syncthing.enabled:
        st_status = get_syncthing_status(instance_name)
        status["syncthing"]["running"] = st_status["running"]
    
    return status
