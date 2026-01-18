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
    
    def remove_instance(self, name: str) -> None:
        """Remove an instance from tracking (does not delete files)."""
        if name in self._instances:
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
            subprocess.run([str(script), mode], check=True)
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
            subprocess.run(cmd, check=True)
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
            # Get backup info
            try:
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                backed_up_count = len([l for l in result.stdout.splitlines() if l.strip()])
                
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
        
        print(colorize("│", Colors.CYAN) + f" Backup Server:  {backup_status:<20} {backup_detail}" + " " * (58 - len(backup_detail) - 35) + colorize("│", Colors.CYAN))
        
        # Instances status
        if instances:
            instance_status = f"{colorize(str(running_count), Colors.GREEN)} running, {colorize(str(stopped_count), Colors.YELLOW) if stopped_count > 0 else str(stopped_count)} stopped"
            print(colorize("│", Colors.CYAN) + f" Instances:      {len(instances)} total • {instance_status}" + " " * (58 - len(f"{len(instances)} total • ") - running_count - stopped_count - 22) + colorize("│", Colors.CYAN))
        else:
            print(colorize("│", Colors.CYAN) + f" Instances:      {colorize('No instances configured', Colors.YELLOW)}" + " " * 22 + colorize("│", Colors.CYAN))
        
        print(colorize("╰──────────────────────────────────────────────────────────╯", Colors.CYAN))
        print()
        
        # Quick instance list
        if instances:
            print(colorize("Active Instances:", Colors.BOLD))
            for instance in instances[:5]:  # Show max 5
                status_icon = colorize("●", Colors.GREEN) if instance.is_running else colorize("○", Colors.YELLOW)
                domain = instance.get_env_value("DOMAIN", "localhost")
                print(f"  {status_icon} {colorize(instance.name, Colors.BOLD):<25} {Colors.CYAN}{domain}{Colors.OFF}")
            
            if len(instances) > 5:
                print(f"  {colorize(f'... and {len(instances) - 5} more', Colors.CYAN)}")
            print()
        
        # Main menu options
        options = [
            ("1", colorize("▸", Colors.GREEN) + " Manage Instances" + (f" ({len(instances)})" if instances else "")),
            ("2", colorize("▸", Colors.BLUE) + " Browse Backups" + (" ✓" if self.rclone_configured else " ⚠")),
            ("3", colorize("▸", Colors.MAGENTA) + " System Backup/Restore"),
            ("4", colorize("▸", Colors.YELLOW) + " Configure Backup Server"),
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
            self.configure_backup_connection()
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
                    print(f"  {idx}) {instance.name} [{status}]")
                    print(f"      Stack: {instance.stack_dir}")
                    print(f"      Data:  {instance.data_root}")
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
                if confirm("Delete ALL instances from tracking?", False):
                    for inst in instances:
                        self.instance_manager.remove_instance(inst.name)
                    ok("All instances removed from tracking")
                    input("\nPress Enter to continue...")
            elif choice.isdigit() and 1 <= int(choice) <= len(instances):
                self.instance_detail_menu(instances[int(choice) - 1])
            else:
                warn("Invalid option")
    
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
            from installer import common, files
            
            # Run the guided setup
            common.pick_and_merge_preset(
                f"https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}"
            )
            common.prompt_core_values()
            common.prompt_backup_plan()
            common.ensure_dir_tree(common.cfg)
            
            files.write_env_file()
            files.write_compose_file()
            files.copy_helper_scripts()
            files.bring_up_stack()
            
            # Run self-test
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from utils.selftest import run_stack_tests
            if run_stack_tests(Path(common.cfg.compose_file), Path(common.cfg.env_file)):
                common.ok("Self-test passed")
            else:
                common.warn("Self-test failed; check container logs")
            
            files.install_cron_backup()
            
            # Register instance
            self.instance_manager.add_instance(
                common.cfg.instance_name,
                Path(common.cfg.stack_dir),
                Path(common.cfg.data_root)
            )
            
            ok(f"Instance '{common.cfg.instance_name}' created successfully!")
            
        except Exception as e:
            error(f"Failed to create instance: {e}")
        
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
            from installer import common
            
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
                if confirm(f"Delete instance '{instance.name}' from tracking?", False):
                    self.instance_manager.remove_instance(instance.name)
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
            
            # Backup instances.json
            system_info = {
                "backup_date": datetime.utcnow().isoformat(),
                "backup_name": backup_name,
                "instance_count": len(instances),
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
                options.append(("0", "Back to main menu"))
                print_menu(options)
                
                choice = get_input("Select instance", "")
                
                if choice == "0":
                    break
                elif choice.isdigit() and 1 <= int(choice) <= len(backup_instances):
                    self._explore_instance_backups(backup_instances[int(choice) - 1])
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
        
        print(colorize("╭──────────────────────────────────────────────────────────────────────────────╮", Colors.CYAN))
        print(colorize("│", Colors.CYAN) + f" Instance:  {colorize(instance_name, Colors.BOLD)}" + " " * (80 - len("Instance:  ") - len(instance_name)) + colorize("│", Colors.CYAN))
        print(colorize("│", Colors.CYAN) + f" Snapshot:  {name}" + " " * (80 - len("Snapshot:  ") - len(name)) + colorize("│", Colors.CYAN))
        mode_display = colorize(mode.upper(), Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN)
        print(colorize("│", Colors.CYAN) + f" Mode:      {mode_display}" + " " * (80 - len("Mode:      ") - len(mode) - 10) + colorize("│", Colors.CYAN))
        print(colorize("│", Colors.CYAN) + f" Created:   {created}" + " " * (80 - len("Created:   ") - len(created)) + colorize("│", Colors.CYAN))
        if mode == "incr" and parent != "?":
            print(colorize("│", Colors.CYAN) + f" Parent:    {parent}" + " " * (80 - len("Parent:    ") - len(parent)) + colorize("│", Colors.CYAN))
        print(colorize("╰──────────────────────────────────────────────────────────────────────────────╯", Colors.CYAN))
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
                from installer import common
                
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
