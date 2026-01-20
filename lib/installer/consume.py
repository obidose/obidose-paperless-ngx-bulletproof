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
    sync_port: int = 22000
    gui_port: int = 8384
    
    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "folder_id": self.folder_id,
            "folder_label": self.folder_label,
            "device_id": self.device_id,
            "api_key": self.api_key,
            "sync_port": self.sync_port,
            "gui_port": self.gui_port,
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


@dataclass
class GlobalConsumeConfig:
    """Global settings for consume services (affects all instances)."""
    samba_tailscale_only: bool = False  # If True, Samba only accessible via Tailscale
    sftp_tailscale_only: bool = False   # If True, SFTP only accessible via Tailscale
    
    def to_dict(self) -> dict:
        return {
            "samba_tailscale_only": self.samba_tailscale_only,
            "sftp_tailscale_only": self.sftp_tailscale_only,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'GlobalConsumeConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


GLOBAL_CONSUME_CONFIG_FILE = Path("/etc/paperless-bulletproof/consume-global.conf")


def load_global_consume_config() -> GlobalConsumeConfig:
    """Load global consume configuration."""
    config = GlobalConsumeConfig()
    
    if not GLOBAL_CONSUME_CONFIG_FILE.exists():
        return config
    
    try:
        for line in GLOBAL_CONSUME_CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().lower()
                if key == "SAMBA_TAILSCALE_ONLY":
                    config.samba_tailscale_only = value == "true"
                elif key == "SFTP_TAILSCALE_ONLY":
                    config.sftp_tailscale_only = value == "true"
    except Exception:
        pass
    
    return config


def save_global_consume_config(config: GlobalConsumeConfig) -> bool:
    """Save global consume configuration."""
    try:
        GLOBAL_CONSUME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        content = f"""# Global Consume Service Settings
# These settings affect ALL instances

# Restrict Samba to Tailscale network only (true/false)
SAMBA_TAILSCALE_ONLY={str(config.samba_tailscale_only).lower()}

# Restrict SFTP to Tailscale network only (true/false)
SFTP_TAILSCALE_ONLY={str(config.sftp_tailscale_only).lower()}
"""
        GLOBAL_CONSUME_CONFIG_FILE.write_text(content)
        return True
    except Exception:
        return False


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
    
    # Method 1: Try using syncthing CLI
    try:
        result = subprocess.run(
            ["docker", "exec", container_name, "syncthing", "cli", "show", "system"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15
        )
        if result.returncode == 0 and "myID" in result.stdout:
            # Parse JSON output
            import json
            data = json.loads(result.stdout)
            if "myID" in data:
                return data["myID"]
    except:
        pass
    
    # Method 2: Try the --device-id flag (older syncthing)
    try:
        result = subprocess.run(
            ["docker", "exec", container_name, "syncthing", "--device-id"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            device_id = result.stdout.strip()
            # Validate it looks like a device ID
            if "-" in device_id and len(device_id) > 20:
                return device_id
    except:
        pass
    
    # Method 3: Parse from config.xml if container volume is accessible
    try:
        result = subprocess.run(
            ["docker", "exec", container_name, "cat", "/var/syncthing/config/config.xml"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10
        )
        if result.returncode == 0:
            import re
            match = re.search(r'<device id="([^"]+)" name="[^"]*" compression="', result.stdout)
            if match:
                return match.group(1)
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


def get_syncthing_api_base(config_dir: Optional[Path] = None, gui_port: int = 8384) -> str:
    """
    Get the correct Syncthing API base URL.
    
    Syncthing may be bound to either localhost (no Tailscale) or Tailscale IP.
    This function determines which address actually works by:
    1. Reading the configured address from config.xml
    2. Trying the specified gui_port on current Tailscale IP
    3. Falling back to localhost
    
    Args:
        config_dir: Path to Syncthing config directory (to read config.xml)
        gui_port: The GUI port for this instance (default 8384, but per-instance)
    
    This handles all edge cases like Tailscale being installed/uninstalled
    after Syncthing was already configured.
    """
    import re
    import urllib.request
    import urllib.error
    
    addresses_to_try = []
    
    # Priority 1: Check what's actually in config.xml
    if config_dir:
        config_file = config_dir / "config.xml"
        if config_file.exists():
            try:
                content = config_file.read_text()
                match = re.search(r'<address>([^<]+)</address>', content)
                if match:
                    gui_addr = match.group(1).strip()
                    if gui_addr and ':' in gui_addr:
                        addresses_to_try.append(f"http://{gui_addr}/rest")
            except:
                pass
    
    # Priority 2: Current Tailscale IP with specified port
    from .tailscale import get_ip as get_tailscale_ip
    tailscale_ip = get_tailscale_ip()
    if tailscale_ip:
        ts_url = f"http://{tailscale_ip}:{gui_port}/rest"
        if ts_url not in addresses_to_try:
            addresses_to_try.append(ts_url)
    
    # Priority 3: Localhost fallback with specified port
    localhost_url = f"http://127.0.0.1:{gui_port}/rest"
    if localhost_url not in addresses_to_try:
        addresses_to_try.append(localhost_url)
    
    # Test each address to find one that works
    for url in addresses_to_try:
        try:
            req = urllib.request.Request(f"{url}/system/ping", method="GET")
            urllib.request.urlopen(req, timeout=2)
            return url  # This one works!
        except:
            continue
    
    # None responded - return the first one (will fail but with appropriate error)
    return addresses_to_try[0] if addresses_to_try else localhost_url


def fix_syncthing_gui_address(config_dir: Path, gui_port: int = 8384) -> str:
    """
    Fix Syncthing GUI to listen on Tailscale interface only (secure).
    Returns the GUI address that was set.
    
    Args:
        config_dir: Path to Syncthing config directory
        gui_port: The GUI port for this instance (per-instance, not hardcoded)
    
    If Tailscale is not available, binds to localhost only.
    NEVER binds to 0.0.0.0 to prevent external HTTP access.
    """
    import time
    import xml.etree.ElementTree as ET
    from .tailscale import get_ip as get_tailscale_ip
    
    config_file = config_dir / "config.xml"
    
    # Determine the GUI address based on Tailscale availability
    tailscale_ip = get_tailscale_ip()
    if tailscale_ip:
        desired = f"{tailscale_ip}:{gui_port}"
    else:
        desired = f"127.0.0.1:{gui_port}"
    
    # Wait for config file to exist (Syncthing generates it on first start)
    for _ in range(30):
        if config_file.exists():
            break
        time.sleep(1)
    
    if not config_file.exists():
        warn(f"Config file not found: {config_file}")
        return desired
    
    try:
        # Parse as XML for safe modification
        tree = ET.parse(config_file)
        root = tree.getroot()
        changed = False
        
        # Find and update the GUI address
        gui = root.find('gui')
        if gui is not None:
            address = gui.find('address')
            if address is not None:
                if address.text != desired:
                    address.text = desired
                    changed = True
            else:
                # Create address element if missing
                address = ET.SubElement(gui, 'address')
                address.text = desired
                changed = True
            
            # Disable host check for Tailscale access (Tailscale IPs are trusted)
            desired_hostcheck = "true" if tailscale_ip else "false"
            hostcheck = gui.find('insecureSkipHostcheck')
            if hostcheck is not None:
                if hostcheck.text != desired_hostcheck:
                    hostcheck.text = desired_hostcheck
                    changed = True
            else:
                hostcheck = ET.SubElement(gui, 'insecureSkipHostcheck')
                hostcheck.text = desired_hostcheck
                changed = True
        else:
            warn("GUI section not found in config.xml")
        
        if changed:
            tree.write(config_file, encoding='unicode', xml_declaration=True)
        return desired
    except ET.ParseError as e:
        warn(f"Config XML is corrupted: {e}")
        # Try to regenerate by deleting corrupted config
        try:
            config_file.unlink()
            warn("Deleted corrupted config.xml - Syncthing will regenerate it")
        except:
            pass
        return desired
    except Exception as e:
        warn(f"Could not fix GUI address: {e}")
        return desired


def initialize_syncthing(instance_name: str, config: SyncthingConfig,
                         config_dir: Path) -> bool:
    """
    Initialize Syncthing with the consume folder.
    
    Called after the container starts to create the shared folder.
    """
    import time
    import urllib.request
    import urllib.error
    
    # Fix the GUI address in config.xml (binds to Tailscale IP for secure access)
    # Use the per-instance GUI port from config
    gui_port = config.gui_port if config.gui_port else 8384
    fix_syncthing_gui_address(config_dir, gui_port)
    
    api_base = get_syncthing_api_base(config_dir, gui_port)
    
    # Wait for API to be available and get API key
    api_key = None
    for attempt in range(30):
        api_key = get_syncthing_api_key(config_dir)
        if api_key:
            break
        time.sleep(1)
    
    if not api_key:
        warn("Could not get Syncthing API key - initialization skipped")
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
        
        config_changed = False
        
        # 1. Check if consume folder exists, create if not
        folder_exists = False
        for folder in current_config.get("folders", []):
            if folder.get("id") == config.folder_id:
                folder_exists = True
                break
        
        if not folder_exists:
            # Create the consume folder
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
                "ignorePerms": False,
                "ignoreDelete": False,
            })
            config_changed = True
            say(f"Created shared folder: {config.folder_label}")
        
        # Push updated config if changed
        if config_changed:
            config_data = json.dumps(current_config).encode()
            req = urllib.request.Request(
                f"{api_base}/config",
                data=config_data,
                headers=headers,
                method="PUT"
            )
            urllib.request.urlopen(req, timeout=10)
            ok("Syncthing folder configured")
        
        return True
        
    except Exception as e:
        warn(f"Could not initialize Syncthing: {e}")
        return False


def add_device_to_syncthing(instance_name: str, config: SyncthingConfig,
                            config_dir: Path, remote_device_id: str, 
                            device_name: str = "User Device") -> bool:
    """
    Add a remote device to Syncthing and share the consume folder with it.
    
    This is the secure way to add devices - the admin explicitly adds each device
    by pasting its Device ID. Both sides must explicitly trust each other.
    """
    import urllib.request
    
    api_key = get_syncthing_api_key(config_dir)
    if not api_key:
        error("Could not get Syncthing API key")
        return False
    
    api_base = get_syncthing_api_base(config_dir)
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


def get_pending_devices(instance_name: str, config: SyncthingConfig,
                        config_dir: Path) -> list[dict]:
    """
    Get devices that are trying to connect but aren't trusted yet.
    
    These are devices that have attempted connection and were rejected.
    Returns a list of dicts with 'deviceID', 'name', 'address', and 'time'.
    """
    import urllib.request
    import urllib.error
    
    api_key = get_syncthing_api_key(config_dir)
    if not api_key:
        return []
    
    api_base = get_syncthing_api_base(config_dir)
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Get pending devices from cluster/pending/devices endpoint
        req = urllib.request.Request(f"{api_base}/cluster/pending/devices", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            pending_data = json.loads(response.read().decode())
        
        result = []
        for device_id, info in pending_data.items():
            result.append({
                "deviceID": device_id,
                "name": info.get("name", "Unknown Device"),
                "address": info.get("address", ""),
                "time": info.get("time", ""),
            })
        
        return result
        
    except Exception:
        return []


def list_syncthing_devices(instance_name: str, config: SyncthingConfig,
                           config_dir: Path) -> list[dict]:
    """
    List all devices currently configured in Syncthing.
    
    Returns a list of dicts with 'deviceID', 'name', and 'connected' status.
    """
    import urllib.request
    import urllib.error
    
    api_key = get_syncthing_api_key(config_dir)
    if not api_key:
        return []
    
    api_base = get_syncthing_api_base(config_dir)
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Get current config for device list
        req = urllib.request.Request(f"{api_base}/config/devices", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            devices = json.loads(response.read().decode())
        
        # Get connection status
        req = urllib.request.Request(f"{api_base}/system/connections", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            connections = json.loads(response.read().decode())
        
        result = []
        our_device_id = config.device_id
        
        for device in devices:
            device_id = device.get("deviceID", "")
            # Skip our own device
            if device_id == our_device_id:
                continue
            
            conn_info = connections.get("connections", {}).get(device_id, {})
            result.append({
                "deviceID": device_id,
                "name": device.get("name", "Unknown"),
                "connected": conn_info.get("connected", False),
            })
        
        return result
        
    except Exception:
        return []


def remove_device_from_syncthing(instance_name: str, config: SyncthingConfig,
                                  config_dir: Path, device_id: str) -> bool:
    """
    Remove a device from Syncthing.
    """
    import urllib.request
    
    api_key = get_syncthing_api_key(config_dir)
    if not api_key:
        error("Could not get Syncthing API key")
        return False
    
    api_base = get_syncthing_api_base(config_dir)
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Delete device via API
        req = urllib.request.Request(
            f"{api_base}/config/devices/{device_id}",
            headers=headers,
            method="DELETE"
        )
        urllib.request.urlopen(req, timeout=10)
        
        ok("Device removed from Syncthing")
        return True
        
    except Exception as e:
        error(f"Failed to remove device: {e}")
        return False


def create_syncthing_config(instance_name: str, consume_path: Path, 
                            config_dir: Path, sync_port: Optional[int] = None,
                            gui_port: Optional[int] = None) -> SyncthingConfig:
    """Create a new Syncthing configuration for an instance."""
    if sync_port is None:
        sync_port = get_next_available_port(22000)
    if gui_port is None:
        gui_port = get_next_available_port(8384)
    
    return SyncthingConfig(
        enabled=True,
        folder_id=generate_folder_id(),
        folder_label=f"{instance_name} Consume",
        device_id="",  # Will be set after container starts
        api_key=generate_secure_password(32),
        sync_port=sync_port,
        gui_port=gui_port,
    )


def write_syncthing_compose_snippet(instance_name: str, config: SyncthingConfig,
                                     consume_path: Path, config_dir: Path) -> str:
    """Generate Docker Compose service definition for Syncthing.
    
    Uses host network to bind to Tailscale interface directly.
    Web UI is only accessible via Tailscale IP (secure by default).
    """
    return f"""
  syncthing-{instance_name}:
    image: syncthing/syncthing:latest
    container_name: syncthing-{instance_name}
    hostname: syncthing-{instance_name}
    network_mode: host
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - {config_dir}:/var/syncthing/config
      - {consume_path}:/var/syncthing/data/consume
    restart: unless-stopped
"""


def start_syncthing_container(instance_name: str, config: SyncthingConfig,
                               consume_path: Path, config_dir: Path) -> bool:
    """Start a per-instance Syncthing container."""
    say(f"Starting Syncthing container for {instance_name}...")
    
    # Ensure directories exist
    config_dir.mkdir(parents=True, exist_ok=True)
    consume_path.mkdir(parents=True, exist_ok=True)
    
    # CRITICAL: Set directory ownership to match container's PUID/PGID (1000:1000)
    # The Syncthing container runs as UID 1000 and needs write access
    try:
        subprocess.run(
            ["chown", "-R", "1000:1000", str(config_dir)],
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["chown", "-R", "1000:1000", str(consume_path)],
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        warn(f"Could not set directory ownership (may need sudo): {e}")
        # Continue anyway - container might still work if run as root
    
    container_name = f"syncthing-{instance_name}"
    
    # Stop existing container if running
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        check=False
    )
    
    try:
        # Get Tailscale IP for secure Web UI binding
        from .tailscale import get_ip as get_tailscale_ip
        tailscale_ip = get_tailscale_ip()
        
        # Use per-instance GUI port (default 8384, but each instance gets its own)
        gui_port = config.gui_port if config.gui_port else 8384
        
        # Bind Web UI to Tailscale IP only (secure) or localhost (no external access)
        # NEVER bind to 0.0.0.0 - that would expose HTTP to the world
        if tailscale_ip:
            gui_addr = f"{tailscale_ip}:{gui_port}"
            say(f"Web UI will be available via Tailscale at http://{tailscale_ip}:{gui_port}")
        else:
            gui_addr = f"127.0.0.1:{gui_port}"
            say(f"Web UI bound to localhost only at port {gui_port} (no Tailscale detected)")
        
        docker_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--hostname", container_name,
            "--network", "host",  # Use host network to bind to Tailscale interface
            "-e", "PUID=1000",
            "-e", "PGID=1000",
            "-e", f"STGUIADDRESS={gui_addr}",
            "-v", f"{config_dir}:/var/syncthing/config",
            "-v", f"{consume_path}:/var/syncthing/data/consume",
            "--restart", "unless-stopped",
            "syncthing/syncthing:latest"
        ]
        # Note: With host network, we don't need to publish ports - they bind directly
        result = subprocess.run(docker_cmd, capture_output=True, text=True, check=True)
        
        ok(f"Syncthing container started: {container_name}")
        
        # Wait for it to initialize and get device ID
        import time
        say("Waiting for Syncthing to initialize...")
        
        # First, wait for the container to actually be running
        for attempt in range(10):
            time.sleep(1)
            status = get_syncthing_status(instance_name)
            if status["running"]:
                break
            elif status["status"] == "exited":
                error(f"Syncthing container exited with code {status.get('exit_code', '?')}")
                # Show last few log lines
                logs = get_syncthing_logs(instance_name, 10)
                if logs:
                    warn("Last log output:")
                    for line in logs.split("\n")[-5:]:
                        if line.strip():
                            print(f"  {line}")
                return False
        
        # Now wait for device ID to become available
        for attempt in range(30):
            time.sleep(1)
            device_id = get_syncthing_device_id(instance_name)
            if device_id:
                config.device_id = device_id
                ok(f"Syncthing device ID: {device_id}")
                
                # Initialize Syncthing with consume folder and Web UI access
                initialize_syncthing(instance_name, config, config_dir)
                return True
        
        # Container is running but we can't get device ID
        warn("Syncthing is running but could not retrieve device ID")
        warn("This may resolve itself - check 'Diagnose' menu for logs")
        return True
        
    except subprocess.CalledProcessError as e:
        error(f"Failed to start Syncthing: {e}")
        if e.stderr:
            warn(f"Error: {e.stderr}")
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
    """Get detailed status of Syncthing container."""
    container_name = f"syncthing-{instance_name}"
    result = {
        "running": False,
        "status": "not found",
        "container": container_name,
        "exit_code": None,
        "error": None,
        "uptime": None,
    }
    
    try:
        # Get container state
        inspect_result = subprocess.run(
            ["docker", "inspect", container_name, "--format", 
             "{{.State.Status}}|{{.State.ExitCode}}|{{.State.Error}}|{{.State.StartedAt}}"],
            capture_output=True,
            text=True,
            check=False
        )
        
        if inspect_result.returncode == 0:
            parts = inspect_result.stdout.strip().split("|")
            if len(parts) >= 4:
                status = parts[0]
                result["status"] = status
                result["running"] = status == "running"
                result["exit_code"] = int(parts[1]) if parts[1].isdigit() else None
                result["error"] = parts[2] if parts[2] else None
                
                # Calculate uptime if running
                if status == "running" and parts[3]:
                    try:
                        from datetime import datetime, timezone
                        started = parts[3].split(".")[0].replace("T", " ")
                        start_time = datetime.fromisoformat(started.replace("Z", ""))
                        uptime = datetime.now(timezone.utc) - start_time
                        if uptime.total_seconds() < 60:
                            result["uptime"] = f"{int(uptime.total_seconds())}s"
                        elif uptime.total_seconds() < 3600:
                            result["uptime"] = f"{int(uptime.total_seconds() / 60)}m"
                        else:
                            result["uptime"] = f"{int(uptime.total_seconds() / 3600)}h"
                    except:
                        pass
        else:
            result["status"] = "not found"
            
    except Exception as e:
        result["error"] = str(e)
    
    return result


def get_syncthing_logs(instance_name: str, lines: int = 50) -> str:
    """Get recent logs from Syncthing container."""
    container_name = f"syncthing-{instance_name}"
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), container_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            return result.stdout + result.stderr
        return f"Could not get logs: container '{container_name}' not found"
    except Exception as e:
        return f"Error getting logs: {e}"


def restart_syncthing_container(instance_name: str) -> bool:
    """Restart Syncthing container (keeps existing settings)."""
    container_name = f"syncthing-{instance_name}"
    try:
        result = subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            ok(f"Syncthing container restarted: {container_name}")
            return True
        else:
            error(f"Failed to restart: {result.stderr}")
            return False
    except Exception as e:
        error(f"Failed to restart: {e}")
        return False


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
    """Start the shared Samba container.
    
    Uses global config to determine if Tailscale-only mode is enabled.
    """
    from lib.installer.tailscale import get_ip as get_tailscale_ip
    
    say("Starting Samba container...")
    
    SAMBA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check global config for Tailscale-only mode
    global_config = load_global_consume_config()
    bind_ip = None
    if global_config.samba_tailscale_only:
        bind_ip = get_tailscale_ip()
        if not bind_ip:
            error("Tailscale-only mode requires Tailscale to be connected!")
            return False
    
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
    
    # Build port mapping
    port_mapping = f"{bind_ip}:445:445" if bind_ip else "445:445"
    
    try:
        result = subprocess.run([
            "docker", "run", "-d",
            "--name", SAMBA_CONTAINER_NAME,
            "-p", port_mapping,
            "-v", f"{SAMBA_CONFIG_DIR}/smb.conf:/etc/samba/smb.conf:ro",
            "-v", f"{data_root_base}:/home/docker",
            "--restart", "unless-stopped",
            "dperson/samba"
        ], capture_output=True, text=True, check=True)
        
        if bind_ip:
            ok(f"Samba container started (Tailscale-only: {bind_ip})")
        else:
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
    """Start the shared SFTP container.
    
    Uses global config to determine if Tailscale-only mode is enabled.
    """
    from lib.installer.tailscale import get_ip as get_tailscale_ip
    
    say("Starting SFTP container...")
    
    SFTP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check global config for Tailscale-only mode
    global_config = load_global_consume_config()
    bind_ip = None
    if global_config.sftp_tailscale_only:
        bind_ip = get_tailscale_ip()
        if not bind_ip:
            error("Tailscale-only mode requires Tailscale to be connected!")
            return False
    
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
        
        # Build port mapping
        port_mapping = f"{bind_ip}:{port}:22" if bind_ip else f"{port}:22"
        
        cmd = [
            "docker", "run", "-d",
            "--name", SFTP_CONTAINER_NAME,
            "-p", port_mapping,
            *volumes,
            "--restart", "unless-stopped",
            "atmoz/sftp",
            users_str
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if bind_ip:
            ok(f"SFTP container started (Tailscale-only: {bind_ip})")
        else:
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
    config.syncthing.sync_port = int(env_vars.get("CONSUME_SYNCTHING_SYNC_PORT", "22000"))
    config.syncthing.gui_port = int(env_vars.get("CONSUME_SYNCTHING_GUI_PORT", "8384"))
    
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
        "CONSUME_SYNCTHING_SYNC_PORT": str(config.syncthing.sync_port),
        "CONSUME_SYNCTHING_GUI_PORT": str(config.syncthing.gui_port),
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

def _get_webui_access_text(tailscale_ip: Optional[str]) -> str:
    """Generate Web UI access text based on Tailscale availability."""
    if tailscale_ip:
        return f"""URL: http://{tailscale_ip}:8384 (via Tailscale)
    The Web UI is accessible to devices on your Tailscale network.
    No authentication needed - Tailscale provides the security."""
    else:
        return """URL: http://localhost:8384 (local access only)
    Without Tailscale, Web UI is bound to localhost for security.
    To access remotely, use SSH port forwarding:
      ssh -L 8384:localhost:8384 user@server
    Then open http://localhost:8384 in your browser."""


def generate_syncthing_guide(instance_name: str, config: SyncthingConfig,
                              tailscale_ip: Optional[str] = None) -> str:
    """Generate setup guide for Syncthing."""
    host = tailscale_ip or "your-server-ip"
    
    # Format device ID for display (or show waiting message)
    if config.device_id and config.device_id != "Starting up...":
        device_id_display = config.device_id
        device_id_status = "✓ Ready"
    else:
        device_id_display = "(Server starting up - view guide again in 30 seconds)"
        device_id_status = "⏳ Starting..."
    
    return f"""
╭────────────────────────────────────────────────────────────────────────────────╮
│                           SYNCTHING SETUP GUIDE                                │
│                           Instance: {instance_name:<40}│
╰────────────────────────────────────────────────────────────────────────────────╯

Syncthing requires BOTH sides to add each other (mutual trust for security).

┌──────────────────────────────────────────────────────────────────────────────┐
│  📋 SERVER DEVICE ID:                                                        │
│  {device_id_display:<74}│
│  Status: {device_id_status:<68}│
└──────────────────────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣  INSTALL SYNCTHING ON YOUR DEVICE
    ──────────────────────────────────
    Download from: https://syncthing.net/downloads/
    • Windows/Mac/Linux: Download installer or use package manager
    • Mobile: Search "Syncthing" in your app store
    
    Open Syncthing (usually http://localhost:8384)

2️⃣  GET YOUR DEVICE ID
    ────────────────────
    In YOUR Syncthing: Actions → Show ID
    Copy your Device ID (looks like: XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-...)

WEB UI ACCESS
    ─────────────
    {_get_webui_access_text(tailscale_ip)}

3️⃣  ADD YOUR DEVICE TO THE SERVER (do this in the app menu)
    ─────────────────────────────────────────────────────────
    Go back to the Consume menu and select "Add a Syncthing device"
    Paste YOUR Device ID and give it a name (e.g., "John's Laptop")

4️⃣  ADD THE SERVER TO YOUR SYNCTHING
    ──────────────────────────────────
    In YOUR Syncthing: + Add Remote Device
    • Paste the SERVER DEVICE ID shown above
    • Name: "Paperless Server"
    • Click Save

5️⃣  ACCEPT THE SHARED FOLDER
    ─────────────────────────
    Your Syncthing will show: "Paperless Server wants to share folder '{config.folder_label}'"
    • Click "Add"
    • Choose where to save files (e.g., ~/Documents/Paperless-Inbox)
    • Click Save

6️⃣  START SYNCING!
    ───────────────
    Drop documents into your local folder → they sync to Paperless automatically!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 CONNECTION INFO
──────────────────
Server Device ID: {device_id_display}
Folder Name:      {config.folder_label}
Sync Port:        {config.sync_port} (TCP/UDP)
Web UI:           {f"http://{host}:8384 (Tailscale)" if tailscale_ip else "localhost:8384 (SSH tunnel)"}

🔒 SECURITY NOTE
────────────────
Both sides must explicitly add each other - this prevents unauthorized access.
Only share Device IDs with people you trust to upload documents.
{'' if tailscale_ip else '''
💡 Want remote Web UI access? Install Tailscale!
   Run the installer again and select the Tailscale option.
   Or use SSH: ssh -L 8384:localhost:8384 user@your-server
'''}"""


def generate_samba_guide(instance_name: str, config: SambaConfig,
                          server_ip: Optional[str] = None,
                          is_tailscale: bool = False) -> str:
    """Generate setup guide for Samba."""
    host = server_ip or "your-server-ip"
    
    security_note = ""
    if not is_tailscale and server_ip and not server_ip.startswith("100."):
        security_note = """
⚠️  SECURITY NOTE
──────────────────
You're using a public IP. For secure remote access, consider installing Tailscale!
This ensures only your devices can access the share.
"""
    
    return f"""
╭────────────────────────────────────────────────────────────────────────────────╮
│                           SAMBA (SMB) SETUP GUIDE                              │
│                           Instance: {instance_name:<40}│
╰────────────────────────────────────────────────────────────────────────────────╯

┌──────────────────────────────────────────────────────────────────────────────┐
│  📋 CREDENTIALS (save these):                                                │
│                                                                              │
│  Server:   {host:<65}│
│  Share:    {config.share_name:<65}│
│  Username: {config.username:<65}│
│  Password: {config.password:<65}│
└──────────────────────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🪟 WINDOWS - Connect from File Explorer
    ────────────────────────────────────
    1. Open File Explorer
    2. Click in the address bar and type: \\\\{host}\\{config.share_name}
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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ DONE!
    Drag and drop PDF/documents into the network folder.
    Paperless will automatically process them.

🔧 TROUBLESHOOTING
──────────────────
• "Network path not found": Check server IP and firewall (port 445)
• "Access denied": Double-check username and password
• Slow connection: Samba works best on local/Tailscale networks
{security_note}"""


def generate_sftp_guide(instance_name: str, config: SFTPConfig,
                         server_ip: Optional[str] = None,
                         is_tailscale: bool = False) -> str:
    """Generate setup guide for SFTP."""
    host = server_ip or "your-server-ip"
    
    security_note = ""
    if not is_tailscale and server_ip and not server_ip.startswith("100."):
        security_note = """
⚠️  SECURITY NOTE
──────────────────
You're using a public IP. For secure remote access, consider installing Tailscale!
This ensures only your devices can access the SFTP server.
"""
    
    return f"""
╭────────────────────────────────────────────────────────────────────────────────╮
│                            SFTP SETUP GUIDE                                    │
│                           Instance: {instance_name:<40}│
╰────────────────────────────────────────────────────────────────────────────────╯

┌──────────────────────────────────────────────────────────────────────────────┐
│  📋 CREDENTIALS (save these):                                                │
│                                                                              │
│  Host:     {host:<65}│
│  Port:     {config.port:<65}│
│  Username: {config.username:<65}│
│  Password: {config.password:<65}│
└──────────────────────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ DONE!
    Upload PDF/documents to the consume folder.
    Paperless will automatically process them.

🔧 TROUBLESHOOTING
──────────────────
• "Connection refused": Check firewall allows port {config.port}
• "Permission denied": Verify username and password
• "Host key verification": Accept the server's fingerprint on first connect
{security_note}"""


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
