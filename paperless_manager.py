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
            choice = get_input("Select option", "").lower()
            
            if choice in ('q', 'quit', 'exit'):
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
                        latest_backup = snaps[-1][0]  # Most recent snapshot name
                        break
                
                backup_info = f"{colorize('pCloud Connected', Colors.GREEN)} • {backed_up_count} backed up instances"
                if latest_backup != "none":
                    backup_info += f" • Last: {latest_backup}"
            except:
                backup_info = colorize("pCloud Connected", Colors.GREEN)
        else:
            backup_info = colorize("Not connected", Colors.YELLOW) + " (configure to enable backups)"
        
        print(f"Backup: {backup_info}")
        print()
        
        # Show instances overview
        if instances:
            print(f"Instances ({len(instances)}):")
            for instance in instances[:5]:  # Show max 5
                status_icon = colorize("●", Colors.GREEN) if instance.is_running else colorize("○", Colors.YELLOW)
                status_text = "Running" if instance.is_running else "Stopped"
                print(f"  {status_icon} {colorize(instance.name, Colors.BOLD)} - {status_text}")
            
            if len(instances) > 5:
                print(f"  ... and {len(instances) - 5} more")
        else:
            print(colorize("No instances configured", Colors.YELLOW))
        
        print()
        
        # Main menu options
        options = [
            ("1", "Instances" + (f" ({len(instances)})" if instances else "")),
            ("2", "Backups" + (" ✓" if self.rclone_configured else " ⚠")),
            ("3", "Backup server connection"),
            ("q", "Quit")
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
            
            options = [
                ("a", "Add new instance"),
                ("d", "Delete all instances"),
            ]
            
            # Add instance selection options
            for idx in range(1, len(instances) + 1):
                options.insert(idx, (str(idx), f"Manage '{instances[idx-1].name}'"))
            
            options.append(("b", "Back to main menu"))
            
            print_menu(options)
            
            choice = get_input("Select option", "").lower()
            
            if choice == "b":
                break
            elif choice == "a":
                self.add_instance_menu()
            elif choice == "d":
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
            ("b", "Back")
        ]
        print_menu(options)
        
        choice = get_input("Select option", "").lower()
        
        if choice == "1":
            self.create_fresh_instance()
        elif choice == "2":
            self.restore_instance_from_backup()
        # else back
    
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
            restore_script.write_text((Path("/usr/local/lib/paperless-bulletproof") / "modules" / "restore.py").read_text())
            
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
            
            status = colorize("Running", Colors.GREEN) if instance.is_running else colorize("Stopped", Colors.YELLOW)
            print(f"Status: {status}")
            print(f"Stack: {instance.stack_dir}")
            print(f"Data:  {instance.data_root}")
            print()
            
            options = [
                ("1", "View details"),
                ("2", "Health check"),
                ("3", "Backup now"),
                ("4", "Restore/revert from backup"),
                ("5", "Container operations"),
                ("6", "Edit settings"),
                ("7", "Delete instance"),
                ("b", "Back")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "").lower()
            
            if choice == "b":
                break
            elif choice == "1":
                self.view_instance_details(instance)
            elif choice == "2":
                self.health_check(instance)
            elif choice == "3":
                self.backup_instance(instance)
            elif choice == "4":
                self.revert_instance(instance)
            elif choice == "5":
                self.container_operations(instance)
            elif choice == "6":
                self.edit_instance(instance)
            elif choice == "7":
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
            ("b", "Cancel")
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
                ("b", "Back")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "").lower()
            
            if choice == "b":
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
    
    def backups_menu(self) -> None:
        """Backups explorer and management."""
        print_header("Backups")
        warn("Backup explorer is under development")
        warn("For now, use the instance-specific restore menu")
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
