#!/usr/bin/env python3
"""
Paperless-NGX Bulletproof Manager
A comprehensive TUI for managing Paperless-NGX instances with backup/restore capabilities.
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Determine branch (set by paperless.py or default to main)
BRANCH = os.environ.get("BP_BRANCH", "main")


# ─── Colors ───────────────────────────────────────────────────────────────────

class Colors:
    """ANSI color codes for terminal output."""
    BLUE = "\033[1;34m"
    GREEN = "\033[1;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[1;31m"
    CYAN = "\033[1;36m"
    MAGENTA = "\033[1;35m"
    BOLD = "\033[1m"
    OFF = "\033[0m"


def colorize(text: str, color: str) -> str:
    """Wrap text in color codes."""
    return f"{color}{text}{Colors.OFF}"


def say(msg: str) -> None:
    """Print an info message."""
    print(f"{Colors.BLUE}[*]{Colors.OFF} {msg}")


def ok(msg: str) -> None:
    """Print a success message."""
    print(f"{Colors.GREEN}[✓]{Colors.OFF} {msg}")


def warn(msg: str) -> None:
    """Print a warning message."""
    print(f"{Colors.YELLOW}[!]{Colors.OFF} {msg}")


def error(msg: str) -> None:
    """Print an error message."""
    print(f"{Colors.RED}[✗]{Colors.OFF} {msg}")


def die(msg: str, code: int = 1) -> None:
    """Print an error and exit."""
    error(msg)
    sys.exit(code)


# ─── UI Utilities ─────────────────────────────────────────────────────────────

import re as _re

def create_box_helper(width: int = 58):
    """Create a box line helper with specified inner width."""
    def box_line(content: str) -> str:
        """Create a properly padded box line."""
        clean = _re.sub(r'\033\[[0-9;]+m', '', content)
        padding = width - len(clean)
        if padding < 0:
            truncated = clean[:width-3] + "..."
            return colorize("│", Colors.CYAN) + truncated + colorize("│", Colors.CYAN)
        return colorize("│", Colors.CYAN) + content + " " * padding + colorize("│", Colors.CYAN)
    return box_line, width


def draw_box_top(width: int = 58) -> str:
    """Draw box top border."""
    return colorize("╭" + "─" * width + "╮", Colors.CYAN)


def draw_box_bottom(width: int = 58) -> str:
    """Draw box bottom border."""
    return colorize("╰" + "─" * width + "╯", Colors.CYAN)


def draw_box_divider(width: int = 58) -> str:
    """Draw box horizontal divider."""
    return colorize("├" + "─" * width + "┤", Colors.CYAN)


def draw_section_header(title: str, width: int = 58) -> str:
    """Draw a section header within content area."""
    padding = width - len(title) - 2
    left_pad = padding // 2
    right_pad = padding - left_pad
    return colorize("│", Colors.CYAN) + " " + colorize("─" * left_pad, Colors.CYAN) + f" {colorize(title, Colors.BOLD)} " + colorize("─" * right_pad, Colors.CYAN) + " " + colorize("│", Colors.CYAN)


# ─── Shared Instance Setup Helpers ────────────────────────────────────────────

def setup_instance_config(instance_name: str, existing_instances: list[str] = None) -> tuple[bool, str]:
    """
    Set up instance configuration with validation.
    Returns (success, error_message).
    """
    if existing_instances is None:
        existing_instances = []
    
    # Validate instance name
    if instance_name in existing_instances:
        return False, f"Instance '{instance_name}' already exists"
    
    if not instance_name or not instance_name.replace("-", "").replace("_", "").isalnum():
        return False, "Instance name must be alphanumeric (hyphens and underscores allowed)"
    
    # Import and configure
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import common
    
    # Set instance name and compute paths
    common.cfg.instance_name = instance_name
    common.cfg.data_root = f"/home/docker/{instance_name}"
    common.cfg.stack_dir = f"/home/docker/{instance_name}-setup"
    common.cfg.rclone_remote_path = f"backups/paperless/{instance_name}"
    common.cfg.refresh_paths()
    
    return True, ""


def check_networking_dependencies() -> dict[str, bool]:
    """Check availability of networking services. Returns dict of service -> available."""
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import traefik, cloudflared, tailscale
    
    return {
        "traefik_running": traefik.is_traefik_running(),
        "cloudflared_installed": cloudflared.is_cloudflared_installed(),
        "cloudflared_authenticated": cloudflared.is_authenticated(),
        "tailscale_installed": tailscale.is_tailscale_installed(),
        "tailscale_connected": tailscale.is_connected(),
    }


def setup_cloudflare_tunnel(instance_name: str, domain: str) -> bool:
    """Set up Cloudflare tunnel for an instance. Returns success status."""
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import cloudflared, common
    
    if not cloudflared.is_authenticated():
        return False
    
    print()
    common.say("Setting up Cloudflare Tunnel...")
    
    if not cloudflared.create_tunnel(instance_name, domain):
        common.warn("Failed to create Cloudflare tunnel")
        return False
    
    common.ok(f"Cloudflare tunnel ready for {domain}")
    common.say(f"To start: cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run")
    
    if confirm("Start tunnel as systemd service?", True):
        try:
            service_content = f"""[Unit]
Description=Cloudflare Tunnel for {instance_name}
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""
            service_file = Path(f"/etc/systemd/system/cloudflared-{instance_name}.service")
            service_file.write_text(service_content)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", f"cloudflared-{instance_name}"], check=True)
            subprocess.run(["systemctl", "start", f"cloudflared-{instance_name}"], check=True)
            common.ok("Tunnel service started")
            return True
        except Exception as e:
            common.warn(f"Failed to create service: {e}")
            return False
    
    return True


def finalize_instance_setup(instance_manager: 'InstanceManager', instance_name: str, 
                           stack_dir: Path, data_root: Path, enable_cloudflared: str, 
                           domain: str) -> None:
    """Finalize instance setup - register and set up optional services."""
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import common, files
    
    # Install backup cron
    files.install_cron_backup()
    
    # Set up Cloudflare tunnel if enabled
    if enable_cloudflared == "yes":
        setup_cloudflare_tunnel(instance_name, domain)
    
    # Register instance
    instance_manager.add_instance(instance_name, stack_dir, data_root)


# ─── Instance Configuration ───────────────────────────────────────────────────

@dataclass
class Instance:
    """Configuration for a Paperless-NGX instance."""
    name: str
    stack_dir: Path
    data_root: Path
    env_file: Path
    compose_file: Path
    
    @property
    def is_running(self) -> bool:
        """Check if the instance is currently running."""
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(self.compose_file), "ps", "-q"],
                capture_output=True,
                check=False
            )
            return bool(result.stdout.strip())
        except Exception:
            return False
    
    def get_env_value(self, key: str, default: str = "") -> str:
        """Get a value from the instance's .env file."""
        if not self.env_file.exists():
            return default
        for line in self.env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
        return default
    
    def get_access_modes(self) -> list[str]:
        """Determine all active access modes for this instance."""
        modes = []
        
        # Check for active Traefik routing
        try:
            if self.compose_file.exists():
                compose_content = self.compose_file.read_text()
                if "traefik.enable=true" in compose_content:
                    result = subprocess.run(
                        ["docker", "ps", "--filter", "name=traefik-system", "--format", "{{.Names}}"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if "traefik-system" in result.stdout:
                        modes.append("traefik")
        except:
            pass
        
        # Check for active Cloudflare tunnel service
        try:
            result = subprocess.run(
                ["systemctl", "is-active", f"cloudflared-{self.name}"],
                capture_output=True,
                check=False
            )
            if result.returncode == 0:  # Service is active
                modes.append("cloudflared")
        except:
            pass
        
        # Check for Tailscale connectivity (additive - can work with other modes)
        try:
            result = subprocess.run(
                ["tailscale", "status"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0 and result.stdout.strip():
                enable_tailscale = self.get_env_value("ENABLE_TAILSCALE", "no")
                if enable_tailscale == "yes":
                    modes.append("tailscale")
        except:
            pass
        
        # Always have direct HTTP as fallback if no other modes
        if not modes or "traefik" not in modes and "cloudflared" not in modes:
            modes.append("http")
        
        return modes
    
    def get_access_mode(self) -> str:
        """Get primary access mode (for backward compatibility)."""
        modes = self.get_access_modes()
        # Priority: traefik > cloudflared > tailscale > http
        for priority in ["traefik", "cloudflared", "tailscale", "http"]:
            if priority in modes:
                return priority
        return "http"
    
    def get_access_urls(self) -> list[tuple[str, str]]:
        """Get all access URLs with mode indicators."""
        urls = []
        modes = self.get_access_modes()
        domain = self.get_env_value("DOMAIN", "localhost")
        port = self.get_env_value("HTTP_PORT", "8000")
        
        if "traefik" in modes:
            urls.append(("🔒 Traefik HTTPS", f"https://{domain}"))
        
        if "cloudflared" in modes:
            urls.append(("☁️  Cloudflare", f"https://{domain}"))
        
        if "tailscale" in modes:
            try:
                result = subprocess.run(
                    ["tailscale", "ip", "-4"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0 and result.stdout.strip():
                    ip = result.stdout.strip()
                    # Check for Tailscale Serve path
                    serve_path = self.get_env_value("TAILSCALE_SERVE_PATH", "")
                    if serve_path:
                        # Get hostname for Tailscale Serve URL
                        try:
                            from lib.installer.tailscale import get_hostname
                            hostname = get_hostname()
                            if hostname:
                                urls.append(("🔐 Tailscale HTTPS", f"https://{hostname}{serve_path}"))
                            else:
                                urls.append(("🔐 Tailscale", f"http://{ip}:{port}"))
                        except:
                            urls.append(("🔐 Tailscale", f"http://{ip}:{port}"))
                    else:
                        urls.append(("🔐 Tailscale", f"http://{ip}:{port}"))
            except:
                urls.append(("🔐 Tailscale", f"http://tailscale-ip:{port}"))
        
        if "http" in modes:
            urls.append(("🌐 Direct", f"http://localhost:{port}"))
        
        return urls
    
    def get_access_url(self) -> str:
        """Get the primary access URL with mode indicator (for backward compatibility)."""
        urls = self.get_access_urls()
        if urls:
            return f"{urls[0][0]}: {urls[0][1]}"
        port = self.get_env_value("HTTP_PORT", "8000")
        return f"🌐 localhost:{port}"


class InstanceManager:
    """Manages multiple Paperless-NGX instances."""
    
    def __init__(self, config_dir: Path = Path("/etc/paperless-bulletproof")):
        self.config_dir = config_dir
        self.config_file = config_dir / "instances.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._instances: dict[str, Instance] = {}
        self.load_instances()
    
    def load_instances(self) -> None:
        """Load instances from config file."""
        if not self.config_file.exists():
            # Try to auto-discover default instance
            default_env = Path("/home/docker/paperless-setup/.env")
            if default_env.exists():
                self.add_instance(
                    "paperless",
                    Path("/home/docker/paperless-setup"),
                    Path("/home/docker/paperless")
                )
            return
        
        try:
            data = json.loads(self.config_file.read_text())
            for name, config in data.items():
                self._instances[name] = Instance(
                    name=name,
                    stack_dir=Path(config["stack_dir"]),
                    data_root=Path(config["data_root"]),
                    env_file=Path(config["env_file"]),
                    compose_file=Path(config["compose_file"])
                )
        except Exception as e:
            warn(f"Failed to load instances config: {e}")
    
    def save_instances(self) -> None:
        """Save instances to config file."""
        data = {}
        for name, instance in self._instances.items():
            data[name] = {
                "stack_dir": str(instance.stack_dir),
                "data_root": str(instance.data_root),
                "env_file": str(instance.env_file),
                "compose_file": str(instance.compose_file)
            }
        self.config_file.write_text(json.dumps(data, indent=2))
    
    def add_instance(self, name: str, stack_dir: Path, data_root: Path) -> Instance:
        """Add a new instance."""
        instance = Instance(
            name=name,
            stack_dir=stack_dir,
            data_root=data_root,
            env_file=stack_dir / ".env",
            compose_file=stack_dir / "docker-compose.yml"
        )
        self._instances[name] = instance
        self.save_instances()
        return instance
    
    def remove_instance(self, name: str, delete_files: bool = True) -> None:
        """Remove an instance from tracking and optionally delete files.
        
        Args:
            name: Instance name
            delete_files: If True, stop containers and delete stack_dir and data_root directories
        """
        if name not in self._instances:
            return
        
        instance = self._instances[name]
        
        if delete_files:
            import shutil
            
            # Stop and remove containers first
            if instance.compose_file.exists():
                try:
                    subprocess.run(
                        ["docker", "compose", "-f", str(instance.compose_file), "down", "-v"],
                        cwd=instance.stack_dir,
                        capture_output=True,
                        check=False
                    )
                except:
                    pass
            
            # Delete stack directory
            if instance.stack_dir.exists():
                try:
                    shutil.rmtree(instance.stack_dir)
                except Exception as e:
                    warn(f"Could not delete stack directory: {e}")
            
            # Delete data directory
            if instance.data_root.exists():
                try:
                    shutil.rmtree(instance.data_root)
                except Exception as e:
                    warn(f"Could not delete data directory: {e}")
            
            # Stop and remove cloudflared service if exists
            try:
                subprocess.run(
                    ["systemctl", "stop", f"cloudflared-{name}"],
                    capture_output=True,
                    check=False
                )
                subprocess.run(
                    ["systemctl", "disable", f"cloudflared-{name}"],
                    capture_output=True,
                    check=False
                )
                service_file = Path(f"/etc/systemd/system/cloudflared-{name}.service")
                if service_file.exists():
                    service_file.unlink()
                subprocess.run(["systemctl", "daemon-reload"], capture_output=True, check=False)
                # Delete Cloudflare tunnel itself
                sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
                try:
                    from lib.installer.cloudflared import delete_tunnel
                    delete_tunnel(name)
                except Exception:
                    pass
            except:
                pass
        
        # Remove from tracking
        del self._instances[name]
        self.save_instances()
    
    def get_instance(self, name: str) -> Optional[Instance]:
        """Get an instance by name."""
        return self._instances.get(name)
    
    def list_instances(self) -> list[Instance]:
        """Get all instances."""
        return list(self._instances.values())


# ─── UI Helpers ───────────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    """Print a decorative header."""
    width = max(60, len(title) + 10)
    print()
    print(colorize("╔" + "═" * (width - 2) + "╗", Colors.CYAN))
    print(colorize(f"║{title.center(width - 2)}║", Colors.CYAN))
    print(colorize("╚" + "═" * (width - 2) + "╝", Colors.CYAN))
    print()


def print_menu(options: list[tuple[str, str]], prompt: str = "Choose") -> None:
    """Print a menu with numbered options."""
    for key, description in options:
        print(f"  {colorize(key + ')', Colors.BOLD)} {description}")
    print()


def get_input(prompt: str, default: str = "") -> str:
    """Get user input with optional default."""
    if default:
        display = f"{prompt} [{colorize(default, Colors.YELLOW)}]: "
    else:
        display = f"{prompt}: "
    return input(display).strip() or default


def confirm(prompt: str, default: bool = False) -> bool:
    """Ask for yes/no confirmation."""
    options = "[Y/n]" if default else "[y/N]"
    response = input(f"{prompt} {options}: ").strip().lower()
    if not response:
        return default
    return response.startswith('y')


# ─── Restore Helper ───────────────────────────────────────────────────────────

def run_restore_with_env(
    snapshot: str,
    instance_name: str,
    env_file: Path,
    compose_file: Path,
    stack_dir: Path,
    data_root: Path,
    rclone_remote_name: str,
    rclone_remote_path: str,
    merge_config: bool = True
) -> bool:
    """
    Execute a restore operation with proper environment setup.
    
    This is the central restore function that all restore operations should use
    to ensure consistency and proper environment handling.
    
    Args:
        snapshot: Name of the snapshot to restore
        instance_name: Name of the instance being restored
        env_file: Path to the instance's .env file
        compose_file: Path to the instance's docker-compose.yml
        stack_dir: Path to the instance's stack directory
        data_root: Path to the instance's data root
        rclone_remote_name: Name of the rclone remote (e.g., "pcloud")
        rclone_remote_path: Path within the remote (e.g., "backups/paperless/myinstance")
        merge_config: If True, merge backup .env with new instance .env, keeping
                      network/path settings from new config while bringing in
                      credentials and other settings from backup.
                      If False, fully overwrite .env and docker-compose.yml from backup.
    
    Returns:
        True if restore succeeded, False otherwise
    """
    # Set up environment variables for the restore
    env_vars = {
        "INSTANCE_NAME": instance_name,
        "ENV_FILE": str(env_file),
        "COMPOSE_FILE": str(compose_file),
        "STACK_DIR": str(stack_dir),
        "DATA_ROOT": str(data_root),
        "RCLONE_REMOTE_NAME": rclone_remote_name,
        "RCLONE_REMOTE_PATH": rclone_remote_path,
        "MERGE_CONFIG": "yes" if merge_config else "no",
    }
    
    # Temporarily set environment variables
    original_env = {}
    for key, value in env_vars.items():
        original_env[key] = os.environ.get(key)
        os.environ[key] = value
    
    try:
        # Determine correct lib path (installed or development)
        lib_path_installed = Path("/usr/local/lib/paperless-bulletproof/lib")
        lib_path_dev = Path(__file__).parent
        lib_path = str(lib_path_installed if lib_path_installed.exists() else lib_path_dev)
        
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
        
        # Import and run the restore function
        from modules.restore import restore_snapshot as do_restore
        do_restore(snapshot)
        return True
        
    except Exception as e:
        error(f"Restore failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Restore original environment
        for key, original_value in original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value


# ─── Backup Operations ────────────────────────────────────────────────────────

class BackupManager:
    """Handles backup and restore operations."""
    
    def __init__(self, instance: Instance):
        self.instance = instance
        self.remote_name = instance.get_env_value("RCLONE_REMOTE_NAME", "pcloud")
        self.remote_path = instance.get_env_value(
            "RCLONE_REMOTE_PATH", 
            f"backups/paperless/{instance.name}"
        )
        self.remote = f"{self.remote_name}:{self.remote_path}"
    
    def fetch_snapshots(self) -> list[tuple[str, str, str]]:
        """Fetch available snapshots from remote."""
        try:
            result = subprocess.run(
                ["rclone", "lsd", self.remote],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                return []
            
            snapshots = []
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                name = parts[-1]
                
                # Try to get manifest info
                mode = parent = "?"
                manifest = subprocess.run(
                    ["rclone", "cat", f"{self.remote}/{name}/manifest.yaml"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if manifest.returncode == 0:
                    for mline in manifest.stdout.splitlines():
                        if ":" in mline:
                            k, v = mline.split(":", 1)
                            k, v = k.strip(), v.strip()
                            if k == "mode":
                                mode = v
                            elif k == "parent":
                                parent = v
                
                snapshots.append((name, mode, parent))
            
            return sorted(snapshots, key=lambda x: x[0])
        except Exception:
            return []
    
    def run_backup(self, mode: str = "incr") -> bool:
        """Run a backup operation."""
        script = self.instance.stack_dir / "backup.py"
        if not script.exists():
            error(f"Backup script not found at {script}")
            return False
        
        try:
            # Ensure backup script uses the correct instance context
            env = os.environ.copy()
            env.update({
                "ENV_FILE": str(self.instance.env_file),
                "COMPOSE_FILE": str(self.instance.compose_file),
                "STACK_DIR": str(self.instance.stack_dir),
                "DATA_ROOT": str(self.instance.data_root),
                "RCLONE_REMOTE_NAME": self.remote_name,
                "RCLONE_REMOTE_PATH": self.remote_path,
            })
            subprocess.run([str(script), mode], check=True, env=env)
            return True
        except subprocess.CalledProcessError:
            return False
    
    def run_restore(self, snapshot: Optional[str] = None) -> bool:
        """Run a restore operation for an existing instance.
        
        Uses the centralized run_restore_with_env helper for consistency.
        For existing instances, we restore config files from backup (preserve_config=False).
        """
        if not snapshot:
            # If no snapshot specified, we need to get the latest
            snapshots = self.fetch_snapshots()
            if not snapshots:
                error("No snapshots available to restore")
                return False
            snapshot = snapshots[-1][0]  # Use latest
        
        return run_restore_with_env(
            snapshot=snapshot,
            instance_name=self.instance.name,
            env_file=self.instance.env_file,
            compose_file=self.instance.compose_file,
            stack_dir=self.instance.stack_dir,
            data_root=self.instance.data_root,
            rclone_remote_name=self.remote_name,
            rclone_remote_path=self.remote_path,
            merge_config=False  # Full restore for existing instance
        )


# ─── Health Checks ────────────────────────────────────────────────────────────

class HealthChecker:
    """Performs system health checks."""
    
    def __init__(self, instance: Instance):
        self.instance = instance
    
    def check_all(self) -> dict[str, bool]:
        """Run all health checks."""
        checks = {
            "Instance exists": self.check_instance_exists(),
            "Docker available": self.check_docker(),
            "Compose file exists": self.check_compose_file(),
            "Environment file exists": self.check_env_file(),
            "Data directories exist": self.check_data_dirs(),
            "Containers running": self.check_containers(),
            "Rclone configured": self.check_rclone(),
            "Backup remote accessible": self.check_backup_remote()
        }
        return checks
    
    def check_instance_exists(self) -> bool:
        """Check if instance directories exist."""
        return self.instance.stack_dir.exists() and self.instance.data_root.exists()
    
    def check_docker(self) -> bool:
        """Check if Docker is available."""
        try:
            subprocess.run(
                ["docker", "version"],
                capture_output=True,
                check=True,
                timeout=5
            )
            return True
        except Exception:
            return False
    
    def check_compose_file(self) -> bool:
        """Check if compose file exists."""
        return self.instance.compose_file.exists()
    
    def check_env_file(self) -> bool:
        """Check if env file exists."""
        return self.instance.env_file.exists()
    
    def check_data_dirs(self) -> bool:
        """Check if data directories exist."""
        dirs = ["data", "media", "export", "consume"]
        return all((self.instance.data_root / d).exists() for d in dirs)
    
    def check_containers(self) -> bool:
        """Check if containers are running."""
        return self.instance.is_running
    
    def check_rclone(self) -> bool:
        """Check if rclone is installed."""
        try:
            subprocess.run(
                ["rclone", "version"],
                capture_output=True,
                check=True,
                timeout=5
            )
            return True
        except Exception:
            return False
    
    def check_backup_remote(self) -> bool:
        """Check if backup remote is accessible."""
        try:
            remote_name = self.instance.get_env_value("RCLONE_REMOTE_NAME", "pcloud")
            subprocess.run(
                ["rclone", "about", f"{remote_name}:"],
                capture_output=True,
                check=True,
                timeout=10
            )
            return True
        except Exception:
            return False
    
    def print_report(self) -> None:
        """Print a health check report."""
        print_header(f"Health Check: {self.instance.name}")
        checks = self.check_all()
        
        for check_name, passed in checks.items():
            status = colorize("✓ PASS", Colors.GREEN) if passed else colorize("✗ FAIL", Colors.RED)
            print(f"  {check_name:<30} {status}")
        
        print()
        total = len(checks)
        passed_count = sum(checks.values())
        if passed_count == total:
            ok(f"All {total} checks passed!")
        else:
            warn(f"{passed_count}/{total} checks passed")


# ─── Main Application ─────────────────────────────────────────────────────────

class PaperlessManager:
    """Main application controller."""
    
    # Standard library paths for restore operations
    LIB_PATH_INSTALLED = Path("/usr/local/lib/paperless-bulletproof/lib")
    LIB_PATH_DEV = Path(__file__).parent  # For development
    
    def __init__(self):
        self.instance_manager = InstanceManager()
        self.rclone_configured = self._check_rclone_connection()
        # Determine correct lib path (installed or development)
        self.lib_path = self.LIB_PATH_INSTALLED if self.LIB_PATH_INSTALLED.exists() else self.LIB_PATH_DEV
    
    def _check_rclone_connection(self) -> bool:
        """Check if pCloud/rclone is configured."""
        try:
            result = subprocess.run(
                ["rclone", "listremotes"],
                capture_output=True,
                text=True,
                check=False
            )
            return "pcloud:" in result.stdout
        except Exception:
            return False
    
    def run(self) -> None:
        """Run the main menu loop."""
        while True:
            self._scan_system()
            self.show_main_menu()
            choice = get_input("Select option", "")
            
            if choice == "0":
                print("\nGoodbye! 👋\n")
                break
            
            self.handle_main_choice(choice)
    
    def _scan_system(self) -> None:
        """Scan for instances and check backup connection."""
        # Reload instances to pick up any changes
        self.instance_manager.load_instances()
        
        # Check backup connection
        self.rclone_configured = self._check_rclone_connection()
    
    def show_main_menu(self) -> None:
        """Display the main menu."""
        print_header("Paperless-NGX Bulletproof Manager")
        
        # Show backup connection status
        instances = self.instance_manager.list_instances()
        running_count = sum(1 for i in instances if i.is_running)
        stopped_count = len(instances) - running_count
        
        # System overview box - use centralized helper
        box_line, box_width = create_box_helper(58)
        
        print(draw_box_top(box_width))
        
        if self.rclone_configured:
            # Get backup info (count only instance folders with at least one snapshot)
            try:
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                instance_dirs = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
                backed_up_count = 0
                for inst_dir in instance_dirs:
                    check = subprocess.run(
                        ["rclone", "lsd", f"pcloud:backups/paperless/{inst_dir}"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5
                    )
                    if check.stdout.strip():
                        backed_up_count += 1
                
                # Get most recent backup date across all instances
                latest_backup = "none"
                for inst in instances:
                    backup_mgr = BackupManager(inst)
                    snaps = backup_mgr.fetch_snapshots()
                    if snaps:
                        latest_backup = snaps[-1][0][:16]  # Just date/time
                        break
                
                backup_status = colorize("✓ Connected", Colors.GREEN)
                backup_detail = f"{backed_up_count} backed up"
                if latest_backup != "none":
                    backup_detail += f" • {latest_backup}"
            except:
                backup_status = colorize("✓ Connected", Colors.GREEN)
                backup_detail = "Ready"
        else:
            backup_status = colorize("⚠ Not connected", Colors.YELLOW)
            backup_detail = "Configure to enable backups"
        
        print(box_line(f" Backup Server:  {backup_status} {backup_detail}"))
        
        # Instances status
        if instances:
            instance_status = f"{running_count} running, {stopped_count} stopped"
            print(box_line(f" Instances:      {len(instances)} total • {instance_status}"))
        else:
            print(box_line(f" Instances:      {colorize('No instances configured', Colors.YELLOW)}"))
        
        # Networking services status
        # Traefik
        from lib.installer.traefik import is_traefik_running, get_traefik_email
        traefik_running = is_traefik_running()
        if traefik_running:
            email = get_traefik_email()
            if email:
                traefik_status = f"{colorize('✓', Colors.GREEN)} Running • {email}"
            else:
                traefik_status = f"{colorize('✓', Colors.GREEN)} Running"
        else:
            traefik_status = colorize("○ Not installed", Colors.CYAN)
        print(box_line(f" Traefik:        {traefik_status}"))
        
        # Cloudflare Tunnel
        from lib.installer.cloudflared import is_cloudflared_installed
        if is_cloudflared_installed():
            # Count tunnels
            try:
                from lib.installer.cloudflared import list_tunnels
                tunnels = list_tunnels()
                tunnel_count = len([t for t in tunnels if t.get('name', '').startswith('paperless-')])
                cloudflared_status = f"{colorize('✓', Colors.GREEN)} Installed • {tunnel_count} tunnel{'s' if tunnel_count != 1 else ''}"
            except:
                cloudflared_status = f"{colorize('✓', Colors.GREEN)} Installed"
        else:
            cloudflared_status = colorize("○ Not installed", Colors.CYAN)
        print(box_line(f" Cloudflare:     {cloudflared_status}"))
        
        # Tailscale
        from lib.installer.tailscale import is_tailscale_installed, is_connected, get_ip
        if is_tailscale_installed():
            if is_connected():
                try:
                    ip = get_ip()
                    tailscale_status = f"{colorize('✓', Colors.GREEN)} Connected • {ip}"
                except:
                    tailscale_status = f"{colorize('✓', Colors.GREEN)} Connected"
            else:
                tailscale_status = f"{colorize('○', Colors.YELLOW)} Installed • Disconnected"
        else:
            tailscale_status = colorize("○ Not installed", Colors.CYAN)
        print(box_line(f" Tailscale:      {tailscale_status}"))
        
        print(draw_box_bottom(box_width))
        print()
        
        # Quick instance list
        if instances:
            print(colorize("Active Instances:", Colors.BOLD))
            for instance in instances[:5]:  # Show max 5
                status_icon = colorize("●", Colors.GREEN) if instance.is_running else colorize("○", Colors.YELLOW)
                url = instance.get_access_url()
                # Format: status icon, name (fixed 25 chars), then URL
                name_padded = f"{instance.name:<25}"
                print(f"  {status_icon} {colorize(name_padded, Colors.BOLD)} {url}")
            
            if len(instances) > 5:
                print(f"  {colorize(f'... and {len(instances) - 5} more', Colors.CYAN)}")
            print()
        
        # Main menu options
        options = [
            ("1", colorize("▸", Colors.GREEN) + " Manage Instances" + (f" ({len(instances)})" if instances else "")),
            ("2", colorize("▸", Colors.BLUE) + " Browse Backups" + (" ✓" if self.rclone_configured else " ⚠")),
            ("3", colorize("▸", Colors.MAGENTA) + " System Backup/Restore"),
            ("4", colorize("▸", Colors.CYAN) + " Manage Traefik (HTTPS)"),
            ("5", colorize("▸", Colors.CYAN) + " Manage Cloudflare Tunnel"),
            ("6", colorize("▸", Colors.CYAN) + " Manage Tailscale"),
            ("7", colorize("▸", Colors.YELLOW) + " Configure Backup Server"),
            ("8", colorize("▸", Colors.RED) + " Nuke Setup (Clean Start)"),
            ("0", colorize("◀", Colors.RED) + " Quit")
        ]
        print_menu(options)
    
    def handle_main_choice(self, choice: str) -> None:
        """Handle main menu selection."""
        if choice == "1":
            self.instances_menu()
        elif choice == "2":
            if not self.rclone_configured:
                warn("Backup server not configured!")
                if confirm("Configure now?", True):
                    self.configure_backup_connection()
            else:
                self.backups_menu()
        elif choice == "3":
            self.system_backup_menu()
        elif choice == "4":
            self.traefik_menu()
        elif choice == "5":
            self.cloudflared_menu()
        elif choice == "6":
            self.tailscale_menu()
        elif choice == "7":
            self.configure_backup_connection()
        elif choice == "8":
            self.nuke_setup()
        else:
            warn("Invalid option")
    
    def configure_backup_connection(self) -> None:
        """Configure rclone cloud backup connection with guided setup."""
        while True:
            print_header("Backup Server Configuration")
            
            box_line, box_width = create_box_helper(60)
            
            # Check current status
            current_remote = None
            remote_type = None
            remote_ok = False
            
            try:
                result = subprocess.run(
                    ["rclone", "listremotes"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0:
                    remotes = [r.strip().rstrip(':') for r in result.stdout.splitlines() if r.strip()]
                    if remotes:
                        current_remote = remotes[0]
                        # Get remote type
                        result = subprocess.run(
                            ["rclone", "config", "show", current_remote],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if result.returncode == 0:
                            for line in result.stdout.splitlines():
                                if line.startswith("type = "):
                                    remote_type = line.split("=")[1].strip()
                                    break
                        # Check if working
                        result = subprocess.run(
                            ["rclone", "about", f"{current_remote}:", "--json"],
                            capture_output=True,
                            text=True,
                            timeout=15,
                            check=False
                        )
                        remote_ok = result.returncode == 0
            except:
                pass
            
            # Display info box
            print(draw_box_top(box_width))
            print(box_line(" Backups are stored in the cloud using rclone, which"))
            print(box_line(" supports 70+ cloud storage providers including:"))
            print(box_line(""))
            print(box_line(f"   • {colorize('pCloud', Colors.CYAN)} - Great value, EU/US servers"))
            print(box_line(f"   • {colorize('Google Drive', Colors.CYAN)} - 15GB free"))
            print(box_line(f"   • {colorize('Dropbox', Colors.CYAN)} - 2GB free"))
            print(box_line(f"   • {colorize('OneDrive', Colors.CYAN)} - 5GB free"))
            print(box_line(f"   • {colorize('Backblaze B2', Colors.CYAN)} - 10GB free, cheap storage"))
            print(box_line(f"   • {colorize('Amazon S3', Colors.CYAN)} - Enterprise scalable"))
            print(box_line(f"   • {colorize('SFTP/WebDAV', Colors.CYAN)} - Self-hosted options"))
            print(box_line(""))
            print(draw_section_header("Current Status", box_width))
            
            if current_remote and remote_ok:
                status_icon = colorize("● Connected", Colors.GREEN)
                print(box_line(f" Status:  {status_icon}"))
                print(box_line(f" Remote:  {colorize(current_remote, Colors.CYAN)} ({remote_type or 'unknown'})"))
                
                # Try to get usage info
                try:
                    result = subprocess.run(
                        ["rclone", "about", f"{current_remote}:", "--json"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False
                    )
                    if result.returncode == 0:
                        import json as json_module
                        about = json_module.loads(result.stdout)
                        if "used" in about and "total" in about:
                            used_gb = about["used"] / (1024**3)
                            total_gb = about["total"] / (1024**3)
                            pct = (about["used"] / about["total"]) * 100 if about["total"] > 0 else 0
                            print(box_line(f" Storage: {used_gb:.1f} GB / {total_gb:.1f} GB ({pct:.0f}% used)"))
                except:
                    pass
            elif current_remote:
                status_icon = colorize("● Configured but not responding", Colors.YELLOW)
                print(box_line(f" Status:  {status_icon}"))
                print(box_line(f" Remote:  {colorize(current_remote, Colors.CYAN)} ({remote_type or 'unknown'})"))
            else:
                status_icon = colorize("○ Not configured", Colors.RED)
                print(box_line(f" Status:  {status_icon}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # Menu options
            if current_remote and remote_ok:
                print(f"  {colorize('1)', Colors.BOLD)} Test connection")
                print(f"  {colorize('2)', Colors.BOLD)} View storage usage")
                print(f"  {colorize('3)', Colors.BOLD)} Change backup provider")
                print(f"  {colorize('4)', Colors.BOLD)} Remove configuration")
            else:
                print(f"  {colorize('1)', Colors.BOLD)} {colorize('Set up pCloud', Colors.CYAN)} {colorize('(recommended)', Colors.GREEN)}")
                print(f"  {colorize('2)', Colors.BOLD)} Set up Google Drive")
                print(f"  {colorize('3)', Colors.BOLD)} Set up Dropbox")
                print(f"  {colorize('4)', Colors.BOLD)} Set up other provider (advanced)")
            
            print()
            print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            
            if current_remote and remote_ok:
                # Already configured menu
                if choice == "1":
                    self._test_backup_connection(current_remote)
                elif choice == "2":
                    self._show_storage_usage(current_remote)
                elif choice == "3":
                    if confirm("Replace current backup configuration?", False):
                        self._setup_backup_provider_menu()
                elif choice == "4":
                    if confirm(f"Remove '{current_remote}' configuration? Backups will stop working.", False):
                        subprocess.run(["rclone", "config", "delete", current_remote], check=False)
                        ok("Configuration removed")
                        self.rclone_configured = False
                        input("\nPress Enter to continue...")
            else:
                # Not configured menu
                if choice == "1":
                    self._setup_pcloud()
                elif choice == "2":
                    self._setup_google_drive()
                elif choice == "3":
                    self._setup_dropbox()
                elif choice == "4":
                    self._setup_other_provider()
            
            # Refresh connection status
            self.rclone_configured = self._check_rclone_connection()
    
    def _test_backup_connection(self, remote: str) -> None:
        """Test the backup connection."""
        print()
        say("Testing connection...")
        
        try:
            result = subprocess.run(
                ["rclone", "lsd", f"{remote}:"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            if result.returncode == 0:
                ok("Connection successful!")
                dirs = [line.split()[-1] for line in result.stdout.splitlines() if line.strip()]
                if dirs:
                    say(f"Found {len(dirs)} top-level folders")
            else:
                error(f"Connection failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            error("Connection timed out")
        except Exception as e:
            error(f"Test failed: {e}")
        
        input("\nPress Enter to continue...")
    
    def _show_storage_usage(self, remote: str) -> None:
        """Show storage usage details."""
        print()
        say("Fetching storage information...")
        
        try:
            result = subprocess.run(
                ["rclone", "about", f"{remote}:"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            if result.returncode == 0:
                print()
                print(result.stdout)
            else:
                warn("Could not fetch storage info")
        except Exception as e:
            error(f"Failed: {e}")
        
        input("\nPress Enter to continue...")
    
    def _setup_backup_provider_menu(self) -> None:
        """Show provider selection menu."""
        print()
        print(f"  {colorize('1)', Colors.BOLD)} {colorize('pCloud', Colors.CYAN)} {colorize('(recommended)', Colors.GREEN)}")
        print(f"  {colorize('2)', Colors.BOLD)} Google Drive")
        print(f"  {colorize('3)', Colors.BOLD)} Dropbox")
        print(f"  {colorize('4)', Colors.BOLD)} Other provider")
        print()
        
        choice = get_input("Select provider", "")
        
        if choice == "1":
            self._setup_pcloud()
        elif choice == "2":
            self._setup_google_drive()
        elif choice == "3":
            self._setup_dropbox()
        elif choice == "4":
            self._setup_other_provider()
    
    def _setup_pcloud(self) -> None:
        """Guided pCloud setup."""
        print()
        box_line, box_width = create_box_helper(60)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('pCloud Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" pCloud offers excellent value with lifetime plans and"))
        print(box_line(" servers in both EU and US regions."))
        print(box_line(""))
        print(box_line(f" {colorize('Step 1:', Colors.CYAN)} On any computer with a browser, run:"))
        print(box_line(""))
        print(box_line(f"   {colorize('rclone authorize \"pcloud\"', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 2:', Colors.CYAN)} Log in to pCloud in the browser"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 3:', Colors.CYAN)} Copy the token JSON that appears"))
        print(draw_box_bottom(box_width))
        print()
        
        token = get_input("Paste token JSON (or 'cancel' to go back)", "")
        
        if token.lower() == "cancel" or not token:
            return
        
        # Validate JSON
        try:
            import json as json_module
            json_module.loads(token)
        except:
            error("Invalid JSON format. Make sure you copy the entire token.")
            input("\nPress Enter to continue...")
            return
        
        say("Configuring pCloud remote...")
        
        # Try EU region first, then US
        for host, region in [("eapi.pcloud.com", "EU"), ("api.pcloud.com", "US")]:
            subprocess.run(["rclone", "config", "delete", "pcloud"], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([
                "rclone", "config", "create", "pcloud", "pcloud",
                "token", token, "hostname", host, "--non-interactive"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Test connection
            result = subprocess.run(
                ["rclone", "about", "pcloud:", "--json"],
                capture_output=True,
                timeout=15,
                check=False
            )
            if result.returncode == 0:
                ok(f"pCloud configured successfully ({region} region)")
                self.rclone_configured = True
                input("\nPress Enter to continue...")
                return
        
        error("Failed to connect with provided token. Please try again.")
        input("\nPress Enter to continue...")
    
    def _setup_google_drive(self) -> None:
        """Guided Google Drive setup."""
        print()
        box_line, box_width = create_box_helper(60)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Google Drive Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" Google Drive offers 15GB free storage."))
        print(box_line(""))
        print(box_line(f" {colorize('Step 1:', Colors.CYAN)} On any computer with a browser, run:"))
        print(box_line(""))
        print(box_line(f"   {colorize('rclone authorize \"drive\"', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 2:', Colors.CYAN)} Log in to Google in the browser"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 3:', Colors.CYAN)} Copy the token JSON that appears"))
        print(draw_box_bottom(box_width))
        print()
        
        token = get_input("Paste token JSON (or 'cancel' to go back)", "")
        
        if token.lower() == "cancel" or not token:
            return
        
        try:
            import json as json_module
            json_module.loads(token)
        except:
            error("Invalid JSON format.")
            input("\nPress Enter to continue...")
            return
        
        say("Configuring Google Drive remote...")
        
        subprocess.run(["rclone", "config", "delete", "pcloud"], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([
            "rclone", "config", "create", "pcloud", "drive",
            "token", token, "--non-interactive"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        result = subprocess.run(
            ["rclone", "about", "pcloud:", "--json"],
            capture_output=True,
            timeout=15,
            check=False
        )
        if result.returncode == 0:
            ok("Google Drive configured successfully")
            self.rclone_configured = True
        else:
            error("Failed to connect. Please try again.")
        
        input("\nPress Enter to continue...")
    
    def _setup_dropbox(self) -> None:
        """Guided Dropbox setup."""
        print()
        box_line, box_width = create_box_helper(60)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Dropbox Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" Dropbox offers 2GB free storage."))
        print(box_line(""))
        print(box_line(f" {colorize('Step 1:', Colors.CYAN)} On any computer with a browser, run:"))
        print(box_line(""))
        print(box_line(f"   {colorize('rclone authorize \"dropbox\"', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 2:', Colors.CYAN)} Log in to Dropbox in the browser"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 3:', Colors.CYAN)} Copy the token JSON that appears"))
        print(draw_box_bottom(box_width))
        print()
        
        token = get_input("Paste token JSON (or 'cancel' to go back)", "")
        
        if token.lower() == "cancel" or not token:
            return
        
        try:
            import json as json_module
            json_module.loads(token)
        except:
            error("Invalid JSON format.")
            input("\nPress Enter to continue...")
            return
        
        say("Configuring Dropbox remote...")
        
        subprocess.run(["rclone", "config", "delete", "pcloud"], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([
            "rclone", "config", "create", "pcloud", "dropbox",
            "token", token, "--non-interactive"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        result = subprocess.run(
            ["rclone", "about", "pcloud:", "--json"],
            capture_output=True,
            timeout=15,
            check=False
        )
        if result.returncode == 0:
            ok("Dropbox configured successfully")
            self.rclone_configured = True
        else:
            error("Failed to connect. Please try again.")
        
        input("\nPress Enter to continue...")
    
    def _setup_other_provider(self) -> None:
        """Advanced setup for other rclone providers."""
        print()
        box_line, box_width = create_box_helper(60)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Advanced Provider Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" rclone supports 70+ cloud providers. For full list:"))
        print(box_line(f"   {colorize('https://rclone.org/overview/', Colors.CYAN)}"))
        print(box_line(""))
        print(box_line(" Common options:"))
        print(box_line("   • Backblaze B2  - Cheap object storage"))
        print(box_line("   • Amazon S3     - Enterprise storage"))
        print(box_line("   • SFTP          - Any SSH server"))
        print(box_line("   • WebDAV        - Nextcloud, ownCloud, etc."))
        print(box_line("   • FTP           - Legacy servers"))
        print(box_line(""))
        print(box_line(" To configure manually, run:"))
        print(box_line(f"   {colorize('rclone config', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Important:', Colors.RED)} Name your remote 'pcloud' for"))
        print(box_line(" compatibility with this system."))
        print(draw_box_bottom(box_width))
        print()
        
        if confirm("Launch rclone interactive config?", True):
            print()
            say("Starting rclone config... Create a remote named 'pcloud'")
            print()
            subprocess.run(["rclone", "config"], check=False)
            
            # Check if it worked
            self.rclone_configured = self._check_rclone_connection()
            if self.rclone_configured:
                ok("Remote configured successfully!")
            else:
                warn("Remote not detected. Make sure it's named 'pcloud'.")
        
        input("\nPress Enter to continue...")
    
    def instances_menu(self) -> None:
        """Instances management menu."""
        while True:
            instances = self.instance_manager.list_instances()
            
            print_header("Instances")
            
            if instances:
                for idx, instance in enumerate(instances, 1):
                    status = colorize("Running", Colors.GREEN) if instance.is_running else colorize("Stopped", Colors.YELLOW)
                    access_urls = instance.get_access_urls()
                    print(f"  {idx}) {instance.name} [{status}]")
                    if len(access_urls) == 1:
                        print(f"      Access: {access_urls[0][0]}: {access_urls[0][1]}")
                    else:
                        print(f"      Access:")
                        for mode_label, url in access_urls:
                            print(f"        {mode_label}: {url}")
                    print(f"      Stack:  {instance.stack_dir}")
                    print(f"      Data:   {instance.data_root}")
                print()
            else:
                print("  No instances configured\n")
            
            # Build options with numbers for instances first, then operations
            options = []
            for idx in range(1, len(instances) + 1):
                options.append((str(idx), f"Manage '{instances[idx-1].name}'"))
            
            next_num = len(instances) + 1
            options.append((str(next_num), "Add new instance"))
            options.append((str(next_num + 1), "Delete all instances"))
            options.append(("0", "Back to main menu"))
            
            print_menu(options)
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == str(next_num):
                self.add_instance_menu()
            elif choice == str(next_num + 1):
                print()
                warn(f"This will DELETE all {len(instances)} instances completely!")
                print("  • All instance directories")
                print("  • All Docker containers")
                print("  • All data and configurations")
                print("  • All Cloudflared services")
                print()
                
                if confirm("Delete ALL instances and their files?", False):
                    confirmation = get_input("Type 'DELETE ALL' to confirm", "")
                    if confirmation == "DELETE ALL":
                        for inst in instances:
                            self.instance_manager.remove_instance(inst.name, delete_files=True)
                        ok(f"All {len(instances)} instances completely deleted")
                        input("\nPress Enter to continue...")
                    else:
                        warn("Deletion cancelled")
            elif choice.isdigit() and 1 <= int(choice) <= len(instances):
                self.instance_detail_menu(instances[int(choice) - 1])
            else:
                warn("Invalid option")
    
    def traefik_menu(self) -> None:
        """Traefik management menu."""
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import traefik
        
        while True:
            print_header("Manage Traefik (HTTPS)")
            
            # Check Traefik status
            is_running = traefik.is_traefik_running()
            configured_email = traefik.get_traefik_email()
            
            if is_running:
                say(colorize("✓ System Traefik is running", Colors.GREEN))
                if configured_email:
                    print(f"Let's Encrypt Email: {colorize(configured_email, Colors.CYAN)}")
                print()
                print("Traefik provides HTTPS routing for all instances.")
                print("Each instance with Traefik enabled will automatically")
                print("get SSL certificates and HTTPS access via its domain.")
                print()
            else:
                say(colorize("⚠ System Traefik is not running", Colors.YELLOW))
                print()
                print("Install Traefik to enable HTTPS for instances.")
                print()
            
            options = []
            if is_running:
                options.extend([
                    ("1", "View Traefik status"),
                    ("2", "Update Let's Encrypt email"),
                    ("3", "Restart Traefik"),
                    ("4", "Stop and remove Traefik"),
                ])
            else:
                options.append(("1", "Install and start Traefik"))
            
            options.append(("0", "Back to main menu"))
            
            print_menu(options)
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if is_running:
                    # Show status
                    import subprocess
                    try:
                        subprocess.run(["docker", "ps", "--filter", "name=traefik-system"], check=True)
                        subprocess.run(["docker", "logs", "--tail", "50", "traefik-system"], check=True)
                    except:
                        warn("Failed to get Traefik status")
                    input("\nPress Enter to continue...")
                else:
                    # Install
                    while True:
                        email = get_input("Let's Encrypt email for SSL certificates", "admin@example.com")
                        if traefik.validate_email(email):
                            break
                        error(f"Invalid email format: {email}")
                        say("Please enter a valid email address (e.g., admin@example.com)")
                    
                    if traefik.setup_system_traefik(email):
                        ok("Traefik installed and started successfully!")
                        say("You can now create instances with HTTPS enabled")
                    else:
                        error("Failed to install Traefik")
                    input("\nPress Enter to continue...")
            elif choice == "2" and is_running:
                # Update email
                current = configured_email or "admin@example.com"
                say(f"Current email: {current}")
                while True:
                    email = get_input("New Let's Encrypt email", current)
                    if traefik.validate_email(email):
                        break
                    error(f"Invalid email format: {email}")
                    say("Please enter a valid email address (e.g., admin@example.com)")
                
                if confirm("Restart Traefik with new email?", True):
                    traefik.stop_system_traefik()
                    if traefik.setup_system_traefik(email):
                        ok("Traefik restarted with new email")
                    else:
                        error("Failed to restart Traefik")
                input("\nPress Enter to continue...")
            elif choice == "3" and is_running:
                # Restart
                import subprocess
                try:
                    subprocess.run(["docker", "restart", "traefik-system"], check=True)
                    ok("Traefik restarted")
                except:
                    warn("Failed to restart Traefik")
                input("\nPress Enter to continue...")
            elif choice == "4" and is_running:
                # Stop and remove
                if confirm("Stop and remove Traefik? All HTTPS instances will become unavailable.", False):
                    traefik.stop_system_traefik()
                    ok("Traefik stopped and removed")
                    warn("Existing instances with HTTPS will not be accessible until Traefik is reinstalled")
                input("\nPress Enter to continue...")
    
    def add_instance_menu(self) -> None:
        """Add new instance submenu with modern styling."""
        print_header("Add New Instance")
        
        box_line, box_width = create_box_helper(60)
        
        print(draw_box_top(box_width))
        print(box_line(" Choose how to create your new instance:"))
        print(box_line(""))
        print(box_line(f"   {colorize('1)', Colors.BOLD)} {colorize('Create fresh instance', Colors.CYAN)}"))
        print(box_line("      Start with a clean Paperless installation"))
        print(box_line(""))
        print(box_line(f"   {colorize('2)', Colors.BOLD)} {colorize('Restore from backup', Colors.CYAN)}"))
        print(box_line("      Restore documents and settings from cloud backup"))
        print(draw_box_bottom(box_width))
        print()
        print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
        print()
        
        choice = get_input("Select option", "")
        
        if choice == "1":
            self.create_fresh_instance()
        elif choice == "2":
            self.restore_instance_from_backup()
        # else back (0 or any other)
    
    def restore_instance_from_backup(self, backup_instance: str = None, snapshot: str = None) -> None:
        """Restore an instance from cloud backup with guided setup.
        
        Flow:
        1. Select backup source and snapshot
        2. Download and parse backup's .env to get original settings
        3. Detect conflicts (ports, names, paths)
        4. Walk through settings, allowing changes and forcing where conflicts exist
        5. Restore data with merged config
        
        Args:
            backup_instance: Name of the backup instance to restore from (prompts if None)
            snapshot: Snapshot name to restore (prompts if None)
        """
        if not self.rclone_configured:
            error("Backup server not configured!")
            say("Configure from main menu: Configure Backup Server")
            input("\nPress Enter to continue...")
            return
        
        print_header("Restore from Backup")
        
        # Get existing instances for validation
        existing_instances = [i.name for i in self.instance_manager.list_instances()]
        
        # Check networking availability
        net_status = check_networking_dependencies()
        
        # Get rclone remote settings
        remote_name = "pcloud"  # TODO: make configurable
        remote_base = f"{remote_name}:backups/paperless"
        
        box_line, box_width = create_box_helper(60)
        
        try:
            # ─── Step 1: Select Backup Source ─────────────────────────────────
            print(colorize("Step 1 of 4: Select Backup", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Select backup instance if not provided
            if not backup_instance:
                say("Scanning backup server...")
                
                result = subprocess.run(
                    ["rclone", "lsd", remote_base],
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn("No backups found on server")
                    input("\nPress Enter to continue...")
                    return
                
                # Parse instance names
                backup_instances = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if parts:
                        backup_instances.append(parts[-1])
                
                if not backup_instances:
                    warn("No backup instances found")
                    input("\nPress Enter to continue...")
                    return
                
                # Show available instances in a nice format
                print(draw_box_top(box_width))
                print(box_line(f" {colorize('Available Backups', Colors.BOLD)}"))
                print(box_line(""))
                for idx, inst_name in enumerate(backup_instances, 1):
                    print(box_line(f"   {colorize(str(idx) + ')', Colors.BOLD)} {inst_name}"))
                print(draw_box_bottom(box_width))
                print()
                
                selected = get_input(f"Select backup [1-{len(backup_instances)}] or 'cancel'", "cancel")
                
                if selected.lower() == "cancel" or not selected.isdigit():
                    return
                
                idx = int(selected)
                if not (1 <= idx <= len(backup_instances)):
                    warn("Invalid selection")
                    return
                
                backup_instance = backup_instances[idx - 1]
            
            # Select snapshot
            if not snapshot:
                say(f"Loading snapshots for '{backup_instance}'...")
                
                result = subprocess.run(
                    ["rclone", "lsd", f"{remote_base}/{backup_instance}"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn(f"No snapshots found for {backup_instance}")
                    input("\nPress Enter to continue...")
                    return
                
                snapshots = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if parts:
                        snapshots.append(parts[-1])
                
                snapshots = sorted(snapshots)
                
                print()
                print(draw_box_top(box_width))
                print(box_line(f" {colorize('Available Snapshots', Colors.BOLD)} ({backup_instance})"))
                print(box_line(""))
                for idx, snap in enumerate(snapshots, 1):
                    # Parse date from snapshot name (format: YYYY-MM-DD_HH-MM-SS)
                    display = snap
                    try:
                        date_part = snap.split("_")[0]
                        time_part = snap.split("_")[1].replace("-", ":")
                        display = f"{date_part} {time_part}"
                    except:
                        pass
                    latest_marker = colorize(" (latest)", Colors.GREEN) if idx == len(snapshots) else ""
                    print(box_line(f"   {colorize(str(idx) + ')', Colors.BOLD)} {display}{latest_marker}"))
                print(draw_box_bottom(box_width))
                print()
                
                snap_choice = get_input(f"Select snapshot [1-{len(snapshots)}] or 'latest'", "latest")
                
                if snap_choice.lower() == "latest":
                    snapshot = snapshots[-1]
                elif snap_choice.isdigit() and 1 <= int(snap_choice) <= len(snapshots):
                    snapshot = snapshots[int(snap_choice) - 1]
                else:
                    warn("Invalid selection")
                    return
            
            ok(f"Selected: {backup_instance}/{snapshot}")
            print()
            
            # ─── Step 2: Load Backup Configuration ────────────────────────────
            print(colorize("Step 2 of 4: Review Backup Settings", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            say("Downloading backup configuration...")
            
            # Download the .env from the backup to see original settings
            result = subprocess.run(
                ["rclone", "cat", f"{remote_base}/{backup_instance}/{snapshot}/.env"],
                capture_output=True,
                text=True,
                check=False
            )
            
            backup_env = {}
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        backup_env[k.strip()] = v.strip()
                ok("Loaded backup configuration")
            else:
                warn("Could not load backup .env - will use defaults")
            
            # Import installer modules
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import common, files, traefik, cloudflared, tailscale
            from lib.installer.common import get_next_available_port
            
            # ─── Detect Conflicts ─────────────────────────────────────────────
            # Check what ports are in use (use bind() method like get_next_available_port)
            original_port = backup_env.get("HTTP_PORT", "8000")
            port_conflict = False
            try:
                import socket
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', int(original_port)))
                # If we get here, port is available
                port_conflict = False
            except OSError:
                # Port is in use
                port_conflict = True
            
            # Check if instance name conflicts with REGISTERED instances
            original_name = backup_env.get("INSTANCE_NAME", backup_instance)
            name_conflict = original_name in existing_instances
            
            # Check if paths exist (separate from name conflict - paths may exist without registration)
            original_data_root = backup_env.get("DATA_ROOT", f"/home/docker/{original_name}")
            original_stack_dir = backup_env.get("STACK_DIR", f"/home/docker/{original_name}-setup")
            path_conflict = Path(original_data_root).exists() or Path(original_stack_dir).exists()
            
            # Show backup configuration with conflict warnings
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Backup Configuration', Colors.BOLD)}"))
            print(draw_box_divider(box_width))
            
            # Instance name
            name_status = colorize(" ⚠ CONFLICT", Colors.RED) if name_conflict else ""
            print(box_line(f" Instance:  {original_name}{name_status}"))
            
            # Credentials (from backup)
            admin_user = backup_env.get("PAPERLESS_ADMIN_USER", "admin")
            print(box_line(f" Admin:     {admin_user}"))
            print(box_line(f" Timezone:  {backup_env.get('TZ', 'UTC')}"))
            
            # Database
            print(box_line(f" Database:  {backup_env.get('POSTGRES_DB', 'paperless')}"))
            
            print(draw_box_divider(box_width))
            
            # Network settings
            print(box_line(f" {colorize('Network Settings:', Colors.BOLD)}"))
            
            port_status = colorize(" ⚠ IN USE", Colors.RED) if port_conflict else ""
            print(box_line(f" Port:      {original_port}{port_status}"))
            
            original_traefik = backup_env.get("ENABLE_TRAEFIK", "no")
            original_cloudflared = backup_env.get("ENABLE_CLOUDFLARED", "no")
            original_domain = backup_env.get("DOMAIN", "")
            
            if original_traefik == "yes":
                print(box_line(f" Access:    HTTPS via Traefik"))
                print(box_line(f" Domain:    {original_domain}"))
            elif original_cloudflared == "yes":
                print(box_line(f" Access:    Cloudflare Tunnel"))
                print(box_line(f" Domain:    {original_domain}"))
            else:
                print(box_line(f" Access:    Direct HTTP"))
            
            if path_conflict:
                print(draw_box_divider(box_width))
                print(box_line(f" {colorize('⚠ Paths already exist - will use new paths', Colors.YELLOW)}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # ─── Step 3: Configure Instance ───────────────────────────────────
            print(colorize("Step 3 of 4: Configure Instance", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Instance name - force change if conflict
            if name_conflict:
                warn(f"Instance name '{original_name}' conflicts with existing registered instance")
                suggested_name = f"{original_name}-restored"
            elif path_conflict:
                warn(f"Paths for '{original_name}' already exist - using new name")
                suggested_name = f"{original_name}-restored"
            else:
                suggested_name = original_name
            
            while True:
                new_name = get_input("Instance name", suggested_name)
                
                if new_name in existing_instances:
                    warn(f"Instance '{new_name}' already exists - choose another name")
                    suggested_name = f"{new_name}-2"
                    continue
                
                if not new_name or not new_name.replace("-", "").replace("_", "").isalnum():
                    warn("Name must be alphanumeric (hyphens and underscores allowed)")
                    continue
                
                # Check if new paths would conflict
                new_data_root = f"/home/docker/{new_name}"
                new_stack_dir = f"/home/docker/{new_name}-setup"
                if Path(new_data_root).exists() or Path(new_stack_dir).exists():
                    warn(f"Paths for '{new_name}' already exist - choose another name")
                    continue
                
                break
            
            # Set up paths
            common.cfg.instance_name = new_name
            common.cfg.data_root = f"/home/docker/{new_name}"
            common.cfg.stack_dir = f"/home/docker/{new_name}-setup"
            common.cfg.rclone_remote_name = remote_name
            common.cfg.rclone_remote_path = f"backups/paperless/{new_name}"
            common.cfg.refresh_paths()
            
            # Load settings from backup
            common.cfg.tz = backup_env.get("TZ", common.cfg.tz)
            common.cfg.puid = backup_env.get("PUID", common.cfg.puid)
            common.cfg.pgid = backup_env.get("PGID", common.cfg.pgid)
            common.cfg.paperless_admin_user = backup_env.get("PAPERLESS_ADMIN_USER", common.cfg.paperless_admin_user)
            common.cfg.paperless_admin_password = backup_env.get("PAPERLESS_ADMIN_PASSWORD", common.cfg.paperless_admin_password)
            common.cfg.postgres_db = backup_env.get("POSTGRES_DB", common.cfg.postgres_db)
            common.cfg.postgres_user = backup_env.get("POSTGRES_USER", common.cfg.postgres_user)
            common.cfg.postgres_password = backup_env.get("POSTGRES_PASSWORD", common.cfg.postgres_password)
            common.cfg.retention_days = backup_env.get("RETENTION_DAYS", common.cfg.retention_days)
            
            print()
            say(f"Instance '{colorize(new_name, Colors.BOLD)}' paths:")
            print(f"  Data:  {colorize(common.cfg.data_root, Colors.CYAN)}")
            print(f"  Stack: {colorize(common.cfg.stack_dir, Colors.CYAN)}")
            print()
            
            # Network access - show original and allow change
            say("Access method:")
            print()
            
            # Determine what was originally used
            original_access = "1"  # Direct HTTP
            if original_traefik == "yes":
                original_access = "2"
            elif original_cloudflared == "yes":
                original_access = "3"
            
            print(f"  {colorize('1)', Colors.BOLD)} {colorize('Direct HTTP', Colors.CYAN)} - Simple port binding" + 
                  (colorize(" (original)", Colors.GREEN) if original_access == "1" else ""))
            print(f"  {colorize('2)', Colors.BOLD)} {colorize('HTTPS via Traefik', Colors.CYAN)}" + 
                  ("" if net_status["traefik_running"] else colorize(" (not running)", Colors.YELLOW)) +
                  (colorize(" (original)", Colors.GREEN) if original_access == "2" else ""))
            print(f"  {colorize('3)', Colors.BOLD)} {colorize('Cloudflare Tunnel', Colors.CYAN)}" + 
                  ("" if net_status["cloudflared_authenticated"] else colorize(" (not configured)", Colors.YELLOW)) +
                  (colorize(" (original)", Colors.GREEN) if original_access == "3" else ""))
            print()
            
            access_choice = get_input("Choose access method [1-3]", original_access)
            
            # Domain - use original if available, suggest new based on new name
            default_domain = original_domain if original_domain else f"{new_name}.example.com"
            if original_name in default_domain and new_name != original_name:
                default_domain = default_domain.replace(original_name, new_name)
            
            if access_choice == "2":
                common.cfg.enable_traefik = "yes"
                common.cfg.enable_cloudflared = "no"
                common.cfg.domain = get_input("Domain", default_domain)
                
                if not net_status["traefik_running"]:
                    warn("Traefik is not running - HTTPS won't work until configured")
                    if not confirm("Continue anyway?", False):
                        return
                        
            elif access_choice == "3":
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "yes"
                common.cfg.domain = get_input("Domain", default_domain)
                
                if not net_status["cloudflared_authenticated"]:
                    warn("Cloudflare Tunnel not configured")
                    if not confirm("Continue anyway? (tunnel won't be created)", False):
                        return
            else:
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "no"
            
            # Port - force change if conflict
            print()
            if port_conflict:
                warn(f"Port {original_port} is already in use!")
                available_port = get_next_available_port(int(original_port) + 1)
                common.cfg.http_port = get_input("HTTP port (must change)", str(available_port))
                
                # Verify new port isn't also in use
                while True:
                    try:
                        import socket
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1)
                        result = sock.connect_ex(('127.0.0.1', int(common.cfg.http_port)))
                        sock.close()
                        if result == 0:
                            warn(f"Port {common.cfg.http_port} is also in use!")
                            available_port = get_next_available_port(int(common.cfg.http_port) + 1)
                            common.cfg.http_port = get_input("HTTP port", str(available_port))
                        else:
                            break
                    except:
                        break
            else:
                common.cfg.http_port = get_input("HTTP port", original_port)
            
            # Tailscale option
            original_tailscale = backup_env.get("ENABLE_TAILSCALE", "no")
            if net_status["tailscale_connected"]:
                print()
                default_ts = original_tailscale == "yes"
                if confirm("Enable Tailscale access?", default_ts):
                    common.cfg.enable_tailscale = "yes"
                else:
                    common.cfg.enable_tailscale = "no"
            else:
                common.cfg.enable_tailscale = "no"
            
            # Backup schedule - use original or defaults
            common.cfg.cron_full_time = backup_env.get("CRON_FULL_TIME", "30 3 * * 0")
            common.cfg.cron_incr_time = backup_env.get("CRON_INCR_TIME", "0 */6 * * *")
            common.cfg.refresh_paths()
            print()
            
            # ─── Step 4: Restore ──────────────────────────────────────────────
            print(colorize("Step 4 of 4: Restore Data", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Summary before proceeding
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Restore Summary', Colors.BOLD)}"))
            print(box_line(""))
            print(box_line(f" Source:  {backup_instance}/{snapshot}"))
            print(box_line(f" Target:  {colorize(new_name, Colors.CYAN)}"))
            print(box_line(f" Path:    {common.cfg.data_root}"))
            
            if common.cfg.enable_cloudflared == "yes":
                print(box_line(f" Access:  ☁️  https://{common.cfg.domain}"))
            elif common.cfg.enable_traefik == "yes":
                print(box_line(f" Access:  🔒 https://{common.cfg.domain}"))
            else:
                print(box_line(f" Access:  🌐 http://localhost:{common.cfg.http_port}"))
            print(draw_box_bottom(box_width))
            print()
            
            if not confirm("Proceed with restore?", True):
                say("Restore cancelled")
                input("\nPress Enter to continue...")
                return
            
            print()
            
            # Check dependencies
            if common.cfg.enable_traefik == "yes" and not net_status["traefik_running"]:
                warn("Traefik not running - HTTPS won't work until configured")
            if common.cfg.enable_cloudflared == "yes" and not net_status["cloudflared_authenticated"]:
                warn("Cloudflare not configured - tunnel won't be created")
            
            # Create directories
            say("Creating directories...")
            common.ensure_dir_tree(common.cfg)
            ok("Directories created")
            
            # Write config files
            say("Writing configuration...")
            files.write_env_file()
            files.write_compose_file()
            files.copy_helper_scripts()
            ok("Configuration written")
            
            # Restore data
            say(f"Restoring data from backup...")
            
            success = run_restore_with_env(
                snapshot=snapshot,
                instance_name=new_name,
                env_file=Path(common.cfg.env_file),
                compose_file=Path(common.cfg.compose_file),
                stack_dir=Path(common.cfg.stack_dir),
                data_root=Path(common.cfg.data_root),
                rclone_remote_name=remote_name,
                rclone_remote_path=f"backups/paperless/{backup_instance}",
                merge_config=True  # Merge backup settings, keep new instance network/paths
            )
            
            if not success:
                raise Exception("Restore operation failed")
            
            ok("Data restored successfully")
            
            # Install backup cron
            files.install_cron_backup()
            ok("Backup schedule configured")
            
            # Set up Cloudflare tunnel if enabled
            if common.cfg.enable_cloudflared == "yes" and net_status["cloudflared_authenticated"]:
                setup_cloudflare_tunnel(new_name, common.cfg.domain)
            
            # Register instance
            self.instance_manager.add_instance(
                new_name,
                Path(common.cfg.stack_dir),
                Path(common.cfg.data_root)
            )
            
            # Success message
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('✓ Restore Complete!', Colors.GREEN)}"))
            print(box_line(""))
            if common.cfg.enable_cloudflared == "yes":
                print(box_line(f" Access: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            elif common.cfg.enable_traefik == "yes":
                print(box_line(f" Access: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            else:
                print(box_line(f" Access: {colorize(f'http://localhost:{common.cfg.http_port}', Colors.CYAN)}"))
            print(box_line(""))
            print(box_line(" Your documents and settings have been restored."))
            print(draw_box_bottom(box_width))
            
        except KeyboardInterrupt:
            print()
            say("Restore cancelled")
        except Exception as e:
            error(f"Restore failed: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def create_fresh_instance(self) -> None:
        """Create a new fresh instance with guided setup."""
        print_header("Create New Instance")
        
        if os.geteuid() != 0:
            error("Creating instances requires root privileges. Please run with sudo.")
            input("\nPress Enter to continue...")
            return
        
        # Get existing instances for validation
        existing_instances = [i.name for i in self.instance_manager.list_instances()]
        
        # Check networking service availability upfront
        net_status = check_networking_dependencies()
        
        # Display welcome box with system status
        box_line, box_width = create_box_helper(60)
        print(draw_box_top(box_width))
        print(box_line(" Welcome to the Paperless-NGX instance creator!"))
        print(box_line(""))
        print(box_line(" This wizard will guide you through setting up a new"))
        print(box_line(" Paperless-NGX instance with your preferred options."))
        print(box_line(""))
        print(draw_section_header("System Status", box_width))
        
        # Show networking availability
        traefik_status = colorize("● Ready", Colors.GREEN) if net_status["traefik_running"] else colorize("○ Not running", Colors.YELLOW)
        cloudflare_status = colorize("● Ready", Colors.GREEN) if net_status["cloudflared_authenticated"] else (
            colorize("○ Not authenticated", Colors.YELLOW) if net_status["cloudflared_installed"] else colorize("○ Not installed", Colors.RED)
        )
        tailscale_status = colorize("● Connected", Colors.GREEN) if net_status["tailscale_connected"] else (
            colorize("○ Not connected", Colors.YELLOW) if net_status["tailscale_installed"] else colorize("○ Not installed", Colors.RED)
        )
        
        print(box_line(f" Traefik (HTTPS):     {traefik_status}"))
        print(box_line(f" Cloudflare Tunnel:   {cloudflare_status}"))
        print(box_line(f" Tailscale:           {tailscale_status}"))
        print(draw_box_bottom(box_width))
        print()
        
        try:
            # Import installer modules
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import common, files, traefik, cloudflared, tailscale
            
            # ─── Step 1: Instance Identity ────────────────────────────────────
            print(colorize("Step 1 of 4: Instance Identity", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Get instance name with validation
            while True:
                instance_name = get_input("Instance name", "paperless")
                
                if instance_name in existing_instances:
                    warn(f"Instance '{instance_name}' already exists. Choose another name.")
                    continue
                
                if not instance_name or not instance_name.replace("-", "").replace("_", "").isalnum():
                    warn("Name must be alphanumeric (hyphens and underscores allowed)")
                    continue
                
                break
            
            # Set up paths
            common.cfg.instance_name = instance_name
            common.cfg.data_root = f"/home/docker/{instance_name}"
            common.cfg.stack_dir = f"/home/docker/{instance_name}-setup"
            common.cfg.rclone_remote_path = f"backups/paperless/{instance_name}"
            common.cfg.refresh_paths()
            
            # Show computed paths
            print()
            say(f"Instance '{colorize(instance_name, Colors.BOLD)}' will use:")
            print(f"  Data:  {colorize(common.cfg.data_root, Colors.CYAN)}")
            print(f"  Stack: {colorize(common.cfg.stack_dir, Colors.CYAN)}")
            print()
            
            # Timezone
            common.cfg.tz = get_input("Timezone", common.cfg.tz)
            
            # Admin credentials
            print()
            say("Set up admin credentials:")
            common.cfg.paperless_admin_user = get_input("Admin username", common.cfg.paperless_admin_user)
            common.cfg.paperless_admin_password = get_input("Admin password", common.cfg.paperless_admin_password)
            print()
            
            # ─── Step 2: Network Access ───────────────────────────────────────
            print(colorize("Step 2 of 4: Network Access", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            say("How should this instance be accessed?")
            print()
            
            options = []
            options.append(("1", "Direct HTTP", "Simple port binding (e.g., http://localhost:8000)", True))
            options.append(("2", "HTTPS via Traefik", "Automatic SSL certificates" + (
                "" if net_status["traefik_running"] else colorize(" (Traefik not running)", Colors.YELLOW)
            ), True))
            options.append(("3", "Cloudflare Tunnel", "Secure access via Cloudflare" + (
                "" if net_status["cloudflared_authenticated"] else colorize(" (Not configured)", Colors.YELLOW)
            ), True))
            
            for key, title, desc, _ in options:
                print(f"  {colorize(key + ')', Colors.BOLD)} {colorize(title, Colors.CYAN)}")
                print(f"     {desc}")
            print()
            
            access_choice = get_input("Choose access method [1-3]", "1")
            
            if access_choice == "2":
                common.cfg.enable_traefik = "yes"
                common.cfg.enable_cloudflared = "no"
                common.cfg.domain = get_input("Domain (DNS must point to this server)", f"{instance_name}.example.com")
                
                if not net_status["traefik_running"]:
                    # Only ask for email if Traefik isn't running yet (will need to be set up)
                    common.cfg.letsencrypt_email = get_input("Email for Let's Encrypt", common.cfg.letsencrypt_email)
                    warn("Traefik is not running!")
                    say("Install from main menu: Manage Traefik (HTTPS)")
                    if not confirm("Continue anyway? (Instance won't work until Traefik is set up)", False):
                        say("Setup cancelled")
                        input("\nPress Enter to continue...")
                        return
                        
            elif access_choice == "3":
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "yes"
                common.cfg.domain = get_input("Domain (configured in Cloudflare)", f"{instance_name}.example.com")
                
                if not net_status["cloudflared_authenticated"]:
                    warn("Cloudflare Tunnel not configured!")
                    say("Set up from main menu: Manage Cloudflare Tunnel")
                    if not confirm("Continue anyway? (Tunnel won't be created automatically)", False):
                        say("Setup cancelled")
                        input("\nPress Enter to continue...")
                        return
            else:
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "no"
            
            # Port selection - find available port BEFORE showing default
            print()
            from lib.installer.common import get_next_available_port
            available_port = get_next_available_port(8000)  # Always start checking from 8000
            common.cfg.http_port = get_input("HTTP port", available_port)
            
            # Tailscale add-on
            print()
            if net_status["tailscale_connected"]:
                say("Tailscale is available for private network access")
                if confirm("Enable Tailscale access?", False):
                    common.cfg.enable_tailscale = "yes"
                    ok("Tailscale access enabled")
                else:
                    common.cfg.enable_tailscale = "no"
            elif net_status["tailscale_installed"]:
                say("Tailscale is installed but not connected")
                common.cfg.enable_tailscale = "no"
            else:
                common.cfg.enable_tailscale = "no"
            print()
            
            # ─── Step 3: Backup Configuration ─────────────────────────────────
            print(colorize("Step 3 of 4: Backup Schedule", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            if self.rclone_configured:
                say("Backup server is configured. Set your backup schedule:")
                print()
                
                print(f"  {colorize('1)', Colors.BOLD)} Weekly full + 6-hourly incremental {colorize('(recommended)', Colors.GREEN)}")
                print(f"  {colorize('2)', Colors.BOLD)} Weekly full + daily incremental")
                print(f"  {colorize('3)', Colors.BOLD)} Custom schedule")
                print()
                
                backup_choice = get_input("Choose backup plan [1-3]", "1")
                
                if backup_choice == "1":
                    common.cfg.cron_full_time = "30 3 * * 0"    # Sunday 3:30 AM
                    common.cfg.cron_incr_time = "0 */6 * * *"   # Every 6 hours
                elif backup_choice == "2":
                    common.cfg.cron_full_time = "30 3 * * 0"    # Sunday 3:30 AM
                    common.cfg.cron_incr_time = "0 0 * * *"     # Daily midnight
                else:
                    common.prompt_backup_plan()
                
                ok("Backup schedule configured")
            else:
                warn("Backup server not configured - backups will be disabled")
                say("Configure from main menu: Configure Backup Server")
                common.cfg.cron_full_time = ""
                common.cfg.cron_incr_time = ""
            print()
            
            # ─── Step 4: Review & Create ──────────────────────────────────────
            print(colorize("Step 4 of 4: Review & Create", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Summary box
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Instance Summary', Colors.BOLD)}"))
            print(box_line(""))
            print(box_line(f" Name:     {colorize(common.cfg.instance_name, Colors.CYAN)}"))
            print(box_line(f" Data:     {common.cfg.data_root}"))
            print(box_line(f" Stack:    {common.cfg.stack_dir}"))
            print(box_line(""))
            
            # Access method
            if common.cfg.enable_traefik == "yes":
                access_str = f"🔒 HTTPS via Traefik → https://{common.cfg.domain}"
            elif common.cfg.enable_cloudflared == "yes":
                access_str = f"☁️  Cloudflare Tunnel → https://{common.cfg.domain}"
            else:
                access_str = f"🌐 Direct HTTP → http://localhost:{common.cfg.http_port}"
            print(box_line(f" Access:   {access_str}"))
            
            if common.cfg.enable_tailscale == "yes":
                print(box_line(f"           🔐 + Tailscale private access"))
            
            print(box_line(""))
            print(box_line(f" Admin:    {common.cfg.paperless_admin_user}"))
            print(box_line(f" Timezone: {common.cfg.tz}"))
            print(draw_box_bottom(box_width))
            print()
            
            if not confirm("Create this instance?", True):
                say("Setup cancelled")
                input("\nPress Enter to continue...")
                return
            
            # ─── Create Instance ──────────────────────────────────────────────
            print()
            say("Creating instance...")
            
            # Create directories
            common.ensure_dir_tree(common.cfg)
            ok("Directories created")
            
            # Write config files
            files.write_env_file()
            files.write_compose_file()
            files.copy_helper_scripts()
            ok("Configuration files written")
            
            # Start containers
            say("Starting containers (this may take a moment)...")
            files.bring_up_stack()
            
            # Run self-test
            from lib.utils.selftest import run_stack_tests
            if run_stack_tests(Path(common.cfg.compose_file), Path(common.cfg.env_file)):
                ok("Health check passed")
            else:
                warn("Health check had warnings - check container logs if issues persist")
            
            # Set up backup cron if configured
            if common.cfg.cron_full_time or common.cfg.cron_incr_time:
                files.install_cron_backup()
                ok("Backup schedule installed")
            
            # Set up Cloudflare tunnel if enabled
            if common.cfg.enable_cloudflared == "yes" and net_status["cloudflared_authenticated"]:
                setup_cloudflare_tunnel(common.cfg.instance_name, common.cfg.domain)
            
            # Register instance
            self.instance_manager.add_instance(
                common.cfg.instance_name,
                Path(common.cfg.stack_dir),
                Path(common.cfg.data_root)
            )
            
            # Success message
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('✓ Instance Created Successfully!', Colors.GREEN)}"))
            print(box_line(""))
            if common.cfg.enable_traefik == "yes":
                print(box_line(f" Access at: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            elif common.cfg.enable_cloudflared == "yes":
                print(box_line(f" Access at: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            else:
                print(box_line(f" Access at: {colorize(f'http://localhost:{common.cfg.http_port}', Colors.CYAN)}"))
            print(box_line(""))
            print(box_line(f" Username: {colorize(common.cfg.paperless_admin_user, Colors.BOLD)}"))
            print(box_line(f" Password: {colorize(common.cfg.paperless_admin_password, Colors.BOLD)}"))
            print(draw_box_bottom(box_width))
            
        except KeyboardInterrupt:
            print()
            say("Setup cancelled")
        except Exception as e:
            error(f"Failed to create instance: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def instance_detail_menu(self, instance: Instance) -> None:
        """Detail menu for a specific instance."""
        while True:
            print_header(f"Instance: {instance.name}")
            
            status = colorize("● Running", Colors.GREEN) if instance.is_running else colorize("○ Stopped", Colors.YELLOW)
            domain = instance.get_env_value("DOMAIN", "localhost")
            access_urls = instance.get_access_urls()
            
            box_line, box_width = create_box_helper(60)
            
            print(draw_box_top(box_width))
            print(box_line(f" Status: {status}"))
            print(box_line(f" Domain: {colorize(domain, Colors.BOLD)}"))
            
            # Show all access URLs
            if access_urls:
                print(box_line(f" Access:"))
                for mode_label, url in access_urls:
                    print(box_line(f"   {mode_label}: {colorize(url, Colors.CYAN)}"))
            
            print(box_line(f" Stack:  {instance.stack_dir}"))
            print(draw_box_bottom(box_width))
            print()
            
            options = [
                ("", colorize("Information:", Colors.BOLD)),
                ("1", "  • View full details"),
                ("2", "  • Health check"),
                ("", ""),
                ("", colorize("Operations:", Colors.BOLD)),
                ("3", "  • Update instance " + colorize("(backup + upgrade)", Colors.YELLOW)),
                ("4", "  • Backup now"),
                ("5", "  • Restore from backup"),
                ("6", "  • Container operations"),
                ("", ""),
                ("", colorize("Advanced:", Colors.BOLD)),
                ("7", "  • Edit settings"),
                ("8", "  • " + colorize("Delete instance", Colors.RED)),
                ("", ""),
                ("0", colorize("◀ Back", Colors.CYAN))
            ]
            
            for key, desc in options:
                if key:
                    print(f"  {colorize(key + ')', Colors.BOLD)} {desc}")
                else:
                    print(f"  {desc}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self.view_instance_details(instance)
            elif choice == "2":
                self.health_check(instance)
            elif choice == "3":
                self.update_instance(instance)
            elif choice == "4":
                self.backup_instance(instance)
            elif choice == "5":
                self.revert_instance(instance)
            elif choice == "6":
                self.container_operations(instance)
            elif choice == "7":
                self.edit_instance(instance)
            elif choice == "8":
                print()
                warn(f"This will DELETE instance '{instance.name}' completely!")
                print(f"  • Stack directory: {instance.stack_dir}")
                print(f"  • Data directory:  {instance.data_root}")
                print(f"  • Docker containers")
                print(f"  • Cloudflared service (if exists)")
                print()
                
                if confirm("Delete ALL files and containers?", False):
                    if confirm("Are you ABSOLUTELY sure? This cannot be undone!", False):
                        self.instance_manager.remove_instance(instance.name, delete_files=True)
                        ok(f"Instance '{instance.name}' completely deleted")
                        input("\nPress Enter to continue...")
                        break
                else:
                    # Just remove from tracking
                    if confirm(f"Remove from tracking only (keep files)?", False):
                        self.instance_manager.remove_instance(instance.name, delete_files=False)
                        ok(f"Instance '{instance.name}' removed from tracking")
                        input("\nPress Enter to continue...")
                        break
            else:
                warn("Invalid option")
    
    def view_instance_details(self, instance: Instance) -> None:
        """View detailed information about an instance."""
        print_header(f"Details: {instance.name}")
        
        print(f"Name: {instance.name}")
        print(f"Status: {'Running' if instance.is_running else 'Stopped'}")
        print(f"Stack Directory: {instance.stack_dir}")
        print(f"Data Root: {instance.data_root}")
        print(f"Env File: {instance.env_file}")
        print(f"Compose File: {instance.compose_file}")
        print()
        
        # Show access URLs
        access_urls = instance.get_access_urls()
        if access_urls:
            print("Access Methods:")
            for mode_label, url in access_urls:
                print(f"  {mode_label}: {url}")
            print()
        
        # Show key settings from env file
        if instance.env_file.exists():
            print("Settings:")
            for key in ["PAPERLESS_URL", "POSTGRES_DB", "TZ", "ENABLE_TRAEFIK", "ENABLE_CLOUDFLARED", "ENABLE_TAILSCALE", "DOMAIN", "HTTP_PORT"]:
                value = instance.get_env_value(key, "not set")
                print(f"  {key}: {value}")
        
        input("\nPress Enter to continue...")
    
    def health_check(self, instance: Instance) -> None:
        """Run health check on instance."""
        checker = HealthChecker(instance)
        checker.print_report()
        input("\nPress Enter to continue...")
    
    def update_instance(self, instance: Instance) -> None:
        """Update instance with automatic backup and Docker version tracking."""
        print_header(f"Update Instance: {instance.name}")
        
        if not self.rclone_configured:
            warn("Backup server not configured - updates without backup are risky!")
            if not confirm("Continue anyway?", False):
                return
        
        say("This will:")
        print("  1. Create a FULL backup (with current Docker versions)")
        print("  2. Pull latest container images")
        print("  3. Recreate containers with new images")
        print("  4. Test health")
        print("  5. If it fails, you can restore from the backup\n")
        
        if not confirm("Continue with update?", True):
            return
        
        # Step 1: Full backup with Docker versions
        if self.rclone_configured:
            say("Creating full backup before update...")
            backup_mgr = BackupManager(instance)
            if not backup_mgr.run_backup("full"):
                error("Backup failed! Update aborted for safety.")
                input("\nPress Enter to continue...")
                return
            ok("Backup completed with Docker version info")
            print()
        
        # Step 2: Get current image versions before upgrade
        say("Recording current Docker versions...")
        current_versions = {}
        try:
            result = subprocess.run(
                [
                    "docker", "compose",
                    "-f", str(instance.compose_file),
                    "images", "--format", "{{.Service}}: {{.Repository}}:{{.Tag}}"
                ],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if ":" in line:
                        current_versions[line.split(":")[0].strip()] = line
                print("Current versions:")
                for version in current_versions.values():
                    print(f"  {version}")
                print()
        except Exception as e:
            warn(f"Could not capture current versions: {e}")
        
        # Step 3: Pull latest images
        say("Pulling latest container images...")
        try:
            self._docker_command(instance, "pull")
            ok("Images pulled successfully")
            print()
        except subprocess.CalledProcessError:
            error("Failed to pull images")
            input("\nPress Enter to continue...")
            return
        
        # Step 4: Recreate containers
        say("Recreating containers with new images...")
        try:
            self._docker_command(instance, "up", "-d", "--force-recreate")
            ok("Containers recreated")
            print()
        except subprocess.CalledProcessError:
            error("Failed to recreate containers!")
            warn("You may need to restore from backup")
            input("\nPress Enter to continue...")
            return
        
        # Step 5: Wait a moment for containers to stabilize
        say("Waiting for containers to stabilize...")
        import time
        time.sleep(10)
        
        # Step 6: Health check
        say("Running health check...")
        checker = HealthChecker(instance)
        checks = checker.check_all()
        
        passed = sum(checks.values())
        total = len(checks)
        
        print()
        if passed == total:
            ok(f"✓ Update successful! All {total} health checks passed")
            say("Your instance is now running the latest container versions")
        else:
            warn(f"⚠ Update completed but {total - passed}/{total} health checks failed")
            error("Instance may not be fully functional")
            print()
            print("You can:")
            print(f"  1. Check logs: docker compose -f {instance.compose_file} logs")
            print("  2. Restore from backup (will restore previous working versions)")
            print()
        
        # Show new versions
        try:
            result = subprocess.run(
                [
                    "docker", "compose",
                    "-f", str(instance.compose_file),
                    "images", "--format", "{{.Service}}: {{.Repository}}:{{.Tag}}"
                ],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                print("\nNew versions:")
                for line in result.stdout.strip().splitlines():
                    print(f"  {line}")
        except Exception:
            pass
        
        input("\nPress Enter to continue...")
    
    def backup_instance(self, instance: Instance) -> None:
        """Backup an instance."""
        if not self.rclone_configured:
            error("Backup server not configured!")
            input("\nPress Enter to continue...")
            return
        
        print_header(f"Backup: {instance.name}")
        
        options = [
            ("1", "Incremental backup"),
            ("2", "Full backup"),
            ("3", "Archive backup"),
            ("0", "Cancel")
        ]
        print_menu(options)
        
        choice = get_input("Select backup type", "1")
        
        mode_map = {"1": "incr", "2": "full", "3": "archive"}
        
        if choice in mode_map:
            backup_mgr = BackupManager(instance)
            say(f"Starting {mode_map[choice]} backup...")
            if backup_mgr.run_backup(mode_map[choice]):
                ok("Backup completed successfully!")
            else:
                error("Backup failed!")
        
        input("\nPress Enter to continue...")
    
    def revert_instance(self, instance: Instance) -> None:
        """Revert instance from backup."""
        if not self.rclone_configured:
            error("Backup server not configured!")
            input("\nPress Enter to continue...")
            return
        
        print_header(f"Restore/Revert: {instance.name}")
        
        backup_mgr = BackupManager(instance)
        snapshots = backup_mgr.fetch_snapshots()
        
        if not snapshots:
            warn("No backups found for this instance")
            input("\nPress Enter to continue...")
            return
        
        print(f"{'#':<5} {'Name':<35} {'Mode':<10} {'Parent'}")
        print("─" * 80)
        
        for idx, (name, mode, parent) in enumerate(snapshots, 1):
            parent_display = parent if mode == "incr" else "-"
            mode_color = Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN
            print(f"{idx:<5} {name:<35} {colorize(mode, mode_color):<20} {parent_display}")
        print()
        
        choice = get_input(f"Select snapshot [1-{len(snapshots)}] or 'cancel'", "cancel")
        
        if choice.isdigit() and 1 <= int(choice) <= len(snapshots):
            snapshot = snapshots[int(choice) - 1][0]
            
            print()
            warn("This will stop the instance and restore data!")
            if confirm("Continue with restore?", False):
                say("Starting restore...")
                if backup_mgr.run_restore(snapshot):
                    ok("Restore completed!")
                else:
                    error("Restore failed!")
        
        input("\nPress Enter to continue...")
    
    def container_operations(self, instance: Instance) -> None:
        """Container operations for an instance."""
        while True:
            print_header(f"Containers: {instance.name}")
            
            options = [
                ("1", "Start containers"),
                ("2", "Stop containers"),
                ("3", "Restart containers"),
                ("4", "View status"),
                ("5", "View logs"),
                ("6", "Upgrade containers"),
                ("0", "Back")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._docker_command(instance, "up", "-d")
                input("\nPress Enter to continue...")
            elif choice == "2":
                self._docker_command(instance, "down")
                input("\nPress Enter to continue...")
            elif choice == "3":
                self._docker_command(instance, "restart")
                input("\nPress Enter to continue...")
            elif choice == "4":
                self._docker_command(instance, "ps")
                input("\nPress Enter to continue...")
            elif choice == "5":
                self._view_logs(instance)
            elif choice == "6":
                self._upgrade_containers(instance)
            else:
                warn("Invalid option")
    
    def _docker_command(self, instance: Instance, *args: str) -> None:
        """Run a docker compose command."""
        cmd = [
            "docker", "compose",
            "-f", str(instance.compose_file),
            "--env-file", str(instance.env_file),
            *args
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            error(f"Command failed with exit code {e.returncode}")
    
    def _view_logs(self, instance: Instance) -> None:
        """View container logs."""
        service = get_input("Service name (blank for all)", "")
        cmd = [
            "docker", "compose",
            "-f", str(instance.compose_file),
            "--env-file", str(instance.env_file),
            "logs", "--tail", "100", "--timestamps"
        ]
        if service:
            cmd.append(service)
        
        try:
            subprocess.run(cmd)
        except subprocess.CalledProcessError:
            error("Failed to view logs")
        
        input("\nPress Enter to continue...")
    
    def _upgrade_containers(self, instance: Instance) -> None:
        """Upgrade containers with automatic backup."""
        if confirm("Run backup before upgrade?", True):
            backup_mgr = BackupManager(instance)
            say("Running full backup before upgrade...")
            if not backup_mgr.run_backup("full"):
                error("Backup failed! Upgrade aborted.")
                input("\nPress Enter to continue...")
                return
            ok("Backup completed")
        
        say("Pulling latest images...")
        self._docker_command(instance, "pull")
        
        say("Recreating containers...")
        self._docker_command(instance, "up", "-d")
        
        ok("Upgrade complete!")
        input("\nPress Enter to continue...")
    
    def edit_instance(self, instance: Instance) -> None:
        """Edit instance settings - networking, domain, ports, etc."""
        while True:
            print_header(f"Edit: {instance.name}")
            
            # Show current settings
            box_line, box_width = create_box_helper(62)
            print(draw_box_top(box_width))
            print(box_line(f" Status: {'Running' if instance.is_running else 'Stopped'}"))
            print(box_line(f""))
            print(box_line(f" {colorize('Current Settings:', Colors.BOLD)}"))
            print(box_line(f"   Domain:        {instance.get_env_value('DOMAIN', 'localhost')}"))
            print(box_line(f"   HTTP Port:     {instance.get_env_value('HTTP_PORT', '8000')}"))
            print(box_line(f"   Traefik:       {instance.get_env_value('ENABLE_TRAEFIK', 'no')}"))
            print(box_line(f"   Cloudflare:    {instance.get_env_value('ENABLE_CLOUDFLARED', 'no')}"))
            print(box_line(f"   Tailscale:     {instance.get_env_value('ENABLE_TAILSCALE', 'no')}"))
            print(draw_box_bottom(box_width))
            print()
            
            # Show active access methods
            access_urls = instance.get_access_urls()
            if access_urls:
                print(colorize("Active Access Methods:", Colors.BOLD))
                for mode_label, url in access_urls:
                    print(f"  {mode_label}: {url}")
                print()
            
            options = [
                ("", colorize("Networking:", Colors.BOLD)),
                ("1", "  Change domain"),
                ("2", "  Change HTTP port"),
                ("3", "  Toggle Traefik (HTTPS)"),
                ("4", "  Toggle Cloudflare Tunnel"),
                ("5", "  Toggle Tailscale"),
                ("", ""),
                ("", colorize("Credentials:", Colors.BOLD)),
                ("6", "  Change admin password"),
                ("", ""),
                ("0", colorize("◀ Back", Colors.CYAN))
            ]
            
            for key, desc in options:
                if key:
                    print(f"  {colorize(key + ')', Colors.BOLD)} {desc}")
                else:
                    print(f"  {desc}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._edit_instance_domain(instance)
            elif choice == "2":
                self._edit_instance_port(instance)
            elif choice == "3":
                self._toggle_instance_traefik(instance)
            elif choice == "4":
                self._toggle_instance_cloudflare(instance)
            elif choice == "5":
                self._toggle_instance_tailscale(instance)
            elif choice == "6":
                self._edit_instance_admin_password(instance)
            else:
                warn("Invalid option")
    
    def _update_instance_env(self, instance: Instance, key: str, value: str) -> bool:
        """Update a single value in the instance's .env file."""
        try:
            if not instance.env_file.exists():
                error(f"Env file not found: {instance.env_file}")
                return False
            
            content = instance.env_file.read_text()
            lines = content.splitlines()
            updated = False
            
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    updated = True
                    break
            
            if not updated:
                # Add new key
                lines.append(f"{key}={value}")
            
            instance.env_file.write_text("\n".join(lines) + "\n")
            return True
        except Exception as e:
            error(f"Failed to update env file: {e}")
            return False
    
    def _edit_instance_domain(self, instance: Instance) -> None:
        """Edit instance domain."""
        current = instance.get_env_value("DOMAIN", "localhost")
        say(f"Current domain: {current}")
        new_domain = get_input("New domain (or Enter to cancel)", current)
        
        if new_domain and new_domain != current:
            if self._update_instance_env(instance, "DOMAIN", new_domain):
                # Also update PAPERLESS_URL if traefik or cloudflare enabled
                enable_traefik = instance.get_env_value("ENABLE_TRAEFIK", "no")
                enable_cloudflared = instance.get_env_value("ENABLE_CLOUDFLARED", "no")
                if enable_traefik == "yes" or enable_cloudflared == "yes":
                    self._update_instance_env(instance, "PAPERLESS_URL", f"https://{new_domain}")
                
                ok(f"Domain changed to: {new_domain}")
                warn("Restart containers for changes to take effect")
        input("\nPress Enter to continue...")
    
    def _edit_instance_port(self, instance: Instance) -> None:
        """Edit instance HTTP port."""
        current = instance.get_env_value("HTTP_PORT", "8000")
        say(f"Current port: {current}")
        new_port = get_input("New HTTP port (or Enter to cancel)", current)
        
        if new_port and new_port != current:
            if new_port.isdigit() and 1024 <= int(new_port) <= 65535:
                if self._update_instance_env(instance, "HTTP_PORT", new_port):
                    ok(f"HTTP port changed to: {new_port}")
                    warn("You must recreate containers for port changes:")
                    say(f"  docker compose -f {instance.compose_file} down")
                    say(f"  docker compose -f {instance.compose_file} up -d")
            else:
                error("Invalid port number (must be 1024-65535)")
        input("\nPress Enter to continue...")
    
    def _toggle_instance_traefik(self, instance: Instance) -> None:
        """Toggle Traefik HTTPS for instance."""
        current = instance.get_env_value("ENABLE_TRAEFIK", "no")
        
        if current == "yes":
            # Disable Traefik
            if confirm("Disable Traefik HTTPS for this instance?", False):
                self._update_instance_env(instance, "ENABLE_TRAEFIK", "no")
                port = instance.get_env_value("HTTP_PORT", "8000")
                self._update_instance_env(instance, "PAPERLESS_URL", f"http://localhost:{port}")
                ok("Traefik disabled - instance will use direct HTTP")
                warn("Restart containers and regenerate docker-compose.yml")
        else:
            # Enable Traefik
            from lib.installer.traefik import is_traefik_running
            if not is_traefik_running():
                error("System Traefik is not running!")
                say("Install Traefik from main menu first")
            elif confirm("Enable Traefik HTTPS for this instance?", True):
                domain = instance.get_env_value("DOMAIN", "localhost")
                if domain == "localhost":
                    domain = get_input("Enter domain for HTTPS", "paperless.example.com")
                    self._update_instance_env(instance, "DOMAIN", domain)
                
                self._update_instance_env(instance, "ENABLE_TRAEFIK", "yes")
                self._update_instance_env(instance, "ENABLE_CLOUDFLARED", "no")  # Mutually exclusive
                self._update_instance_env(instance, "PAPERLESS_URL", f"https://{domain}")
                ok(f"Traefik enabled for https://{domain}")
                warn("You must regenerate docker-compose.yml and recreate containers")
                self._offer_regenerate_compose(instance)
        
        input("\nPress Enter to continue...")
    
    def _toggle_instance_cloudflare(self, instance: Instance) -> None:
        """Toggle Cloudflare Tunnel for instance."""
        current = instance.get_env_value("ENABLE_CLOUDFLARED", "no")
        
        if current == "yes":
            # Disable Cloudflare
            if confirm("Disable Cloudflare Tunnel for this instance?", False):
                self._update_instance_env(instance, "ENABLE_CLOUDFLARED", "no")
                port = instance.get_env_value("HTTP_PORT", "8000")
                self._update_instance_env(instance, "PAPERLESS_URL", f"http://localhost:{port}")
                
                # Stop and remove tunnel service
                service_name = f"cloudflared-{instance.name}"
                try:
                    subprocess.run(["systemctl", "stop", service_name], check=False, capture_output=True)
                    subprocess.run(["systemctl", "disable", service_name], check=False, capture_output=True)
                    service_file = Path(f"/etc/systemd/system/{service_name}.service")
                    if service_file.exists():
                        service_file.unlink()
                    subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
                except:
                    pass
                
                ok("Cloudflare Tunnel disabled")
        else:
            # Enable Cloudflare
            from lib.installer.cloudflared import is_cloudflared_installed, is_authenticated, create_tunnel
            if not is_cloudflared_installed():
                error("Cloudflared is not installed!")
                say("Install from main menu: Manage Cloudflare Tunnel")
            elif not is_authenticated():
                error("Cloudflared is not authenticated!")
                say("Authenticate from main menu: Manage Cloudflare Tunnel")
            elif confirm("Enable Cloudflare Tunnel for this instance?", True):
                domain = instance.get_env_value("DOMAIN", "localhost")
                if domain == "localhost":
                    domain = get_input("Enter domain for Cloudflare Tunnel", "paperless.example.com")
                    self._update_instance_env(instance, "DOMAIN", domain)
                
                self._update_instance_env(instance, "ENABLE_CLOUDFLARED", "yes")
                self._update_instance_env(instance, "ENABLE_TRAEFIK", "no")  # Mutually exclusive
                self._update_instance_env(instance, "PAPERLESS_URL", f"https://{domain}")
                
                # Create tunnel
                say("Creating Cloudflare tunnel...")
                if create_tunnel(instance.name, domain):
                    # Create and start systemd service
                    self._create_cloudflare_service(instance.name)
                    ok(f"Cloudflare Tunnel enabled for https://{domain}")
                else:
                    warn("Tunnel creation failed - you may need to set it up manually")
        
        input("\nPress Enter to continue...")
    
    def _toggle_instance_tailscale(self, instance: Instance) -> None:
        """Toggle Tailscale access for instance."""
        current = instance.get_env_value("ENABLE_TAILSCALE", "no")
        
        if current == "yes":
            if confirm("Disable Tailscale access for this instance?", False):
                self._update_instance_env(instance, "ENABLE_TAILSCALE", "no")
                
                # Remove Tailscale Serve if configured
                from lib.installer.tailscale import remove_serve, is_serve_available
                if is_serve_available():
                    path = f"/{instance.name}"
                    remove_serve(path)
                
                ok("Tailscale access disabled")
        else:
            from lib.installer.tailscale import (
                is_tailscale_installed, is_connected, get_ip, get_hostname,
                is_serve_available, add_serve, get_serve_url
            )
            if not is_tailscale_installed():
                error("Tailscale is not installed!")
                say("Install from main menu: Manage Tailscale")
            elif not is_connected():
                error("Tailscale is not connected!")
                say("Connect from main menu: Manage Tailscale")
            elif confirm("Enable Tailscale access for this instance?", True):
                self._update_instance_env(instance, "ENABLE_TAILSCALE", "yes")
                ip = get_ip()
                port = instance.get_env_value("HTTP_PORT", "8000")
                
                # Try to set up Tailscale Serve for HTTPS access
                if is_serve_available():
                    hostname = get_hostname()
                    if hostname:
                        print()
                        say(f"Tailscale Serve can provide HTTPS: https://{hostname}/{instance.name}")
                        if confirm("Configure Tailscale Serve for HTTPS access?", True):
                            path = f"/{instance.name}"
                            if add_serve(path, int(port)):
                                serve_url = get_serve_url(path)
                                self._update_instance_env(instance, "TAILSCALE_SERVE_PATH", path)
                                ok(f"Tailscale Serve configured: {serve_url}")
                            else:
                                warn("Tailscale Serve setup failed - using direct IP access")
                                ok(f"Tailscale enabled - access via http://{ip}:{port}")
                        else:
                            ok(f"Tailscale enabled - access via http://{ip}:{port}")
                    else:
                        ok(f"Tailscale enabled - access via http://{ip}:{port}")
                else:
                    ok(f"Tailscale enabled - access via http://{ip}:{port}")
                    say("Tip: Tailscale Serve (HTTPS) requires a paid Tailscale plan")
                
                say("Note: Tailscale works alongside other access methods")
        
        input("\nPress Enter to continue...")
    
    def _edit_instance_admin_password(self, instance: Instance) -> None:
        """Change the Paperless admin password."""
        say("This will update the admin password in the configuration.")
        warn("The password is stored in the .env file.")
        
        new_password = get_input("New admin password (or Enter to cancel)", "")
        if new_password:
            if len(new_password) < 8:
                error("Password must be at least 8 characters")
            else:
                if self._update_instance_env(instance, "PAPERLESS_ADMIN_PASSWORD", new_password):
                    ok("Admin password updated in configuration")
                    say("To apply, you need to recreate the admin user:")
                    say(f"  docker compose -f {instance.compose_file} exec paperless python manage.py changepassword admin")
        
        input("\nPress Enter to continue...")
    
    def _create_cloudflare_service(self, instance_name: str) -> bool:
        """Create and start a systemd service for Cloudflare tunnel."""
        try:
            service_content = f"""[Unit]
Description=Cloudflare Tunnel for {instance_name}
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""
            service_file = Path(f"/etc/systemd/system/cloudflared-{instance_name}.service")
            service_file.write_text(service_content)
            
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", f"cloudflared-{instance_name}"], check=True)
            subprocess.run(["systemctl", "start", f"cloudflared-{instance_name}"], check=True)
            
            ok("Cloudflare tunnel service started")
            return True
        except Exception as e:
            warn(f"Failed to create service: {e}")
            return False
    
    def _offer_regenerate_compose(self, instance: Instance) -> None:
        """Offer to regenerate docker-compose.yml for the instance."""
        if confirm("Regenerate docker-compose.yml now?", True):
            try:
                sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
                from lib.installer import common, files
                
                # Load values from env file into config
                common.cfg.instance_name = instance.name
                common.cfg.stack_dir = str(instance.stack_dir)
                common.cfg.data_root = str(instance.data_root)
                common.cfg.enable_traefik = instance.get_env_value("ENABLE_TRAEFIK", "no")
                common.cfg.enable_cloudflared = instance.get_env_value("ENABLE_CLOUDFLARED", "no")
                common.cfg.enable_tailscale = instance.get_env_value("ENABLE_TAILSCALE", "no")
                common.cfg.domain = instance.get_env_value("DOMAIN", "localhost")
                common.cfg.http_port = instance.get_env_value("HTTP_PORT", "8000")
                common.cfg.postgres_version = instance.get_env_value("POSTGRES_VERSION", "15")
                common.cfg.postgres_db = instance.get_env_value("POSTGRES_DB", "paperless")
                common.cfg.postgres_user = instance.get_env_value("POSTGRES_USER", "paperless")
                common.cfg.postgres_password = instance.get_env_value("POSTGRES_PASSWORD", "")
                common.cfg.refresh_paths()
                
                # Write new compose file
                files.write_compose_file()
                ok("docker-compose.yml regenerated")
                
                if confirm("Recreate containers now?", True):
                    self._docker_command(instance, "down")
                    self._docker_command(instance, "up", "-d")
                    ok("Containers recreated")
            except Exception as e:
                error(f"Failed to regenerate: {e}")

    def system_backup_menu(self) -> None:
        """System-level backup and restore menu."""
        while True:
            print_header("System Backup & Restore")
            
            if not self.rclone_configured:
                warn("Backup server not configured!")
                input("\nPress Enter to continue...")
                return
            
            instances = self.instance_manager.list_instances()
            
            # Check for existing system backups
            try:
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless-system"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                system_backups = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
            except:
                system_backups = []
            
            # System overview box
            box_line, box_width = create_box_helper(58)
            
            print(draw_box_top(box_width))
            print(box_line(f" Current System: {len(instances)} instance(s) configured"))
            print(box_line(f" System Backups: {len(system_backups)} available"))
            print(draw_box_bottom(box_width))
            print()
            
            print(colorize("What is System Backup?", Colors.BOLD))
            print("  • Backs up metadata about ALL instances")
            print("  • Records which instances exist, their config, state")
            print("  • Enables disaster recovery: restore entire multi-instance setup")
            print("  • Separate from individual instance data backups")
            print()
            
            options = [
                ("1", colorize("💾", Colors.GREEN) + " Backup current system"),
                ("2", colorize("📋", Colors.BLUE) + " View system backups"),
                ("3", colorize("🔄", Colors.YELLOW) + " Restore system from backup"),
                ("0", colorize("◀ Back", Colors.CYAN))
            ]
            print_menu(options)
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._backup_system()
            elif choice == "2":
                self._view_system_backups()
            elif choice == "3":
                self._restore_system()
            else:
                warn("Invalid option")
    
    def _backup_system(self) -> None:
        """Backup current system configuration."""
        print_header("Backup Current System")
        
        instances = self.instance_manager.list_instances()
        
        if not instances:
            warn("No instances to backup!")
            input("\nPress Enter to continue...")
            return
        
        print(f"This will backup metadata for {len(instances)} instance(s):")
        for inst in instances:
            status = "running" if inst.is_running else "stopped"
            print(f"  • {inst.name} ({status})")
        print()
        
        # Check what network configs exist
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import traefik, cloudflared, tailscale
        
        traefik_running = traefik.is_traefik_running()
        cloudflare_tunnels = cloudflared.list_tunnels() if cloudflared.is_authenticated() else []
        tailscale_connected = tailscale.is_connected()
        rclone_conf = Path.home() / ".config" / "rclone" / "rclone.conf"
        
        print("Network configuration to backup:")
        print(f"  • Traefik: {'✓ Running' if traefik_running else '○ Not active'}")
        print(f"  • Cloudflare Tunnels: {len(cloudflare_tunnels)} tunnel(s)")
        print(f"  • Tailscale: {'✓ Connected' if tailscale_connected else '○ Not active'}")
        print(f"  • rclone config: {'✓ Found' if rclone_conf.exists() else '○ Not found'}")
        print()
        
        if not confirm("Create system backup?", True):
            return
        
        try:
            from datetime import datetime
            import json
            import tempfile
            import shutil
            import base64
            
            # Create temp directory for system backup
            backup_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            work = Path(tempfile.mkdtemp(prefix="paperless-system-"))
            
            say(f"Creating system backup: {backup_name}")
            
            # ─── Backup Network Configurations ────────────────────────────────
            network_dir = work / "network"
            network_dir.mkdir(parents=True, exist_ok=True)
            
            network_info = {
                "traefik": {"enabled": False},
                "cloudflare": {"enabled": False, "tunnels": []},
                "tailscale": {"enabled": False},
                "rclone": {"enabled": False}
            }
            
            # Backup Traefik config
            traefik_dir = Path("/opt/traefik")
            if traefik_dir.exists():
                say("Backing up Traefik configuration...")
                traefik_backup_dir = network_dir / "traefik"
                traefik_backup_dir.mkdir(exist_ok=True)
                
                # traefik.yml (config)
                traefik_yml = traefik_dir / "traefik.yml"
                if traefik_yml.exists():
                    shutil.copy2(traefik_yml, traefik_backup_dir / "traefik.yml")
                
                # acme.json (SSL certificates) - this is sensitive!
                acme_json = traefik_dir / "acme.json"
                if acme_json.exists():
                    shutil.copy2(acme_json, traefik_backup_dir / "acme.json")
                
                network_info["traefik"] = {
                    "enabled": True,
                    "running": traefik_running,
                    "email": traefik.get_traefik_email()
                }
                ok("Traefik config backed up (including SSL certificates)")
            
            # Backup Cloudflare Tunnel configs
            cloudflared_etc = Path("/etc/cloudflared")
            cloudflared_home = Path.home() / ".cloudflared"
            
            if cloudflared_etc.exists() or cloudflared_home.exists():
                say("Backing up Cloudflare Tunnel configuration...")
                cf_backup_dir = network_dir / "cloudflared"
                cf_backup_dir.mkdir(exist_ok=True)
                
                tunnel_configs = []
                
                # Backup /etc/cloudflared/*.yml (tunnel configs)
                if cloudflared_etc.exists():
                    etc_backup = cf_backup_dir / "etc"
                    etc_backup.mkdir(exist_ok=True)
                    for yml_file in cloudflared_etc.glob("*.yml"):
                        shutil.copy2(yml_file, etc_backup / yml_file.name)
                        tunnel_configs.append(yml_file.name.replace(".yml", ""))
                
                # Backup ~/.cloudflared/ (credentials and cert)
                if cloudflared_home.exists():
                    home_backup = cf_backup_dir / "home"
                    home_backup.mkdir(exist_ok=True)
                    
                    # cert.pem (authentication cert)
                    cert_pem = cloudflared_home / "cert.pem"
                    if cert_pem.exists():
                        shutil.copy2(cert_pem, home_backup / "cert.pem")
                    
                    # *.json (tunnel credentials)
                    for json_file in cloudflared_home.glob("*.json"):
                        shutil.copy2(json_file, home_backup / json_file.name)
                
                # Backup systemd services
                services_backup = cf_backup_dir / "services"
                services_backup.mkdir(exist_ok=True)
                for service_file in Path("/etc/systemd/system").glob("cloudflared-*.service"):
                    shutil.copy2(service_file, services_backup / service_file.name)
                
                network_info["cloudflare"] = {
                    "enabled": True,
                    "authenticated": cloudflared.is_authenticated(),
                    "tunnels": tunnel_configs,
                    "tunnel_count": len(cloudflare_tunnels)
                }
                ok(f"Cloudflare config backed up ({len(tunnel_configs)} tunnel configs)")
            
            # Backup rclone config
            if rclone_conf.exists():
                say("Backing up rclone configuration...")
                rclone_backup_dir = network_dir / "rclone"
                rclone_backup_dir.mkdir(exist_ok=True)
                shutil.copy2(rclone_conf, rclone_backup_dir / "rclone.conf")
                network_info["rclone"] = {"enabled": True}
                ok("rclone config backed up")
            
            # Note Tailscale status (can't really backup Tailscale auth)
            if tailscale_connected:
                ts_hostname = None
                ts_ip = None
                try:
                    ts_hostname = tailscale.get_hostname()
                    ts_ip = tailscale.get_ip()
                except:
                    pass
                network_info["tailscale"] = {
                    "enabled": True,
                    "hostname": ts_hostname,
                    "ip": ts_ip,
                    "note": "Tailscale requires re-authentication on new server"
                }
            
            # ─── Backup Instance Information ──────────────────────────────────
            system_info = {
                "backup_date": datetime.utcnow().isoformat(),
                "backup_name": backup_name,
                "backup_version": "2.0",  # New version with network config
                "instance_count": len(instances),
                "network": network_info,
                "instances": {},
                "instances_registry": json.loads(self.instance_manager.config_file.read_text()) if self.instance_manager.config_file.exists() else {}
            }
            
            for inst in instances:
                inst_info = {
                    "name": inst.name,
                    "stack_dir": str(inst.stack_dir),
                    "data_root": str(inst.data_root),
                    "running": inst.is_running,
                    "env_vars": {},
                    "latest_backup": None
                }
                
                # Capture key env variables
                if inst.env_file.exists():
                    for key in ["DOMAIN", "PAPERLESS_URL", "POSTGRES_DB", "ENABLE_TRAEFIK", 
                               "ENABLE_CLOUDFLARED", "ENABLE_TAILSCALE", "HTTP_PORT",
                               "RCLONE_REMOTE_PATH", "INSTANCE_NAME", "COMPOSE_PROJECT_NAME"]:
                        inst_info["env_vars"][key] = inst.get_env_value(key, "")
                
                # Find latest backup for this instance
                try:
                    backup_mgr = BackupManager(inst)
                    snaps = backup_mgr.fetch_snapshots()
                    if snaps:
                        inst_info["latest_backup"] = snaps[-1][0]
                except:
                    pass
                
                system_info["instances"][inst.name] = inst_info
            
            (work / "system-info.json").write_text(json.dumps(system_info, indent=2))
            
            # Create manifest
            manifest = f"""system_backup: true
backup_version: "2.0"
backup_date: {datetime.utcnow().isoformat()}
instance_count: {len(instances)}
network_config: true
traefik_enabled: {network_info['traefik']['enabled']}
cloudflare_tunnels: {len(network_info['cloudflare'].get('tunnels', []))}
rclone_config: {network_info['rclone']['enabled']}
"""
            (work / "manifest.yaml").write_text(manifest)
            
            # Upload to pCloud
            remote = f"pcloud:backups/paperless-system/{backup_name}"
            say("Uploading to backup server...")
            subprocess.run(
                ["rclone", "copy", str(work), remote],
                check=True,
                stdout=subprocess.DEVNULL
            )
            
            ok(f"System backup created: {backup_name}")
            print()
            
            box_line, box_width = create_box_helper(60)
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Backup Contents:', Colors.BOLD)}"))
            print(box_line(""))
            print(box_line(" ✓ Instance registry and metadata"))
            print(box_line(" ✓ References to latest data backups"))
            if network_info["traefik"]["enabled"]:
                print(box_line(" ✓ Traefik config + SSL certificates"))
            if network_info["cloudflare"]["enabled"]:
                print(box_line(f" ✓ Cloudflare tunnel configs ({len(network_info['cloudflare']['tunnels'])})"))
            if network_info["rclone"]["enabled"]:
                print(box_line(" ✓ rclone backup server config"))
            if network_info["tailscale"]["enabled"]:
                print(box_line(" ✓ Tailscale info (requires re-auth)"))
            print(draw_box_bottom(box_width))
            print()
            print("To restore on a new server:")
            print("  1. Install paperless-bulletproof")
            print("  2. Configure backup server connection")
            print("  3. Use 'Restore system from backup'")
            
            # Cleanup
            shutil.rmtree(work)
            
        except Exception as e:
            error(f"System backup failed: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def _view_system_backups(self) -> None:
        """View available system backups."""
        print_header("System Backups")
        
        try:
            result = subprocess.run(
                ["rclone", "lsd", "pcloud:backups/paperless-system"],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode != 0 or not result.stdout.strip():
                warn("No system backups found")
                input("\nPress Enter to continue...")
                return
            
            backups = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
            
            print(colorize("Available System Backups:", Colors.BOLD))
            print()
            
            for idx, backup in enumerate(sorted(backups, reverse=True), 1):
                # Get backup info
                try:
                    info = subprocess.run(
                        ["rclone", "cat", f"pcloud:backups/paperless-system/{backup}/system-info.json"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if info.returncode == 0:
                        import json
                        data = json.loads(info.stdout)
                        inst_count = data.get("instance_count", "?")
                        print(f"  {idx}) {backup} - {inst_count} instance(s)")
                    else:
                        print(f"  {idx}) {backup}")
                except:
                    print(f"  {idx}) {backup}")
            
        except Exception as e:
            error(f"Failed to list system backups: {e}")
        
        input("\nPress Enter to continue...")
    
    def _restore_system(self) -> None:
        """Restore system from backup including network configuration."""
        print_header("Restore System from Backup")
        
        box_line, box_width = create_box_helper(65)
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('System Restore - Disaster Recovery', Colors.BOLD)}"))
        print(draw_box_divider(box_width))
        print(box_line(" This will restore:"))
        print(box_line("   • Instance registry and metadata"))
        print(box_line("   • Traefik configuration + SSL certificates"))
        print(box_line("   • Cloudflare tunnel configs and credentials"))
        print(box_line("   • Backup server (rclone) configuration"))
        print(draw_box_divider(box_width))
        print(box_line(f" {colorize('Note:', Colors.YELLOW)} Tailscale requires re-authentication"))
        print(draw_box_bottom(box_width))
        print()
        
        try:
            result = subprocess.run(
                ["rclone", "lsd", "pcloud:backups/paperless-system"],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode != 0 or not result.stdout.strip():
                warn("No system backups found")
                input("\nPress Enter to continue...")
                return
            
            backups = sorted([l.split()[-1] for l in result.stdout.splitlines() if l.strip()], reverse=True)
            
            print(colorize("Available system backups:", Colors.BOLD))
            print()
            
            for idx, backup in enumerate(backups, 1):
                # Get backup info
                try:
                    info = subprocess.run(
                        ["rclone", "cat", f"pcloud:backups/paperless-system/{backup}/system-info.json"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if info.returncode == 0:
                        import json
                        data = json.loads(info.stdout)
                        inst_count = data.get("instance_count", "?")
                        version = data.get("backup_version", "1.0")
                        network = "+" if data.get("network") else ""
                        print(f"  {idx}) {backup} - {inst_count} instance(s) {f'[v{version} network]' if network else ''}")
                    else:
                        print(f"  {idx}) {backup}")
                except:
                    print(f"  {idx}) {backup}")
            print()
            
            choice = get_input(f"Select backup [1-{len(backups)}] or 'cancel'", "cancel")
            
            if not choice.isdigit() or not (1 <= int(choice) <= len(backups)):
                return
            
            backup_name = backups[int(choice) - 1]
            
            # Download and parse system info
            say("Downloading system backup...")
            import json
            import tempfile
            import shutil
            
            work = Path(tempfile.mkdtemp(prefix="paperless-system-restore-"))
            subprocess.run(
                ["rclone", "copy", f"pcloud:backups/paperless-system/{backup_name}", str(work)],
                check=True,
                stdout=subprocess.DEVNULL
            )
            
            system_info = json.loads((work / "system-info.json").read_text())
            network_info = system_info.get("network", {})
            backup_version = system_info.get("backup_version", "1.0")
            
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Backup Details', Colors.BOLD)}"))
            print(draw_box_divider(box_width))
            print(box_line(f" Name: {backup_name}"))
            print(box_line(f" Date: {system_info['backup_date'][:19]}"))
            print(box_line(f" Instances: {system_info['instance_count']}"))
            print(box_line(f" Version: {backup_version}"))
            print(draw_box_divider(box_width))
            
            if backup_version >= "2.0" and network_info:
                print(box_line(f" {colorize('Network Configuration:', Colors.BOLD)}"))
                traefik_info = network_info.get("traefik", {})
                cf_info = network_info.get("cloudflare", {})
                rclone_info = network_info.get("rclone", {})
                ts_info = network_info.get("tailscale", {})
                
                if traefik_info.get("enabled"):
                    print(box_line(f"   ✓ Traefik + SSL certificates"))
                else:
                    print(box_line(f"   ○ Traefik: not configured"))
                
                if cf_info.get("enabled"):
                    tunnels = cf_info.get("tunnels", [])
                    print(box_line(f"   ✓ Cloudflare: {len(tunnels)} tunnel(s)"))
                else:
                    print(box_line(f"   ○ Cloudflare: not configured"))
                
                if rclone_info.get("enabled"):
                    print(box_line(f"   ✓ rclone backup config"))
                else:
                    print(box_line(f"   ○ rclone: not configured"))
                
                if ts_info.get("enabled"):
                    print(box_line(f"   ⚠ Tailscale: requires re-auth"))
                else:
                    print(box_line(f"   ○ Tailscale: not configured"))
            else:
                print(box_line(f" {colorize('Note:', Colors.YELLOW)} Legacy backup (no network config)"))
                traefik_info = {"enabled": system_info.get("traefik_enabled", False)}
                cf_info = {}
                rclone_info = {}
                ts_info = {}
            
            print(draw_box_divider(box_width))
            print(box_line(" Instances:"))
            for inst_name, inst_data in system_info["instances"].items():
                latest = inst_data.get("latest_backup", "no backup")
                print(box_line(f"   • {inst_name}: {latest[:19] if latest != 'no backup' else latest}"))
            print(draw_box_bottom(box_width))
            print()
            
            if not confirm("Restore this system configuration?", False):
                shutil.rmtree(work)
                return
            
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import traefik, cloudflared
            
            # ─── Restore Network Configurations ───────────────────────────────
            print()
            say("Restoring Network Configuration...")
            
            # Restore rclone config first (needed for other restores)
            rclone_backup = work / "network" / "rclone"
            if rclone_backup.exists() and (rclone_backup / "rclone.conf").exists():
                say("Restoring rclone configuration...")
                rclone_dest = Path.home() / ".config" / "rclone"
                rclone_dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rclone_backup / "rclone.conf", rclone_dest / "rclone.conf")
                ok("rclone config restored")
            
            # Restore Traefik
            traefik_backup = work / "network" / "traefik"
            if traefik_backup.exists() and traefik_info.get("enabled"):
                say("Restoring Traefik configuration...")
                traefik_dest = Path("/opt/traefik")
                traefik_dest.mkdir(parents=True, exist_ok=True)
                
                # Copy traefik.yml
                if (traefik_backup / "traefik.yml").exists():
                    shutil.copy2(traefik_backup / "traefik.yml", traefik_dest / "traefik.yml")
                
                # Copy acme.json (SSL certs) with correct permissions
                if (traefik_backup / "acme.json").exists():
                    shutil.copy2(traefik_backup / "acme.json", traefik_dest / "acme.json")
                    (traefik_dest / "acme.json").chmod(0o600)
                
                # Start Traefik if not running
                if not traefik.is_traefik_running():
                    say("Starting Traefik with restored certificates...")
                    # Check if docker network exists
                    subprocess.run(
                        ["docker", "network", "create", "web"],
                        capture_output=True,
                        check=False
                    )
                    # Start Traefik
                    result = subprocess.run(
                        ["docker", "compose", "-f", "/opt/traefik/docker-compose.yml", "up", "-d"],
                        capture_output=True,
                        check=False
                    )
                    if result.returncode == 0:
                        ok("Traefik started with restored SSL certificates")
                    else:
                        # May need to set up fresh
                        email = traefik_info.get("email", "admin@example.com")
                        if traefik.setup_system_traefik(email):
                            ok("Traefik reinstalled (will regenerate SSL certs)")
                        else:
                            warn("Failed to start Traefik")
                else:
                    ok("Traefik already running")
            elif traefik_info.get("enabled") and not traefik_backup.exists():
                # Legacy backup - just install Traefik
                if not traefik.is_traefik_running():
                    say("System backup had Traefik enabled. Installing Traefik...")
                    email = get_input("Let's Encrypt email for SSL certificates", "admin@example.com")
                    if traefik.setup_system_traefik(email):
                        ok("Traefik installed and running")
                    else:
                        warn("Failed to install Traefik - HTTPS instances may not work")
            
            # Restore Cloudflare tunnel configs
            cf_backup = work / "network" / "cloudflared"
            if cf_backup.exists() and cf_info.get("enabled"):
                say("Restoring Cloudflare Tunnel configuration...")
                
                # Restore ~/.cloudflared/ (credentials and cert)
                home_backup = cf_backup / "home"
                if home_backup.exists():
                    cloudflared_home = Path.home() / ".cloudflared"
                    cloudflared_home.mkdir(parents=True, exist_ok=True)
                    
                    for file in home_backup.iterdir():
                        shutil.copy2(file, cloudflared_home / file.name)
                    ok("Cloudflare credentials restored")
                
                # Restore /etc/cloudflared/ configs
                etc_backup = cf_backup / "etc"
                if etc_backup.exists():
                    cloudflared_etc = Path("/etc/cloudflared")
                    cloudflared_etc.mkdir(parents=True, exist_ok=True)
                    
                    for file in etc_backup.iterdir():
                        shutil.copy2(file, cloudflared_etc / file.name)
                    ok(f"Cloudflare tunnel configs restored")
                
                # Restore systemd services
                services_backup = cf_backup / "services"
                if services_backup.exists():
                    for service_file in services_backup.iterdir():
                        shutil.copy2(service_file, Path("/etc/systemd/system") / service_file.name)
                    
                    # Reload and start services
                    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, check=False)
                    
                    # Start each tunnel service
                    for service_file in services_backup.iterdir():
                        service_name = service_file.name
                        subprocess.run(
                            ["systemctl", "enable", "--now", service_name],
                            capture_output=True,
                            check=False
                        )
                    ok("Cloudflare tunnel services started")
            
            # ─── Restore Instance Registry ────────────────────────────────────
            print()
            say("Restoring Instance Registry...")
            
            if "instances_registry" in system_info:
                self.instance_manager.config_file.parent.mkdir(parents=True, exist_ok=True)
                self.instance_manager.config_file.write_text(
                    json.dumps(system_info["instances_registry"], indent=2)
                )
                self.instance_manager.load_instances()
                ok(f"Restored {len(system_info['instances'])} instance(s) to registry")
            
            # ─── Summary & IP Change Guidance ─────────────────────────────────
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('✓ System Restore Complete', Colors.GREEN)}"))
            print(draw_box_divider(box_width))
            
            # Basic next steps
            print(box_line(f" {colorize('Essential Next Steps:', Colors.BOLD)}"))
            print(box_line(""))
            print(box_line("   1. Restore each instance's data:"))
            print(box_line("      → Manage Instances → [instance] → Restore from backup"))
            print(box_line(""))
            print(box_line("   2. Start instances after data is restored"))
            print(draw_box_divider(box_width))
            
            # IP/Server change guidance
            print(box_line(f" {colorize('If Server IP Changed:', Colors.YELLOW)}"))
            print(box_line(""))
            
            # Traefik guidance
            if traefik_info.get("enabled"):
                print(box_line(f"   {colorize('Traefik (HTTPS):', Colors.CYAN)}"))
                print(box_line("   → Update DNS A records to point to new IP"))
                print(box_line("   → SSL certs restored (will auto-renew)"))
                print(box_line(""))
            
            # Cloudflare guidance
            if cf_info.get("enabled"):
                print(box_line(f"   {colorize('Cloudflare Tunnels:', Colors.CYAN)}"))
                print(box_line("   → Tunnels auto-reconnect (IP doesn't matter)"))
                print(box_line("   → Check: systemctl status cloudflared-*"))
                print(box_line("   → If issues: cloudflared service install"))
                print(box_line(""))
            
            # Tailscale guidance  
            if ts_info.get("enabled"):
                print(box_line(f"   {colorize('Tailscale:', Colors.CYAN)}"))
                print(box_line(f"   → Previous: {ts_info.get('hostname', '?')} ({ts_info.get('ip', '?')})"))
                print(box_line("   → Re-authenticate: sudo tailscale up"))
                print(box_line("   → Re-enable serve paths for each instance"))
                print(box_line(""))
            
            # If no network config needed special handling
            if not (traefik_info.get("enabled") or cf_info.get("enabled") or ts_info.get("enabled")):
                print(box_line("   → No network services need reconfiguration"))
                print(box_line(""))
            
            print(draw_box_bottom(box_width))
            
            # Print command reference
            print()
            print(colorize("Useful Commands:", Colors.BOLD))
            print("  paperless                     - Open management TUI")
            print("  systemctl status cloudflared-*  - Check Cloudflare tunnels")
            print("  tailscale status              - Check Tailscale connection")
            print("  docker ps                     - Check running containers")
            
            shutil.rmtree(work)
            
        except Exception as e:
            error(f"System restore failed: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def backups_menu(self) -> None:
        """Backups explorer and management."""
        while True:
            print_header("Backup Explorer")
            
            say("Scanning backup server...")
            
            try:
                # Get all instance folders from backup
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn("No backups found or unable to connect")
                    input("\nPress Enter to continue...")
                    return
                
                # Parse instance names
                backup_instances = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if parts:
                        backup_instances.append(parts[-1])
                
                if not backup_instances:
                    warn("No backup instances found")
                    input("\nPress Enter to continue...")
                    return
                
                # Show instances
                print(f"Backed up instances ({len(backup_instances)}):")
                for idx, name in enumerate(backup_instances, 1):
                    # Count snapshots for this instance
                    snap_result = subprocess.run(
                        ["rclone", "lsd", f"pcloud:backups/paperless/{name}"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5
                    )
                    snap_count = len([l for l in snap_result.stdout.splitlines() if l.strip()])
                    print(f"  {idx}) {name} ({snap_count} snapshots)")
                print()
                
                options = [(str(i), f"Explore '{backup_instances[i-1]}'" ) for i in range(1, len(backup_instances) + 1)]
                options.append((str(len(backup_instances) + 1), colorize("🧹", Colors.YELLOW) + " Clean empty folders (auto)"))
                options.append((str(len(backup_instances) + 2), colorize("🧹", Colors.YELLOW) + " Clean empty folders (select)"))
                options.append(("0", "Back to main menu"))
                print_menu(options)
                
                choice = get_input("Select instance", "")
                
                if choice == "0":
                    break
                elif choice.isdigit() and 1 <= int(choice) <= len(backup_instances):
                    self._explore_instance_backups(backup_instances[int(choice) - 1])
                elif choice == str(len(backup_instances) + 1):
                    self._clean_empty_backup_folders()
                elif choice == str(len(backup_instances) + 2):
                    self._clean_empty_backup_folders_selective()
                else:
                    warn("Invalid option")
                    
            except Exception as e:
                error(f"Failed to list backups: {e}")
                input("\nPress Enter to continue...")
                return
    
    def _explore_instance_backups(self, instance_name: str) -> None:
        """Explore backups for a specific instance."""
        while True:
            print_header(f"Backups: {instance_name}")
            
            remote_path = f"pcloud:backups/paperless/{instance_name}"
            
            try:
                # Get snapshots
                result = subprocess.run(
                    ["rclone", "lsd", remote_path],
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn(f"No snapshots found for {instance_name}")
                    input("\nPress Enter to continue...")
                    return
                
                # Parse snapshots with metadata
                snapshots = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if not parts:
                        continue
                    snap_name = parts[-1]
                    
                    # Get manifest info
                    mode = parent = created = "?"
                    manifest = subprocess.run(
                        ["rclone", "cat", f"{remote_path}/{snap_name}/manifest.yaml"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if manifest.returncode == 0:
                        for mline in manifest.stdout.splitlines():
                            if ":" in mline:
                                k, v = mline.split(":", 1)
                                k, v = k.strip(), v.strip()
                                if k == "mode":
                                    mode = v
                                elif k == "parent":
                                    parent = v
                                elif k == "created":
                                    created = v[:19]  # Just date/time
                    
                    # Check for docker versions file
                    has_versions = subprocess.run(
                        ["rclone", "lsf", f"{remote_path}/{snap_name}/docker-images.txt"],
                        capture_output=True,
                        check=False
                    ).returncode == 0
                    
                    snapshots.append((snap_name, mode, parent, created, has_versions))
                
                snapshots = sorted(snapshots, key=lambda x: x[0])
                
                # Display snapshots
                print(colorize("Available Snapshots:", Colors.BOLD))
                print()
                print(f"{colorize('#', Colors.BOLD):<5} {colorize('Snapshot Name', Colors.BOLD):<30} {colorize('Mode', Colors.BOLD):<10} {colorize('Created', Colors.BOLD):<20} {colorize('Docker', Colors.BOLD)}")
                print(colorize("─" * 85, Colors.CYAN))
                
                for idx, (name, mode, parent, created, has_vers) in enumerate(snapshots, 1):
                    mode_color = Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN
                    vers_icon = colorize("✓", Colors.GREEN) if has_vers else colorize("✗", Colors.RED)
                    print(f"{idx:<5} {name:<30} {colorize(mode.upper(), mode_color):<20} {created:<20} {vers_icon}")
                print()
                
                # Options
                options = []
                for i in range(1, len(snapshots) + 1):
                    options.append((str(i), f"View details of snapshot #{i}"))
                options.append((str(len(snapshots) + 1), colorize("↻", Colors.GREEN) + " Restore to new instance"))
                options.append((str(len(snapshots) + 2), colorize("✗", Colors.RED) + " Delete snapshot"))
                options.append((str(len(snapshots) + 3), colorize("🗑", Colors.RED) + " Delete entire backup folder"))
                options.append(("0", colorize("◀ Back", Colors.CYAN)))
                print_menu(options)
                
                choice = get_input("Select option", "")
                
                if choice == "0":
                    break
                elif choice.isdigit() and 1 <= int(choice) <= len(snapshots):
                    self._view_snapshot_details(instance_name, snapshots[int(choice) - 1])
                elif choice == str(len(snapshots) + 1):
                    self._restore_from_explorer(instance_name, snapshots)
                elif choice == str(len(snapshots) + 2):
                    self._delete_snapshot(instance_name, snapshots)
                elif choice == str(len(snapshots) + 3):
                    self._delete_instance_backup_folder(instance_name, len(snapshots))
                else:
                    warn("Invalid option")
                    
            except Exception as e:
                error(f"Failed to explore backups: {e}")
                input("\nPress Enter to continue...")
                return
    
    def _view_snapshot_details(self, instance_name: str, snapshot: tuple) -> None:
        """View detailed information about a snapshot."""
        name, mode, parent, created, has_versions = snapshot
        
        print_header(f"Snapshot: {name}")
        
        box_line, box_width = create_box_helper(82)
        
        print(draw_box_top(box_width))
        print(box_line(f" Instance:  {colorize(instance_name, Colors.BOLD)}"))
        print(box_line(f" Snapshot:  {name}"))
        mode_display = colorize(mode.upper(), Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN)
        print(box_line(f" Mode:      {mode_display}"))
        print(box_line(f" Created:   {created}"))
        if mode == "incr" and parent != "?":
            print(box_line(f" Parent:    {parent}"))
        print(draw_box_bottom(box_width))
        print()
        
        remote_path = f"pcloud:backups/paperless/{instance_name}/{name}"
        
        # Show Docker versions FIRST and prominently if available
        if has_versions:
            print(colorize("▸ Docker Container Versions at Backup Time:", Colors.BOLD))
            print()
            versions = subprocess.run(
                ["rclone", "cat", f"{remote_path}/docker-images.txt"],
                capture_output=True,
                text=True,
                check=False
            )
            if versions.returncode == 0:
                for line in versions.stdout.strip().splitlines():
                    # Parse and colorize
                    if ":" in line:
                        print(f"  {colorize('•', Colors.GREEN)} {line}")
                    else:
                        print(f"  {line}")
            else:
                warn("Could not load Docker version information")
            print()
        else:
            warn("⚠  No Docker version information in this snapshot")
            print("   (This snapshot was created before version tracking was added)")
            print()
        
        # Show files in snapshot
        print(colorize("▸ Snapshot Contents:", Colors.BOLD))
        print()
        result = subprocess.run(
            ["rclone", "ls", remote_path],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            total_size = 0
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    size_bytes = int(parts[0])
                    total_size += size_bytes
                    filename = parts[1]
                    # Convert to human readable
                    if size_bytes < 1024:
                        size = f"{size_bytes}B"
                    elif size_bytes < 1024 * 1024:
                        size = f"{size_bytes / 1024:.1f}KB"
                    elif size_bytes < 1024 * 1024 * 1024:
                        size = f"{size_bytes / (1024 * 1024):.1f}MB"
                    else:
                        size = f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"
                    
                    # Color-code file types
                    if filename.endswith('.tar.gz'):
                        filename = colorize(filename, Colors.CYAN)
                    elif filename.endswith('.sql'):
                        filename = colorize(filename, Colors.GREEN)
                    elif filename.endswith('.yaml') or filename.endswith('.yml'):
                        filename = colorize(filename, Colors.YELLOW)
                    
                    print(f"  {size:>10}  {filename}")
            
            # Show total
            if total_size > 0:
                if total_size < 1024 * 1024 * 1024:
                    total = f"{total_size / (1024 * 1024):.1f}MB"
                else:
                    total = f"{total_size / (1024 * 1024 * 1024):.2f}GB"
                print()
                print(f"  {colorize('Total:', Colors.BOLD)} {colorize(total, Colors.GREEN)}")
        
        print()
        input("\nPress Enter to continue...")

    def _clean_empty_backup_folders(self) -> None:
        """Scan and delete empty instance backup folders (lists before deleting)."""
        print_header("Clean Empty Backup Folders")
        try:
            # List instance directories
            result = subprocess.run(
                ["rclone", "lsd", "pcloud:backups/paperless"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            if result.returncode != 0:
                error("Unable to list backup root")
                input("\nPress Enter to continue...")
                return
            instance_dirs = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
            empty = []
            for name in instance_dirs:
                check = subprocess.run(
                    ["rclone", "lsd", f"pcloud:backups/paperless/{name}"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                # Consider empty when there are no snapshot subfolders
                if not check.stdout.strip():
                    empty.append(name)
            
            if not empty:
                ok("No empty backup folders found")
                input("\nPress Enter to continue...")
                return
            
            print(colorize("Empty backup folders:", Colors.BOLD))
            for name in empty:
                print(f"  • {name}")
            print()
            if confirm("Delete ALL listed empty folders?", False):
                deleted = 0
                for name in empty:
                    try:
                        # Purge (safe even if empty); ensures removal across remotes
                        subprocess.run(["rclone", "purge", f"pcloud:backups/paperless/{name}"], check=False)
                        deleted += 1
                    except Exception:
                        pass
                ok(f"Deleted {deleted}/{len(empty)} empty folders")
            else:
                say("No changes made")
        except Exception as e:
            error(f"Cleanup failed: {e}")
        input("\nPress Enter to continue...")

    def _clean_empty_backup_folders_selective(self) -> None:
        """List empty instance backup folders and allow selective deletion."""
        print_header("Clean Empty Folders (Select)")
        try:
            result = subprocess.run(
                ["rclone", "lsd", "pcloud:backups/paperless"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            if result.returncode != 0:
                error("Unable to list backup root")
                input("\nPress Enter to continue...")
                return
            instance_dirs = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
            empties = []
            for name in instance_dirs:
                check = subprocess.run(
                    ["rclone", "lsd", f"pcloud:backups/paperless/{name}"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                if not check.stdout.strip():
                    empties.append(name)
            if not empties:
                ok("No empty backup folders found")
                input("\nPress Enter to continue...")
                return
            print(colorize("Empty folders:", Colors.BOLD))
            for idx, name in enumerate(empties, 1):
                print(f"  {idx}) {name}")
            print()
            choice = get_input("Enter numbers to delete (space-separated), 'all' or 'cancel'", "cancel")
            if choice == "cancel":
                say("Cancelled")
                input("\nPress Enter to continue...")
                return
            targets = empties if choice.strip().lower() == "all" else []
            if not targets:
                for part in choice.split():
                    if part.isdigit() and 1 <= int(part) <= len(empties):
                        targets.append(empties[int(part) - 1])
            if not targets:
                warn("No valid selections")
                input("\nPress Enter to continue...")
                return
            if confirm(f"Delete {len(targets)} empty folder(s)?", False):
                deleted = 0
                for name in targets:
                    try:
                        subprocess.run(["rclone", "purge", f"pcloud:backups/paperless/{name}"], check=False)
                        deleted += 1
                    except Exception:
                        pass
                ok(f"Deleted {deleted}/{len(targets)} folders")
            else:
                say("No changes made")
        except Exception as e:
            error(f"Cleanup failed: {e}")
        input("\nPress Enter to continue...")
    
    def _restore_from_explorer(self, instance_name: str, snapshots: list) -> None:
        """Restore a snapshot to a new instance - uses unified restore method."""
        print("Select snapshot to restore:")
        for idx, (name, mode, parent, created, _) in enumerate(snapshots, 1):
            print(f"  {idx}) {name} ({mode}, {created})")
        print()
        
        choice = get_input(f"Select snapshot [1-{len(snapshots)}] or 'latest'", "latest")
        
        if choice == "latest":
            snapshot_name = snapshots[-1][0]
        elif choice.isdigit() and 1 <= int(choice) <= len(snapshots):
            snapshot_name = snapshots[int(choice) - 1][0]
        else:
            return
        
        # Use the unified restore method
        self.restore_instance_from_backup(backup_instance=instance_name, snapshot=snapshot_name)
    
    def _delete_snapshot(self, instance_name: str, snapshots: list) -> None:
        """Delete a snapshot from backup server."""
        print_header("Delete Snapshot")
        
        warn("⚠️  DANGER: This permanently deletes the backup!")
        print()
        
        print("Select snapshot to DELETE:")
        for idx, (name, mode, parent, created, _) in enumerate(snapshots, 1):
            print(f"  {idx}) {name} ({mode}, {created})")
        print()
        
        choice = get_input(f"Select snapshot [1-{len(snapshots)}] or 'cancel'", "cancel")
        
        if not choice.isdigit() or not (1 <= int(choice) <= len(snapshots)):
            return
        
        snapshot_name = snapshots[int(choice) - 1][0]
        
        print()
        if confirm(f"PERMANENTLY DELETE {instance_name}/{snapshot_name}?", False):
            remote_path = f"pcloud:backups/paperless/{instance_name}/{snapshot_name}"
            try:
                say(f"Deleting {snapshot_name}...")
                subprocess.run(["rclone", "purge", remote_path], check=True)
                ok("Snapshot deleted")
            except Exception as e:
                error(f"Failed to delete snapshot: {e}")
        
        input("\nPress Enter to continue...")

    def _delete_instance_backup_folder(self, instance_name: str, snapshot_count: int) -> None:
        """Delete the entire backup folder for an instance (warn if non-empty)."""
        print_header("Delete Backup Folder")
        if snapshot_count > 0:
            warn(f"Folder '{instance_name}' contains {snapshot_count} snapshot(s)")
        else:
            say(f"Folder '{instance_name}' is empty")
        print()
        confirm_text = get_input(f"Type DELETE {instance_name} to confirm (or just 'DELETE')", "")
        if confirm_text not in (f"DELETE {instance_name}", "DELETE"):
            say("Cancelled")
            input("\nPress Enter to continue...")
            return
        try:
            subprocess.run(["rclone", "purge", f"pcloud:backups/paperless/{instance_name}"], check=False)
            ok(f"Deleted backup folder '{instance_name}'")
        except Exception as e:
            error(f"Failed to delete folder: {e}")
        input("\nPress Enter to continue...")
    
    def cloudflared_menu(self) -> None:
        """Cloudflare Tunnel management menu."""
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import cloudflared
        
        while True:
            print_header("Manage Cloudflare Tunnel")
            
            installed = cloudflared.is_cloudflared_installed()
            authenticated = cloudflared.is_authenticated() if installed else False
            
            if not installed:
                say(colorize("⚠ Cloudflared not installed", Colors.YELLOW))
                print("\nCloudflare Tunnel provides secure access without exposing ports.")
                print()
                options = [("1", "Install cloudflared"), ("0", "Back to main menu")]
            elif not authenticated:
                say(colorize("⚠ Not authenticated with Cloudflare", Colors.YELLOW))
                print()
                options = [("1", "Authenticate with Cloudflare"), ("0", "Back to main menu")]
            else:
                say(colorize("✓ Cloudflared installed and authenticated", Colors.GREEN))
                
                # Show per-instance tunnel status
                instances = self.instance_manager.list_instances()
                tunnels = cloudflared.list_tunnels()
                paperless_tunnels = [t for t in tunnels if t.get('name', '').startswith('paperless-')]
                
                if instances:
                    print()
                    print(colorize("Instance Tunnel Status:", Colors.BOLD))
                    for inst in instances:
                        tunnel = cloudflared.get_tunnel_for_instance(inst.name)
                        cf_enabled = inst.get_env_value("ENABLE_CLOUDFLARED", "no") == "yes"
                        
                        # Check if service is running
                        service_active = False
                        try:
                            result = subprocess.run(
                                ["systemctl", "is-active", f"cloudflared-{inst.name}"],
                                capture_output=True, check=False
                            )
                            service_active = result.returncode == 0
                        except:
                            pass
                        
                        if tunnel and service_active:
                            status = colorize("● Active", Colors.GREEN)
                            domain = inst.get_env_value("DOMAIN", "?")
                            print(f"  {inst.name}: {status} → https://{domain}")
                        elif tunnel:
                            status = colorize("○ Configured", Colors.YELLOW)
                            print(f"  {inst.name}: {status} (tunnel exists, service stopped)")
                        elif cf_enabled:
                            status = colorize("⚠ Misconfigured", Colors.RED)
                            print(f"  {inst.name}: {status} (enabled but no tunnel)")
                        else:
                            status = colorize("○ Not enabled", Colors.CYAN)
                            print(f"  {inst.name}: {status}")
                
                print()
                options = [
                    ("1", "List all tunnels"),
                    ("2", "Enable tunnel for an instance"),
                    ("3", "Disable tunnel for an instance"),
                    ("0", "Back to main menu")
                ]
            
            print_menu(options)
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if not installed:
                    if cloudflared.install_cloudflared():
                        ok("Cloudflared installed!")
                    else:
                        error("Installation failed")
                elif not authenticated:
                    if cloudflared.authenticate():
                        ok("Authentication successful!")
                    else:
                        error("Authentication failed")
                else:
                    # List tunnels
                    tunnels = cloudflared.list_tunnels()
                    if tunnels:
                        print()
                        for t in tunnels:
                            print(f"  {t.get('name')} - {t.get('id')}")
                    else:
                        say("No tunnels found")
                input("\nPress Enter to continue...")
            elif choice == "2" and authenticated:
                # Enable tunnel for an instance
                instances = self.instance_manager.list_instances()
                available = [i for i in instances if i.get_env_value("ENABLE_CLOUDFLARED", "no") != "yes"]
                if not available:
                    say("All instances already have Cloudflare enabled")
                else:
                    print("\nSelect instance to enable Cloudflare tunnel:")
                    for idx, inst in enumerate(available, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(available)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(available):
                        inst = available[int(sel) - 1]
                        self._toggle_instance_cloudflare(inst)
                input("\nPress Enter to continue...")
            elif choice == "3" and authenticated:
                # Disable tunnel for an instance
                instances = self.instance_manager.list_instances()
                enabled = [i for i in instances if i.get_env_value("ENABLE_CLOUDFLARED", "no") == "yes"]
                if not enabled:
                    say("No instances have Cloudflare enabled")
                else:
                    print("\nSelect instance to disable Cloudflare tunnel:")
                    for idx, inst in enumerate(enabled, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(enabled)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(enabled):
                        inst = enabled[int(sel) - 1]
                        self._toggle_instance_cloudflare(inst)
                input("\nPress Enter to continue...")
    
    def tailscale_menu(self) -> None:
        """Tailscale management menu."""
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import tailscale
        
        while True:
            print_header("Manage Tailscale")
            
            installed = tailscale.is_tailscale_installed()
            connected = tailscale.is_connected() if installed else False
            
            if not installed:
                say(colorize("⚠ Tailscale not installed", Colors.YELLOW))
                print("\nTailscale provides secure private network access.")
                print()
                options = [("1", "Install Tailscale"), ("0", "Back to main menu")]
            elif not connected:
                say(colorize("⚠ Tailscale not connected", Colors.YELLOW))
                print()
                options = [
                    ("1", "Connect to Tailscale"),
                    ("0", "Back to main menu")
                ]
            else:
                say(colorize("✓ Tailscale connected", Colors.GREEN))
                ip = tailscale.get_ip()
                hostname = tailscale.get_hostname()
                serve_available = tailscale.is_serve_available()
                
                if hostname:
                    print(f"Hostname: {colorize(hostname, Colors.CYAN)}")
                if ip:
                    print(f"IP: {colorize(ip, Colors.CYAN)}")
                if serve_available:
                    print(f"Tailscale Serve: {colorize('Available', Colors.GREEN)}")
                else:
                    print(f"Tailscale Serve: {colorize('Not available (requires paid plan)', Colors.YELLOW)}")
                
                # Show per-instance Tailscale status
                instances = self.instance_manager.list_instances()
                if instances:
                    print()
                    print(colorize("Instance Tailscale Status:", Colors.BOLD))
                    for inst in instances:
                        ts_enabled = inst.get_env_value("ENABLE_TAILSCALE", "no") == "yes"
                        port = inst.get_env_value("HTTP_PORT", "8000")
                        serve_path = inst.get_env_value("TAILSCALE_SERVE_PATH", "")
                        
                        if ts_enabled:
                            if serve_path and hostname:
                                status = colorize("● HTTPS", Colors.GREEN)
                                print(f"  {inst.name}: {status} → https://{hostname}{serve_path}")
                            else:
                                status = colorize("● HTTP", Colors.GREEN)
                                print(f"  {inst.name}: {status} → http://{ip}:{port}")
                        else:
                            status = colorize("○ Not enabled", Colors.CYAN)
                            print(f"  {inst.name}: {status}")
                
                # Show current Tailscale Serve paths
                serve_paths = tailscale.list_serve_paths()
                if serve_paths:
                    print()
                    print(colorize("Active Tailscale Serve paths:", Colors.BOLD))
                    for path, target, port in serve_paths:
                        print(f"  {path} → {target}")
                
                print()
                options = [
                    ("1", "View status"),
                    ("2", "Enable Tailscale for an instance"),
                    ("3", "Disable Tailscale for an instance"),
                ]
                if serve_available:
                    options.append(("4", "Manage Tailscale Serve paths"))
                options.extend([
                    ("5", "Disconnect from Tailscale"),
                    ("0", "Back to main menu")
                ])
            
            print_menu(options)
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if not installed:
                    if tailscale.install_tailscale():
                        ok("Tailscale installed!")
                    else:
                        error("Installation failed")
                elif not connected:
                    if tailscale.connect():
                        ok("Connected to Tailscale!")
                    else:
                        error("Connection failed")
                else:
                    # Show status
                    print(tailscale.get_status())
                input("\nPress Enter to continue...")
            elif choice == "2" and connected:
                # Enable Tailscale for an instance
                instances = self.instance_manager.list_instances()
                available = [i for i in instances if i.get_env_value("ENABLE_TAILSCALE", "no") != "yes"]
                if not available:
                    say("All instances already have Tailscale enabled")
                else:
                    print("\nSelect instance to enable Tailscale:")
                    for idx, inst in enumerate(available, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(available)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(available):
                        inst = available[int(sel) - 1]
                        self._toggle_instance_tailscale(inst)
                input("\nPress Enter to continue...")
            elif choice == "3" and connected:
                # Disable Tailscale for an instance
                instances = self.instance_manager.list_instances()
                enabled = [i for i in instances if i.get_env_value("ENABLE_TAILSCALE", "no") == "yes"]
                if not enabled:
                    say("No instances have Tailscale enabled")
                else:
                    print("\nSelect instance to disable Tailscale:")
                    for idx, inst in enumerate(enabled, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(enabled)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(enabled):
                        inst = enabled[int(sel) - 1]
                        self._toggle_instance_tailscale(inst)
                input("\nPress Enter to continue...")
            elif choice == "4" and connected and tailscale.is_serve_available():
                # Manage Tailscale Serve
                self._tailscale_serve_menu(tailscale)
            elif choice == "5" and connected:
                if tailscale.disconnect():
                    ok("Disconnected from Tailscale")
                input("\nPress Enter to continue...")
    
    def _tailscale_serve_menu(self, tailscale) -> None:
        """Submenu for managing Tailscale Serve paths."""
        while True:
            print_header("Tailscale Serve Management")
            
            hostname = tailscale.get_hostname()
            if hostname:
                say(f"Your Tailscale hostname: {colorize(hostname, Colors.CYAN)}")
            print()
            
            # Show current serve paths
            serve_paths = tailscale.list_serve_paths()
            if serve_paths:
                print(colorize("Current Serve paths:", Colors.BOLD))
                for idx, (path, target, port) in enumerate(serve_paths, 1):
                    print(f"  {idx}) https://{hostname}{path} → {target}")
                print()
            else:
                say("No Tailscale Serve paths configured")
                print()
            
            # Show instances that could be served
            instances = self.instance_manager.list_instances()
            unserved = []
            for inst in instances:
                serve_path = inst.get_env_value("TAILSCALE_SERVE_PATH", "")
                if not serve_path:
                    unserved.append(inst)
            
            options = [
                ("1", "Add Serve path for an instance"),
                ("2", "Remove a Serve path"),
                ("3", "Reset all Serve paths"),
                ("0", "Back")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if not unserved:
                    say("All instances already have Serve paths")
                else:
                    print("\nSelect instance to add Serve path:")
                    for idx, inst in enumerate(unserved, 1):
                        port = inst.get_env_value("HTTP_PORT", "8000")
                        print(f"  {idx}) {inst.name} (port {port})")
                    
                    sel = get_input(f"Select [1-{len(unserved)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(unserved):
                        inst = unserved[int(sel) - 1]
                        port = inst.get_env_value("HTTP_PORT", "8000")
                        default_path = f"/{inst.name}"
                        
                        path = get_input(f"Serve path (e.g., /{inst.name})", default_path)
                        if not path.startswith("/"):
                            path = "/" + path
                        
                        if tailscale.add_serve(path, int(port)):
                            self._update_instance_env(inst, "TAILSCALE_SERVE_PATH", path)
                            self._update_instance_env(inst, "ENABLE_TAILSCALE", "yes")
                            serve_url = tailscale.get_serve_url(path)
                            ok(f"Serve path added: {serve_url}")
                input("\nPress Enter to continue...")
            elif choice == "2":
                if not serve_paths:
                    say("No serve paths to remove")
                else:
                    print("\nSelect path to remove:")
                    for idx, (path, target, port) in enumerate(serve_paths, 1):
                        print(f"  {idx}) {path}")
                    
                    sel = get_input(f"Select [1-{len(serve_paths)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(serve_paths):
                        path, _, _ = serve_paths[int(sel) - 1]
                        if tailscale.remove_serve(path):
                            # Update instance env if this was linked
                            for inst in instances:
                                if inst.get_env_value("TAILSCALE_SERVE_PATH", "") == path:
                                    self._update_instance_env(inst, "TAILSCALE_SERVE_PATH", "")
                            ok(f"Removed serve path: {path}")
                input("\nPress Enter to continue...")
            elif choice == "3":
                if confirm("Reset all Tailscale Serve paths?", False):
                    if tailscale.reset_serve():
                        # Clear all instance serve paths
                        for inst in instances:
                            self._update_instance_env(inst, "TAILSCALE_SERVE_PATH", "")
                        ok("All serve paths reset")
                input("\nPress Enter to continue...")
    
    def nuke_setup(self) -> None:
        """Nuclear option - delete all instances and Docker resources with optional cleanups."""
        print_header("Nuke Setup (Clean Start)")
        
        instances = self.instance_manager.list_instances()
        
        warn("This will DELETE core system components:")
        print("  • All Docker containers (stopped and running)")
        print("  • All Docker networks")
        print("  • All Docker volumes")
        print("  • All instance directories (/home/docker/*)")
        print("  • All instance tracking data")
        print()
        
        # Optional cleanups
        print(colorize("Optional cleanups (you will be asked):", Colors.YELLOW))
        print("  • Traefik configuration")
        print("  • Cloudflare tunnels")
        print("  • Tailscale connection")
        print("  • All pCloud backups")
        print()
        
        # Single confirmation with NUKE
        confirmation = get_input("Type the word NUKE in capitals to confirm", "")
        if confirmation != "NUKE":
            say("Cancelled - confirmation did not match")
            input("\nPress Enter to continue...")
            return
        
        # Ask about optional cleanups
        delete_traefik = confirm("Also delete Traefik configuration?", False)
        delete_cloudflared = confirm("Also delete all Cloudflare tunnels?", False)
        delete_tailscale = confirm("Also disconnect Tailscale?", False)
        delete_backups = False
        if self.rclone_configured:
            warn("⚠️  DANGER: This will permanently delete ALL backups!")
            delete_backups = confirm("Also delete ALL pCloud backups?", False)
        
        print()
        say("Starting nuclear cleanup...")
        print()
        
        try:
            # Use consolidated instance deletion
            if instances:
                say(f"Deleting {len(instances)} instance(s) with all data...")
                for inst in instances:
                    try:
                        self.instance_manager.remove_instance(inst.name, delete_files=True)
                    except Exception as e:
                        warn(f"Error deleting {inst.name}: {e}")
            
            # Stop all remaining containers
            say("Stopping all Docker containers...")
            subprocess.run(
                "docker stop $(docker ps -aq) 2>/dev/null",
                shell=True,
                check=False,
                capture_output=True
            )
            
            # Remove all containers
            say("Removing all Docker containers...")
            subprocess.run(
                "docker rm $(docker ps -aq) 2>/dev/null",
                shell=True,
                check=False,
                capture_output=True
            )
            
            # Remove all networks (except default ones)
            say("Removing Docker networks...")
            result = subprocess.run(
                ["docker", "network", "ls", "--format", "{{.Name}}"],
                capture_output=True,
                text=True,
                check=False
            )
            for network in result.stdout.splitlines():
                if network not in ["bridge", "host", "none"]:
                    subprocess.run(["docker", "network", "rm", network], check=False, capture_output=True)
            
            # Prune volumes
            say("Pruning Docker volumes...")
            subprocess.run(["docker", "volume", "prune", "-f"], check=False, capture_output=True)
            
            # Remove any remaining instance directories
            say("Cleaning remaining instance directories...")
            import shutil
            docker_home = Path("/home/docker")
            if docker_home.exists():
                for item in docker_home.iterdir():
                    try:
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                    except Exception as e:
                        warn(f"Could not remove {item}: {e}")
            
            # Optional: Remove Traefik
            if delete_traefik:
                say("Removing Traefik configuration...")
                traefik_dir = Path("/opt/traefik")
                if traefik_dir.exists():
                    shutil.rmtree(traefik_dir)
                ok("Traefik removed")
            
            # Optional: Delete all Cloudflare tunnels
            if delete_cloudflared:
                say("Deleting all Cloudflare tunnels...")
                sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
                try:
                    from lib.installer.cloudflared import list_tunnels
                    tunnels = list_tunnels()
                    for tunnel in tunnels:
                        if tunnel.get('name', '').startswith('paperless-'):
                            try:
                                subprocess.run(
                                    ["cloudflared", "tunnel", "delete", "-f", tunnel.get('name')],
                                    check=False,
                                    capture_output=True
                                )
                            except:
                                pass
                    ok("Cloudflare tunnels deleted")
                except Exception as e:
                    warn(f"Could not delete tunnels: {e}")
            
            # Optional: Disconnect Tailscale
            if delete_tailscale:
                say("Disconnecting Tailscale...")
                try:
                    subprocess.run(["tailscale", "logout"], check=False, capture_output=True)
                    ok("Tailscale disconnected")
                except:
                    warn("Could not disconnect Tailscale")
            
            # Optional: Delete all backups
            if delete_backups:
                warn("Deleting ALL pCloud backups...")
                try:
                    subprocess.run(
                        ["rclone", "purge", "pcloud:backups/paperless"],
                        check=False,
                        capture_output=True
                    )
                    ok("All backups deleted")
                except Exception as e:
                    warn(f"Could not delete backups: {e}")
            
            # Remove instance tracking
            say("Removing instance tracking...")
            tracking_file = Path("/etc/paperless-bulletproof/instances.json")
            if tracking_file.exists():
                tracking_file.unlink()
            
            # Also remove old tracking file location if it exists
            old_tracking = Path("/root/.paperless_instances.json")
            if old_tracking.exists():
                old_tracking.unlink()
            
            # Reload instance manager to reflect changes
            self.instance_manager = InstanceManager()
            
            ok("Nuclear cleanup complete!")
            say("System is now in clean state")
            if not delete_backups:
                say("Backups preserved on pCloud")
            say("You can start fresh by creating new instances")
            
        except Exception as e:
            error(f"Cleanup error: {e}")
        
        input("\nPress Enter to continue...")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Paperless-NGX Bulletproof Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    args = parser.parse_args()
    
    try:
        app = PaperlessManager()
        app.run()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user\n")
        sys.exit(0)
    except Exception as e:
        error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
