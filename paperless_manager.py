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
        self.current_instance: Optional[Instance] = None
        
        # Auto-select single instance if only one exists
        instances = self.instance_manager.list_instances()
        if len(instances) == 1:
            self.current_instance = instances[0]
    
    def run(self) -> None:
        """Run the main menu loop."""
        while True:
            self.show_main_menu()
            choice = get_input("Select option", "").lower()
            
            if choice in ('q', 'quit', 'exit'):
                print("\nGoodbye! 👋\n")
                break
            
            self.handle_main_choice(choice)
    
    def show_main_menu(self) -> None:
        """Display the main menu."""
        print_header("Paperless-NGX Bulletproof Manager")
        
        # Show current instance
        if self.current_instance:
            status = colorize("Running", Colors.GREEN) if self.current_instance.is_running else colorize("Stopped", Colors.YELLOW)
            print(f"Current Instance: {colorize(self.current_instance.name, Colors.CYAN)} [{status}]")
        else:
            print(f"Current Instance: {colorize('None selected', Colors.YELLOW)}")
        print()
        
        # Main menu options
        options = [
            ("1", "Setup new instance"),
            ("2", "Select/switch instance"),
            ("3", "Backup management"),
            ("4", "Restore from backup"),
            ("5", "System health check"),
            ("6", "Instance management"),
            ("7", "Container operations"),
            ("q", "Quit")
        ]
        print_menu(options)
    
    def handle_main_choice(self, choice: str) -> None:
        """Handle main menu selection."""
        if choice == "1":
            self.setup_new_instance()
        elif choice == "2":
            self.select_instance()
        elif choice == "3":
            if self.ensure_instance_selected():
                self.backup_menu()
        elif choice == "4":
            if self.ensure_instance_selected():
                self.restore_menu()
        elif choice == "5":
            if self.ensure_instance_selected():
                self.health_check()
        elif choice == "6":
            self.instance_management()
        elif choice == "7":
            if self.ensure_instance_selected():
                self.container_operations()
        else:
            warn("Invalid option")
    
    def ensure_instance_selected(self) -> bool:
        """Ensure an instance is selected, prompt if not."""
        if self.current_instance:
            return True
        
        warn("No instance selected!")
        instances = self.instance_manager.list_instances()
        if not instances:
            error("No instances configured. Please setup a new instance first.")
            return False
        
        if confirm("Would you like to select an instance now?", True):
            self.select_instance()
            return self.current_instance is not None
        return False
    
    def setup_new_instance(self) -> None:
        """Setup a new Paperless-NGX instance."""
        print_header("Setup New Instance")
        
        say("This will run the installer to create a new instance.")
        if not confirm("Continue?", True):
            return
        
        try:
            # Run the installer
            subprocess.run([sys.executable, str(Path(__file__).parent / "install.py")], check=True)
            
            # Reload instances to pick up the new one
            self.instance_manager.load_instances()
            ok("Instance setup complete!")
        except subprocess.CalledProcessError:
            error("Installation failed")
        except KeyboardInterrupt:
            warn("Installation cancelled")
    
    def select_instance(self) -> None:
        """Select an instance to work with."""
        instances = self.instance_manager.list_instances()
        if not instances:
            warn("No instances configured")
            return
        
        print_header("Select Instance")
        for idx, instance in enumerate(instances, 1):
            status = colorize("●", Colors.GREEN) if instance.is_running else colorize("○", Colors.YELLOW)
            print(f"  {idx}) {status} {instance.name} ({instance.stack_dir})")
        print()
        
        choice = get_input(f"Select instance [1-{len(instances)}]", "")
        if choice.isdigit() and 1 <= int(choice) <= len(instances):
            self.current_instance = instances[int(choice) - 1]
            ok(f"Selected instance: {self.current_instance.name}")
        else:
            warn("Invalid selection")
    
    def backup_menu(self) -> None:
        """Backup management menu."""
        assert self.current_instance is not None  # Type checker hint
        backup_mgr = BackupManager(self.current_instance)
        
        while True:
            print_header(f"Backup Management: {self.current_instance.name}")
            
            # Show snapshot count
            snapshots = backup_mgr.fetch_snapshots()
            latest = snapshots[-1][0] if snapshots else "none"
            print(f"Remote: {colorize(backup_mgr.remote, Colors.CYAN)}")
            print(f"Snapshots: {colorize(str(len(snapshots)), Colors.YELLOW)} (latest: {latest})")
            print()
            
            options = [
                ("1", "Run backup (incremental)"),
                ("2", "Run backup (full)"),
                ("3", "Run backup (archive)"),
                ("4", "View snapshots"),
                ("5", "Configure backup schedule"),
                ("b", "Back to main menu")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "").lower()
            
            if choice == "b":
                break
            elif choice == "1":
                say("Starting incremental backup...")
                if backup_mgr.run_backup("incr"):
                    ok("Backup completed successfully!")
                else:
                    error("Backup failed!")
            elif choice == "2":
                say("Starting full backup...")
                if backup_mgr.run_backup("full"):
                    ok("Backup completed successfully!")
                else:
                    error("Backup failed!")
            elif choice == "3":
                say("Starting archive backup...")
                if backup_mgr.run_backup("archive"):
                    ok("Backup completed successfully!")
                else:
                    error("Backup failed!")
            elif choice == "4":
                self.view_snapshots(backup_mgr)
            elif choice == "5":
                self.configure_backup_schedule()
            else:
                warn("Invalid option")
            
            if choice != "b":
                input("\nPress Enter to continue...")
    
    def view_snapshots(self, backup_mgr: BackupManager) -> None:
        """View available snapshots."""
        snapshots = backup_mgr.fetch_snapshots()
        if not snapshots:
            warn("No snapshots found")
            return
        
        print_header("Available Snapshots")
        print(f"{'#':<5} {'Name':<35} {'Mode':<10} {'Parent'}")
        print("─" * 80)
        
        for idx, (name, mode, parent) in enumerate(snapshots, 1):
            parent_display = parent if mode == "incr" else "-"
            mode_color = Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN
            print(f"{idx:<5} {name:<35} {colorize(mode, mode_color):<20} {parent_display}")
        print()
        
        # Option to view manifest
        if confirm("View manifest for a snapshot?", False):
            choice = get_input(f"Snapshot number [1-{len(snapshots)}]", "")
            if choice.isdigit() and 1 <= int(choice) <= len(snapshots):
                snapshot = snapshots[int(choice) - 1][0]
                try:
                    result = subprocess.run(
                        ["rclone", "cat", f"{backup_mgr.remote}/{snapshot}/manifest.yaml"],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    print("\n" + colorize("Manifest:", Colors.BOLD))
                    print(result.stdout)
                except subprocess.CalledProcessError:
                    error("Failed to fetch manifest")
    
    def configure_backup_schedule(self) -> None:
        """Configure automated backup schedule."""
        print_header("Configure Backup Schedule")
        warn("This feature is under development")
        # TODO: Implement cron configuration UI
    
    def restore_menu(self) -> None:
        """Restore menu."""
        assert self.current_instance is not None  # Type checker hint
        backup_mgr = BackupManager(self.current_instance)
        
        print_header(f"Restore: {self.current_instance.name}")
        
        snapshots = backup_mgr.fetch_snapshots()
        if not snapshots:
            warn("No snapshots available to restore")
            return
        
        print(f"{'#':<5} {'Name':<35} {'Mode':<10} {'Parent'}")
        print("─" * 80)
        
        for idx, (name, mode, parent) in enumerate(snapshots, 1):
            parent_display = parent if mode == "incr" else "-"
            mode_color = Colors.GREEN if mode == "full" else Colors.YELLOW
            print(f"{idx:<5} {name:<35} {colorize(mode, mode_color):<20} {parent_display}")
        print()
        
        print(colorize("⚠️  WARNING:", Colors.RED) + " Restore will stop the instance and replace current data!")
        print()
        
        choice = get_input(f"Snapshot to restore [1-{len(snapshots)}] or 'latest'", "latest")
        
        if choice == "latest":
            snapshot = None  # restore script will use latest
        elif choice.isdigit() and 1 <= int(choice) <= len(snapshots):
            snapshot = snapshots[int(choice) - 1][0]
        else:
            warn("Invalid selection")
            return
        
        if not confirm("Are you SURE you want to proceed with restore?", False):
            say("Restore cancelled")
            return
        
        say("Starting restore operation...")
        if backup_mgr.run_restore(snapshot):
            ok("Restore completed successfully!")
        else:
            error("Restore failed!")
        
        input("\nPress Enter to continue...")
    
    def health_check(self) -> None:
        """Run health check on current instance."""
        assert self.current_instance is not None  # Type checker hint
        checker = HealthChecker(self.current_instance)
        checker.print_report()
        input("\nPress Enter to continue...")
    
    def instance_management(self) -> None:
        """Instance management menu."""
        while True:
            print_header("Instance Management")
            
            options = [
                ("1", "List all instances"),
                ("2", "Add existing instance"),
                ("3", "Remove instance from tracking"),
                ("4", "Clone instance"),
                ("b", "Back to main menu")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "").lower()
            
            if choice == "b":
                break
            elif choice == "1":
                self.list_instances()
            elif choice == "2":
                self.add_existing_instance()
            elif choice == "3":
                self.remove_instance()
            elif choice == "4":
                self.clone_instance()
            else:
                warn("Invalid option")
            
            if choice != "b":
                input("\nPress Enter to continue...")
    
    def list_instances(self) -> None:
        """List all configured instances."""
        instances = self.instance_manager.list_instances()
        if not instances:
            warn("No instances configured")
            return
        
        print()
        for instance in instances:
            status = colorize("Running", Colors.GREEN) if instance.is_running else colorize("Stopped", Colors.YELLOW)
            print(f"  • {colorize(instance.name, Colors.BOLD)} [{status}]")
            print(f"    Stack: {instance.stack_dir}")
            print(f"    Data:  {instance.data_root}")
            print()
    
    def add_existing_instance(self) -> None:
        """Add an existing instance to tracking."""
        print_header("Add Existing Instance")
        
        name = get_input("Instance name", "")
        if not name:
            warn("Name required")
            return
        
        stack_dir = Path(get_input("Stack directory", "/home/docker/paperless-setup"))
        data_root = Path(get_input("Data root directory", "/home/docker/paperless"))
        
        if not stack_dir.exists():
            error(f"Stack directory does not exist: {stack_dir}")
            return
        
        if not data_root.exists():
            error(f"Data directory does not exist: {data_root}")
            return
        
        self.instance_manager.add_instance(name, stack_dir, data_root)
        ok(f"Instance '{name}' added successfully!")
    
    def remove_instance(self) -> None:
        """Remove an instance from tracking."""
        instances = self.instance_manager.list_instances()
        if not instances:
            warn("No instances to remove")
            return
        
        print_header("Remove Instance")
        for idx, instance in enumerate(instances, 1):
            print(f"  {idx}) {instance.name}")
        print()
        
        choice = get_input(f"Select instance to remove [1-{len(instances)}]", "")
        if not choice.isdigit() or not (1 <= int(choice) <= len(instances)):
            warn("Invalid selection")
            return
        
        instance = instances[int(choice) - 1]
        
        print()
        warn("This will only remove the instance from tracking.")
        warn("Docker containers and data files will NOT be deleted.")
        print()
        
        if not confirm(f"Remove '{instance.name}' from tracking?", False):
            say("Cancelled")
            return
        
        self.instance_manager.remove_instance(instance.name)
        if self.current_instance == instance:
            self.current_instance = None
        ok(f"Instance '{instance.name}' removed from tracking")
    
    def clone_instance(self) -> None:
        """Clone an instance by restoring from its backup."""
        print_header("Clone Instance")
        warn("This feature allows you to create a new instance from a backup")
        warn("This is under development")
        # TODO: Implement instance cloning
    
    def container_operations(self) -> None:
        """Container operations menu."""
        assert self.current_instance is not None  # Type checker hint
        
        while True:
            print_header(f"Container Operations: {self.current_instance.name}")
            
            options = [
                ("1", "Start containers"),
                ("2", "Stop containers"),
                ("3", "Restart containers"),
                ("4", "View status"),
                ("5", "View logs"),
                ("6", "Upgrade containers"),
                ("7", "Pull latest images"),
                ("b", "Back to main menu")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "").lower()
            
            if choice == "b":
                break
            elif choice == "1":
                self.docker_command("up", "-d")
            elif choice == "2":
                self.docker_command("down")
            elif choice == "3":
                self.docker_command("restart")
            elif choice == "4":
                self.docker_command("ps")
            elif choice == "5":
                self.view_logs()
            elif choice == "6":
                self.upgrade_containers()
            elif choice == "7":
                self.docker_command("pull")
            else:
                warn("Invalid option")
            
            if choice != "b" and choice in ["1", "2", "3", "6", "7"]:
                input("\nPress Enter to continue...")
    
    def docker_command(self, *args: str) -> None:
        """Run a docker compose command."""
        assert self.current_instance is not None  # Type checker hint
        cmd = [
            "docker", "compose",
            "-f", str(self.current_instance.compose_file),
            *args
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            error(f"Command failed with exit code {e.returncode}")
    
    def view_logs(self) -> None:
        """View container logs."""
        assert self.current_instance is not None  # Type checker hint
        service = get_input("Service name (blank for all)", "")
        cmd = [
            "docker", "compose",
            "-f", str(self.current_instance.compose_file),
            "logs", "--tail", "100", "--timestamps"
        ]
        if service:
            cmd.append(service)
        
        try:
            subprocess.run(cmd)
        except subprocess.CalledProcessError:
            error("Failed to view logs")
        
        input("\nPress Enter to continue...")
    
    def upgrade_containers(self) -> None:
        """Upgrade containers with automatic backup."""
        assert self.current_instance is not None  # Type checker hint
        
        if not confirm("Run backup before upgrade?", True):
            say("Skipping backup...")
        else:
            backup_mgr = BackupManager(self.current_instance)
            say("Running full backup before upgrade...")
            if not backup_mgr.run_backup("full"):
                error("Backup failed! Upgrade aborted.")
                return
            ok("Backup completed")
        
        say("Pulling latest images...")
        self.docker_command("pull")
        
        say("Recreating containers...")
        self.docker_command("up", "-d")
        
        ok("Upgrade complete!")


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
