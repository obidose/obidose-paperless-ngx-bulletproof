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
    
    def get_access_mode(self) -> str:
        """Determine how this instance is accessed by probing live status."""
        # Check for active Traefik routing
        try:
            # Check if instance has Traefik labels in docker-compose
            if self.compose_file.exists():
                compose_content = self.compose_file.read_text()
                if "traefik.enable=true" in compose_content:
                    # Verify Traefik is actually running
                    result = subprocess.run(
                        ["docker", "ps", "--filter", "name=traefik-system", "--format", "{{.Names}}"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if "traefik-system" in result.stdout:
                        return "traefik"
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
                return "cloudflared"
        except:
            pass
        
        # Check for Tailscale connectivity
        try:
            result = subprocess.run(
                ["tailscale", "status"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0 and result.stdout.strip():
                # Tailscale is connected, check if this instance is configured for it
                enable_tailscale = self.get_env_value("ENABLE_TAILSCALE", "no")
                if enable_tailscale == "yes":
                    return "tailscale"
        except:
            pass
        
        # Default: direct HTTP access
        return "http"
    
    def get_access_url(self) -> str:
        """Get the access URL with mode indicator by checking live status."""
        mode = self.get_access_mode()
        domain = self.get_env_value("DOMAIN", "localhost")
        
        if mode == "traefik":
            # Traefik with HTTPS
            return f"🔒 https://{domain}"
        elif mode == "cloudflared":
            # Cloudflare Tunnel with HTTPS
            return f"☁️  https://{domain}"
        elif mode == "tailscale":
            # Tailscale with private IP
            try:
                result = subprocess.run(
                    ["tailscale", "ip", "-4"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0 and result.stdout.strip():
                    ip = result.stdout.strip()
                    port = self.get_env_value("HTTP_PORT", "8000")
                    return f"🔐 http://{ip}:{port}"
            except:
                pass
            return f"🔐 {domain}"
        else:
            # Direct HTTP access
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
        """Run a restore operation."""
        script = self.instance.stack_dir / "restore.py"
        if not script.exists():
            error(f"Restore script not found at {script}")
            return False
        
        cmd = [str(script)]
        if snapshot:
            cmd.append(snapshot)
        
        try:
            # Ensure restore script uses the correct instance context
            env = os.environ.copy()
            env.update({
                "ENV_FILE": str(self.instance.env_file),
                "COMPOSE_FILE": str(self.instance.compose_file),
                "STACK_DIR": str(self.instance.stack_dir),
                "DATA_ROOT": str(self.instance.data_root),
                "RCLONE_REMOTE_NAME": self.remote_name,
                "RCLONE_REMOTE_PATH": self.remote_path,
            })
            subprocess.run(cmd, check=True, env=env)
            return True
        except subprocess.CalledProcessError:
            return False


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
    
    def __init__(self):
        self.instance_manager = InstanceManager()
        self.rclone_configured = self._check_rclone_connection()
    
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
        
        # System overview box
        print(colorize("╭──────────────────────────────────────────────────────────╮", Colors.CYAN))
        
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
                    backup_detail += f" • Last: {latest_backup}"
            except:
                backup_status = colorize("✓ Connected", Colors.GREEN)
                backup_detail = "Ready"
        else:
            backup_status = colorize("⚠ Not connected", Colors.YELLOW)
            backup_detail = "Configure to enable backups"
        
        # Calculate padding for proper box alignment
        box_width = 62
        backup_line = f" Backup Server:  {backup_status} {backup_detail}"
        # Strip ANSI codes for length calculation
        import re
        clean_line = re.sub(r'\033\[[0-9;]+m', '', backup_line)
        padding = max(0, box_width - len(clean_line) - 2)
        print(colorize("│", Colors.CYAN) + backup_line + " " * padding + colorize("│", Colors.CYAN))
        
        # Instances status
        if instances:
            instance_status = f"{running_count} running, {stopped_count} stopped"
            instance_line = f" Instances:      {len(instances)} total • {instance_status}"
            clean_line = re.sub(r'\033\[[0-9;]+m', '', instance_line)
            padding = max(0, box_width - len(clean_line) - 2)
            print(colorize("│", Colors.CYAN) + instance_line + " " * padding + colorize("│", Colors.CYAN))
        else:
            no_instances_line = f" Instances:      {colorize('No instances configured', Colors.YELLOW)}"
            clean_line = re.sub(r'\033\[[0-9;]+m', '', no_instances_line)
            padding = max(0, box_width - len(clean_line) - 2)
            print(colorize("│", Colors.CYAN) + no_instances_line + " " * padding + colorize("│", Colors.CYAN))
        
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
        traefik_line = f" Traefik:        {traefik_status}"
        clean_line = re.sub(r'\033\[[0-9;]+m', '', traefik_line)
        padding = max(0, box_width - len(clean_line) - 2)
        print(colorize("│", Colors.CYAN) + traefik_line + " " * padding + colorize("│", Colors.CYAN))
        
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
        cloudflared_line = f" Cloudflare:     {cloudflared_status}"
        clean_line = re.sub(r'\033\[[0-9;]+m', '', cloudflared_line)
        padding = max(0, box_width - len(clean_line) - 2)
        print(colorize("│", Colors.CYAN) + cloudflared_line + " " * padding + colorize("│", Colors.CYAN))
        
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
        tailscale_line = f" Tailscale:      {tailscale_status}"
        clean_line = re.sub(r'\033\[[0-9;]+m', '', tailscale_line)
        padding = max(0, box_width - len(clean_line) - 2)
        print(colorize("│", Colors.CYAN) + tailscale_line + " " * padding + colorize("│", Colors.CYAN))
        
        print(colorize("╰──────────────────────────────────────────────────────────╯", Colors.CYAN))
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
        """Configure rclone/pCloud connection."""
        print_header("Backup Server Connection")
        
        try:
            # Check if running from installed package
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from installer import pcloud
            pcloud.ensure_pcloud_remote_or_menu()
            self.rclone_configured = self._check_rclone_connection()
            ok("Backup connection configured!")
        except Exception as e:
            error(f"Failed to configure backup connection: {e}")
        
        input("\nPress Enter to continue...")
    
    def instances_menu(self) -> None:
        """Instances management menu."""
        while True:
            instances = self.instance_manager.list_instances()
            
            print_header("Instances")
            
            if instances:
                for idx, instance in enumerate(instances, 1):
                    status = colorize("Running", Colors.GREEN) if instance.is_running else colorize("Stopped", Colors.YELLOW)
                    url = instance.get_access_url()
                    print(f"  {idx}) {instance.name} [{status}]")
                    print(f"      Access: {url}")
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
        """Add new instance submenu."""
        print_header("Add New Instance")
        
        options = [
            ("1", "Create fresh instance"),
            ("2", "Restore from backup"),
            ("0", "Back")
        ]
        print_menu(options)
        
        choice = get_input("Select option", "")
        
        if choice == "1":
            self.create_fresh_instance()
        elif choice == "2":
            self.restore_instance_from_backup()
        # else back (0 or any other)
    
    def create_fresh_instance(self) -> None:
        """Create a new fresh instance."""
        print_header("Create Fresh Instance")
        
        say("This will guide you through creating a new Paperless-NGX instance")
        print()
        
        if os.geteuid() != 0:
            error("Creating instances requires root privileges. Please run with sudo.")
            input("\nPress Enter to continue...")
            return
        
        try:
            # Import installer modules
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import common, files, traefik, cloudflared, tailscale
            
            # Run the guided setup (removed preset selection)
            common.prompt_core_values()
            common.prompt_networking()  # New: ask how to access the instance
            common.prompt_backup_plan()
            
            # Check if Traefik is needed and available
            if common.cfg.enable_traefik == "yes":
                if not traefik.is_traefik_running():
                    common.warn("HTTPS enabled but system Traefik is not running!")
                    common.say("You can install Traefik from the main menu: Manage Traefik (HTTPS)")
                    if not confirm("Continue without HTTPS? Instance will be inaccessible until Traefik is installed.", False):
                        common.warn("Instance creation cancelled")
                        input("\nPress Enter to continue...")
                        return
                else:
                    common.ok("Using existing system Traefik for HTTPS routing")
            
            # Check if Cloudflared is needed and available
            if common.cfg.enable_cloudflared == "yes":
                if not cloudflared.is_cloudflared_installed():
                    common.warn("Cloudflare Tunnel enabled but cloudflared not installed!")
                    common.say("Install cloudflared from main menu: Manage Cloudflare Tunnel")
                    if not confirm("Continue without tunnel? Instance will be on port 8000 only.", False):
                        common.warn("Instance creation cancelled")
                        input("\nPress Enter to continue...")
                        return
                elif not cloudflared.is_authenticated():
                    common.warn("Cloudflared installed but not authenticated!")
                    common.say("Authenticate from main menu: Manage Cloudflare Tunnel")
                    if not confirm("Continue without tunnel? You'll need to set it up manually.", False):
                        common.warn("Instance creation cancelled")
                        input("\nPress Enter to continue...")
                        return
            
            # Check if Tailscale is needed
            if common.cfg.enable_tailscale == "yes":
                if not tailscale.is_tailscale_installed():
                    common.warn("Tailscale enabled but not installed!")
                    common.say("Install Tailscale from main menu: Manage Tailscale")
                elif not tailscale.is_connected():
                    common.warn("Tailscale installed but not connected!")
                    common.say("Connect from main menu: Manage Tailscale")
            
            common.ensure_dir_tree(common.cfg)
            
            files.write_env_file()
            files.write_compose_file()
            files.copy_helper_scripts()
            files.bring_up_stack()
            
            # Run self-test
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.utils.selftest import run_stack_tests
            if run_stack_tests(Path(common.cfg.compose_file), Path(common.cfg.env_file)):
                common.ok("Self-test passed")
            else:
                common.warn("Self-test failed; check container logs")
            
            files.install_cron_backup()
            
            # Set up Cloudflare tunnel if enabled
            if common.cfg.enable_cloudflared == "yes" and cloudflared.is_authenticated():
                print()
                common.say("Setting up Cloudflare Tunnel...")
                if cloudflared.create_tunnel(common.cfg.instance_name, common.cfg.domain):
                    common.ok(f"Cloudflare tunnel created for {common.cfg.domain}")
                    common.say("To start the tunnel, run:")
                    print(f"  cloudflared tunnel --config /etc/cloudflared/{common.cfg.instance_name}.yml run")
                    print()
                    if confirm("Start tunnel now as a background service?", True):
                        try:
                            # Create systemd service
                            service_content = f"""[Unit]
Description=Cloudflare Tunnel for {common.cfg.instance_name}
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared tunnel --config /etc/cloudflared/{common.cfg.instance_name}.yml run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""
                            service_file = Path(f"/etc/systemd/system/cloudflared-{common.cfg.instance_name}.service")
                            service_file.write_text(service_content)
                            
                            subprocess.run(["systemctl", "daemon-reload"], check=True)
                            subprocess.run(["systemctl", "enable", f"cloudflared-{common.cfg.instance_name}"], check=True)
                            subprocess.run(["systemctl", "start", f"cloudflared-{common.cfg.instance_name}"], check=True)
                            
                            common.ok(f"Tunnel service started and enabled")
                            common.say(f"Access at: https://{common.cfg.domain}")
                        except Exception as e:
                            common.warn(f"Failed to create service: {e}")
                            common.say("You can start the tunnel manually with the command above")
                else:
                    common.warn("Failed to create Cloudflare tunnel")
                    common.say("You can create it manually from the Cloudflare Tunnel menu")
            
            # Register instance
            self.instance_manager.add_instance(
                common.cfg.instance_name,
                Path(common.cfg.stack_dir),
                Path(common.cfg.data_root)
            )
            
            ok(f"Instance '{common.cfg.instance_name}' created successfully!")
            
        except Exception as e:
            error(f"Failed to create instance: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def restore_instance_from_backup(self) -> None:
        """Restore instance from backup."""
        if not self.rclone_configured:
            error("Backup server not configured!")
            input("\nPress Enter to continue...")
            return
        
        print_header("Restore Instance from Backup")
        
        say("Scanning for available backups...")
        
        # Get rclone remote settings
        remote_name = "pcloud"  # TODO: make configurable
        remote_base = f"{remote_name}:backups/paperless"
        
        try:
            # List all instance folders in backup
            result = subprocess.run(
                ["rclone", "lsd", remote_base],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode != 0 or not result.stdout.strip():
                warn("No backups found")
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
            
            # Show available instances
            print("Available backup instances:")
            for idx, inst_name in enumerate(backup_instances, 1):
                print(f"  {idx}) {inst_name}")
            print()
            
            choice = get_input(f"Select instance [1-{len(backup_instances)}] or 'cancel'", "cancel")
            
            if not choice.isdigit() or not (1 <= int(choice) <= len(backup_instances)):
                return
            
            selected_instance = backup_instances[int(choice) - 1]
            
            # Now show snapshots for this instance
            remote_path = f"{remote_base}/{selected_instance}"
            result = subprocess.run(
                ["rclone", "lsd", remote_path],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode != 0 or not result.stdout.strip():
                warn(f"No snapshots found for {selected_instance}")
                input("\nPress Enter to continue...")
                return
            
            # Parse snapshots
            snapshots = []
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if parts:
                    snapshots.append(parts[-1])
            
            snapshots = sorted(snapshots)
            
            print(f"\nSnapshots for '{selected_instance}':")
            for idx, snap in enumerate(snapshots, 1):
                print(f"  {idx}) {snap}")
            print()
            
            snap_choice = get_input(f"Select snapshot [1-{len(snapshots)}] or 'latest'", "latest")
            
            if snap_choice == "latest":
                snapshot = snapshots[-1]
            elif snap_choice.isdigit() and 1 <= int(snap_choice) <= len(snapshots):
                snapshot = snapshots[int(snap_choice) - 1]
            else:
                return
            
            # Prompt for new instance name
            new_name = get_input("New instance name", selected_instance)
            
            say(f"Restoring {selected_instance}/{snapshot} as '{new_name}'...")
            
            # Set up config for restore
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import common
            
            # Use defaults but allow customization
            common.cfg.instance_name = new_name
            common.cfg.rclone_remote_name = remote_name
            common.cfg.rclone_remote_path = f"backups/paperless/{selected_instance}"
            
            # Run restore
            restore_script = Path(f"/tmp/restore_{new_name}.py")
            restore_script.write_text((Path("/usr/local/lib/paperless-bulletproof") / "lib" / "modules" / "restore.py").read_text())
            
            subprocess.run([sys.executable, str(restore_script), snapshot], check=True)
            
            # Register the restored instance
            self.instance_manager.add_instance(
                new_name,
                Path(common.cfg.stack_dir),
                Path(common.cfg.data_root)
            )
            
            ok(f"Instance '{new_name}' restored successfully!")
            
        except Exception as e:
            error(f"Restore failed: {e}")
        
        input("\nPress Enter to continue...")
    
    def instance_detail_menu(self, instance: Instance) -> None:
        """Detail menu for a specific instance."""
        while True:
            print_header(f"Instance: {instance.name}")
            
            status = colorize("● Running", Colors.GREEN) if instance.is_running else colorize("○ Stopped", Colors.YELLOW)
            domain = instance.get_env_value("DOMAIN", "localhost")
            url = instance.get_env_value("PAPERLESS_URL", "")
            
            print(colorize("╭──────────────────────────────────────────────────────────╮", Colors.CYAN))
            print(colorize("│", Colors.CYAN) + f" Status: {status}" + " " * (58 - len("Status: ") - 10) + colorize("│", Colors.CYAN))
            print(colorize("│", Colors.CYAN) + f" Domain: {colorize(domain, Colors.BOLD)}" + " " * (58 - len("Domain: ") - len(domain)) + colorize("│", Colors.CYAN))
            if url:
                print(colorize("│", Colors.CYAN) + f" URL:    {colorize(url, Colors.CYAN)}" + " " * (58 - len("URL:    ") - len(url)) + colorize("│", Colors.CYAN))
            print(colorize("│", Colors.CYAN) + f" Stack:  {instance.stack_dir}" + " " * (58 - len("Stack:  ") - len(str(instance.stack_dir))) + colorize("│", Colors.CYAN))
            print(colorize("╰──────────────────────────────────────────────────────────╯", Colors.CYAN))
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
        
        # Show key settings from env file
        if instance.env_file.exists():
            print("Settings:")
            for key in ["PAPERLESS_URL", "POSTGRES_DB", "TZ", "ENABLE_TRAEFIK", "DOMAIN"]:
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
        """Edit instance settings."""
        print_header(f"Edit: {instance.name}")
        warn("Instance editing requires stopping containers and updating configuration")
        warn("This feature is under development")
        input("\nPress Enter to continue...")
    
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
            
            print(colorize("╭──────────────────────────────────────────────────────────╮", Colors.CYAN))
            print(colorize("│", Colors.CYAN) + f" Current System: {len(instances)} instance(s) configured" + " " * (58 - len(f" Current System: {len(instances)} instance(s) configured")) + colorize("│", Colors.CYAN))
            print(colorize("│", Colors.CYAN) + f" System Backups: {len(system_backups)} available" + " " * (58 - len(f" System Backups: {len(system_backups)} available")) + colorize("│", Colors.CYAN))
            print(colorize("╰──────────────────────────────────────────────────────────╯", Colors.CYAN))
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
        
        if not confirm("Create system backup?", True):
            return
        
        try:
            from datetime import datetime
            import json
            import tempfile
            
            # Create temp directory for system backup
            backup_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            work = Path(tempfile.mkdtemp(prefix="paperless-system-"))
            
            say(f"Creating system backup: {backup_name}")
            
            # Check if Traefik is running
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import traefik
            traefik_running = traefik.is_traefik_running()
            
            # Backup instances.json
            system_info = {
                "backup_date": datetime.utcnow().isoformat(),
                "backup_name": backup_name,
                "instance_count": len(instances),
                "traefik_enabled": traefik_running,
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
                               "RCLONE_REMOTE_PATH", "INSTANCE_NAME"]:
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
backup_date: {datetime.utcnow().isoformat()}
instance_count: {len(instances)}
"""
            (work / "manifest.yaml").write_text(manifest)
            
            # Upload to pCloud
            remote = f"pcloud:backups/paperless-system/{backup_name}"
            say("Uploading to pCloud...")
            subprocess.run(
                ["rclone", "copy", str(work), remote],
                check=True,
                stdout=subprocess.DEVNULL
            )
            
            ok(f"System backup created: {backup_name}")
            print()
            print("This backup contains:")
            print("  ✓ Instance registry (instances.json)")
            print("  ✓ Metadata for all instances")
            print("  ✓ References to latest data backups")
            print()
            print("To restore: Use 'Restore system from backup' option")
            
            # Cleanup
            import shutil
            shutil.rmtree(work)
            
        except Exception as e:
            error(f"System backup failed: {e}")
        
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
        """Restore system from backup."""
        print_header("Restore System from Backup")
        
        warn("⚠  IMPORTANT: System restore will:")
        print("  • Register all instances from the backup")
        print("  • Restore data from individual instance backups")
        print("  • May overwrite existing instance registry")
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
            
            print("Available system backups:")
            for idx, backup in enumerate(backups, 1):
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
            
            work = Path(tempfile.mkdtemp(prefix="paperless-system-restore-"))
            subprocess.run(
                ["rclone", "copy", f"pcloud:backups/paperless-system/{backup_name}", str(work)],
                check=True,
                stdout=subprocess.DEVNULL
            )
            
            system_info = json.loads((work / "system-info.json").read_text())
            
            print()
            print(f"System backup: {backup_name}")
            print(f"Created: {system_info['backup_date']}")
            print(f"Instances: {system_info['instance_count']}")
            traefik_was_enabled = system_info.get('traefik_enabled', False)
            print(f"Traefik: {'Enabled' if traefik_was_enabled else 'Disabled'}")
            print()
            print("Instances in backup:")
            for inst_name, inst_data in system_info["instances"].items():
                latest = inst_data.get("latest_backup", "no backup")
                print(f"  • {inst_name} - latest backup: {latest}")
            print()
            
            if not confirm("Restore this system configuration?", False):
                import shutil
                shutil.rmtree(work)
                return
            
            # Check if Traefik should be restored
            if traefik_was_enabled:
                sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
                from lib.installer import traefik
                if not traefik.is_traefik_running():
                    say("System backup had Traefik enabled. Installing Traefik...")
                    email = get_input("Let's Encrypt email for SSL certificates", "admin@example.com")
                    if traefik.setup_system_traefik(email):
                        ok("Traefik installed and running")
                    else:
                        warn("Failed to install Traefik - HTTPS instances may not work")
                else:
                    ok("Traefik already running")
            
            # Restore instances registry from system info
            if "instances_registry" in system_info:
                say("Restoring instance registry...")
                self.instance_manager.config_file.parent.mkdir(parents=True, exist_ok=True)
                self.instance_manager.config_file.write_text(
                    json.dumps(system_info["instances_registry"], indent=2)
                )
                self.instance_manager.load_instances()
            
            ok("System configuration restored!")
            print()
            print("Next steps:")
            print("  1. Check 'Manage Instances' to see restored instances")
            print("  2. Use each instance's 'Restore from backup' to restore data")
            print(f"     (Latest backups are shown in the system info above)")
            
            import shutil
            shutil.rmtree(work)
            
        except Exception as e:
            error(f"System restore failed: {e}")
        
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
        
        import re
        box_width = 84  # matches border length below
        def pad_line(content: str) -> str:
            clean = re.sub(r"\033\[[0-9;]+m", "", content)
            padding = max(0, box_width - len(clean) - 2)  # minus borders
            return colorize("│", Colors.CYAN) + content + " " * padding + colorize("│", Colors.CYAN)
        
        print(colorize("╭" + "─" * (box_width - 2) + "╮", Colors.CYAN))
        print(pad_line(f" Instance:  {colorize(instance_name, Colors.BOLD)}"))
        print(pad_line(f" Snapshot:  {name}"))
        mode_display = colorize(mode.upper(), Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN)
        print(pad_line(f" Mode:      {mode_display}"))
        print(pad_line(f" Created:   {created}"))
        if mode == "incr" and parent != "?":
            print(pad_line(f" Parent:    {parent}"))
        print(colorize("╰" + "─" * (box_width - 2) + "╯", Colors.CYAN))
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
        """Restore a snapshot to a new instance."""
        print_header("Restore to New Instance")
        
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
        
        new_instance = get_input("New instance name", f"{instance_name}-restored")
        
        if confirm(f"Restore {instance_name}/{snapshot_name} as '{new_instance}'?", False):
            try:
                say("Restoring instance...")
                
                # Set up config for restore
                sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
                from lib.installer import common
                
                common.cfg.instance_name = new_instance
                common.cfg.rclone_remote_name = "pcloud"
                common.cfg.rclone_remote_path = f"backups/paperless/{instance_name}"
                common.cfg.refresh_paths()
                
                # Set up environment for restore module
                os.environ["INSTANCE_NAME"] = new_instance
                os.environ["STACK_DIR"] = str(common.cfg.stack_dir)
                os.environ["DATA_ROOT"] = str(common.cfg.data_root)
                os.environ["RCLONE_REMOTE_PATH"] = f"backups/paperless/{instance_name}"
                
                # Call restore module
                restore_module = Path("/usr/local/lib/paperless-bulletproof/lib/modules/restore.py")
                subprocess.run([sys.executable, str(restore_module), snapshot_name], check=True)
                
                # Register instance
                self.instance_manager.add_instance(
                    new_instance,
                    Path(common.cfg.stack_dir),
                    Path(common.cfg.data_root)
                )
                
                ok(f"Instance '{new_instance}' restored successfully!")
            except Exception as e:
                error(f"Restore failed: {e}")
        
        input("\nPress Enter to continue...")
    
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
        confirm_text = get_input(f"Type DELETE {instance_name} to confirm", "")
        if confirm_text != f"DELETE {instance_name}":
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
                tunnels = cloudflared.list_tunnels()
                if tunnels:
                    print(f"\nActive tunnels: {len(tunnels)}")
                    for tunnel in tunnels[:5]:
                        print(f"  • {tunnel.get('name')}")
                print()
                options = [
                    ("1", "List all tunnels"),
                    ("2", "View tunnel status"),
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
                        for t in tunnels:
                            print(f"\n{t.get('name')} - {t.get('id')}")
                    else:
                        say("No tunnels found")
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
                if ip:
                    print(f"Tailscale IP: {colorize(ip, Colors.CYAN)}")
                print()
                options = [
                    ("1", "View status"),
                    ("2", "Disconnect"),
                    ("0", "Back to main menu")
                ]
            
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
                if tailscale.disconnect():
                    ok("Disconnected from Tailscale")
                input("\nPress Enter to continue...")
    
    def nuke_setup(self) -> None:
        """Nuclear option - delete all instances and Docker resources."""
        print_header("Nuke Setup (Clean Start)")
        
        warn("This will DELETE EVERYTHING:")
        print("  • All Docker containers (stopped and running)")
        print("  • All Docker networks")
        print("  • All Docker volumes")
        print("  • All instance directories (/home/docker/*)")
        print("  • All instance tracking data")
        print("  • Traefik configuration")
        print()
        error("Backups on pCloud will NOT be deleted")
        print()
        
        # Single confirmation with NUKE
        confirmation = get_input("Type the word NUKE in capitals to confirm", "")
        if confirmation != "NUKE":
            say("Cancelled - confirmation did not match")
            input("\nPress Enter to continue...")
            return
        
        say("Starting nuclear cleanup...")
        print()
        
        try:
            # Stop all containers
            say("Stopping all Docker containers...")
            subprocess.run(
                ["docker", "stop", "$(docker ps -aq)"],
                shell=True,
                check=False,
                capture_output=True
            )
            
            # Remove all containers
            say("Removing all Docker containers...")
            subprocess.run(
                ["docker", "rm", "$(docker ps -aq)"],
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
            
            # Remove instance directories
            say("Removing instance directories...")
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
            
            # Remove Traefik config
            say("Removing Traefik configuration...")
            traefik_dir = Path("/opt/traefik")
            if traefik_dir.exists():
                shutil.rmtree(traefik_dir)
            
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
