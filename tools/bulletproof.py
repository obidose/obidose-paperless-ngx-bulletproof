#!/usr/bin/env python3
"""
Main CLI entry point for Paperless-ngx Bulletproof.

This is the main command-line interface that orchestrates all functionality
by importing from specialized modules for UI, cloud storage, instances, and backup operations.
"""

import argparse
import os
import secrets
import string
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Import from our modules
from ui import (
    print_header, say, ok, warn, error, _read, print_instances_table, print_menu_options,
    COLOR_RED, COLOR_GREEN, COLOR_BLUE, COLOR_YELLOW, COLOR_DIM, COLOR_BOLD, COLOR_OFF,
    ICON_SUCCESS, ICON_ERROR, ICON_WARNING, ICON_INFO, ICON_BULLET
)
from cloud_storage import setup_pcloud_remote, _pcloud_remote_ok
from instance import (
    find_instances, Instance, load_env, cleanup_orphans,
    down_instance, up_instance, start_all, stop_all, delete_all, _create_instance_structure
)
from backup_restore import (
    list_remote_instances, fetch_snapshots_for, explore_backups, 
    run_stack_tests, cmd_backup, cmd_snapshots, cmd_restore
)


# Global variables for current instance context
STACK_DIR: Optional[Path] = None
DATA_ROOT: Optional[Path] = None
ENV_FILE: Optional[Path] = None
COMPOSE_FILE: Optional[Path] = None
INSTANCE_NAME: str = ""
RCLONE_REMOTE_NAME: str = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
RCLONE_REMOTE_PATH: str = ""
REMOTE: str = ""


def die(msg: str) -> None:
    """Print error and exit."""
    print(f"{ICON_ERROR} {COLOR_RED}{msg}{COLOR_OFF}")
    raise SystemExit(1)


def init_from_env() -> None:
    """Initialize global variables from environment."""
    global STACK_DIR, DATA_ROOT, ENV_FILE, COMPOSE_FILE
    global INSTANCE_NAME, RCLONE_REMOTE_NAME, RCLONE_REMOTE_PATH, REMOTE
    
    INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "")
    RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    RCLONE_REMOTE_PATH = os.environ.get("RCLONE_REMOTE_PATH", "")
    REMOTE = os.environ.get("REMOTE", "")
    
    stack_dir_str = os.environ.get("STACK_DIR")
    data_root_str = os.environ.get("DATA_ROOT")
    
    if stack_dir_str:
        STACK_DIR = Path(stack_dir_str)
        ENV_FILE = STACK_DIR / ".env"
        COMPOSE_FILE = STACK_DIR / "docker-compose.yml"
    
    if data_root_str:
        DATA_ROOT = Path(data_root_str)


def _get_instance_name() -> Optional[str]:
    """Get and validate instance name from user."""
    while True:
        name = _read("Instance name: ").strip()
        if not name:
            warn("Instance name cannot be empty.")
            continue
        
        # Check if instance already exists
        existing_instances = find_instances()
        if any(inst.name == name for inst in existing_instances):
            warn(f"Instance '{name}' already exists.")
            continue
        
        return name


def _handle_restore_from_backup(name: str) -> bool:
    """Handle restoration from existing backup. Returns True if restore was attempted."""
    remote_instances = list_remote_instances()
    if name not in remote_instances:
        return False
    
    restore_choice = _read(f"Found backup for '{name}'. Restore from backup? [y/N]: ").strip().lower()
    if not restore_choice.startswith('y'):
        return False
    
    # Restore from backup
    say(f"Restoring instance '{name}' from backup...")
    
    # Get available snapshots
    snapshots = fetch_snapshots_for(name)
    if not snapshots:
        warn(f"No snapshots found for instance '{name}'")
        return True
    
    # Show available snapshots and let user choose
    say("Available snapshots:")
    for i, (snap_name, mode, parent) in enumerate(snapshots, 1):
        print(f"  {i}) {snap_name} ({mode})")
    
    latest_snap = snapshots[-1][0]  # Get latest snapshot name
    snap_choice = _read(f"Choose snapshot [1-{len(snapshots)}] or press Enter for latest ({latest_snap}): ").strip()
    
    selected_snap = latest_snap
    if snap_choice:
        try:
            snap_index = int(snap_choice) - 1
            if 0 <= snap_index < len(snapshots):
                selected_snap = snapshots[snap_index][0]
            else:
                warn("Invalid selection, using latest snapshot")
        except ValueError:
            warn("Invalid selection, using latest snapshot")
    
    say(f"Restoring from snapshot: {selected_snap}")
    
    # Get paths for restore
    data_dir = Path(f"/home/docker/{name}")
    stack_dir = Path(f"/home/docker/{name}-setup")
    
    # Create directory structure for restore (restore_mode=True)
    result = _create_instance_structure(name, str(data_dir), str(stack_dir), restore_mode=True)
    if not result:
        return True
    
    # Set up global environment variables for this restore operation
    global STACK_DIR, DATA_ROOT, ENV_FILE, COMPOSE_FILE, REMOTE
    
    STACK_DIR = stack_dir
    DATA_ROOT = data_dir
    ENV_FILE = stack_dir / ".env"
    COMPOSE_FILE = stack_dir / "docker-compose.yml"
    
    # Update environment variables
    os.environ.update({
        "INSTANCE_NAME": name,
        "STACK_DIR": str(stack_dir),
        "DATA_ROOT": str(data_dir),
        "RCLONE_REMOTE_NAME": RCLONE_REMOTE_NAME,
        "RCLONE_REMOTE_PATH": f"backups/paperless/{name}",
        "REMOTE": f"{RCLONE_REMOTE_NAME}:backups/paperless/{name}"
    })
    
    # Create argparse namespace for cmd_restore
    restore_args = argparse.Namespace()
    restore_args.snapshot = selected_snap
    
    # Call restore function
    cmd_restore(restore_args)
    return True


def _get_instance_config() -> Optional[Dict[str, Any]]:
    """Get configuration for new instance from user."""
    # Basic configuration prompts
    timezone = _read("Timezone [UTC]: ").strip() or "UTC"
    
    # Admin credentials
    admin_user = _read("Admin username [admin]: ").strip() or "admin"
    admin_password = _read("Admin password: ").strip()
    if not admin_password:
        warn("Admin password cannot be empty.")
        return None
    
    # Database password
    db_password = _read("Database password [auto-generated]: ").strip()
    if not db_password:
        db_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        say(f"Generated database password: {db_password}")
    
    # HTTPS/Traefik configuration
    use_https = _read("Enable HTTPS with Traefik? [y/N]: ").strip().lower().startswith('y')
    domain = ""
    email = ""
    
    if use_https:
        domain = _read("Domain name: ").strip()
        if not domain:
            warn("Domain name required for HTTPS.")
            return None
        email = _read("Email for Let's Encrypt: ").strip()
        if not email:
            warn("Email required for Let's Encrypt.")
            return None
    
    return {
        'timezone': timezone,
        'admin_user': admin_user,
        'admin_password': admin_password,
        'db_password': db_password,
        'use_https': use_https,
        'domain': domain,
        'email': email
    }


def cmd_create_instance(args: argparse.Namespace) -> None:
    """Command to create a new Paperless-ngx instance."""
    print_header("Create New Instance")
    
    # Ensure cloud storage is set up first
    if not _pcloud_remote_ok():
        say("Cloud storage remote not configured. Setting up now...")
        if not setup_pcloud_remote():
            warn("Cloud storage setup failed. Instance creation requires cloud storage for backups.")
            return
    
    # Get instance name
    name = _get_instance_name()
    if not name:
        return
    
    # Check for existing backups and handle restore
    if _handle_restore_from_backup(name):
        return
    
    # Create new instance from scratch
    say("Creating new instance from scratch...")
    say("This will guide you through creating a new Paperless-ngx instance.")
    
    # Get configuration
    config = _get_instance_config()
    if not config:
        return
    
    # Path configuration
    data_root = _read(f"Data directory [/home/docker/{name}]: ").strip() or f"/home/docker/{name}"
    stack_dir = _read(f"Stack directory [/home/docker/{name}-setup]: ").strip() or f"/home/docker/{name}-setup"
    
    # Create instance structure using unified function
    if not _create_instance_structure(name, data_root, stack_dir, restore_mode=False, config=config):
        warn("Failed to create instance structure")
        return
    
    ok(f"Instance '{name}' created successfully!")
    say("Next steps:")
    say(f"  1. cd {stack_dir}")
    say("  2. Review the configuration in .env")
    say("  3. Start with: docker compose up -d")
    say("  4. Or use 'bulletproof' to manage this instance")


def cmd_upgrade(_: argparse.Namespace) -> None:
    """Command to upgrade (backup then pull and restart)."""
    try:
        cmd_backup(argparse.Namespace(mode="full"))
        say("Pulling latest images and restarting...")
        subprocess.run(["docker", "compose", "pull"], check=False)
        subprocess.run(["docker", "compose", "up", "-d"], check=False)
        ok("Upgrade completed!")
    except Exception as e:
        error(f"Upgrade failed: {e}")


def cmd_status(_: argparse.Namespace) -> None:
    """Command to show docker status."""
    try:
        say("Container Status:")
        subprocess.run(["docker", "compose", "ps"], check=False)
    except Exception as e:
        error(f"Failed to get status: {e}")


def cmd_logs(args: argparse.Namespace) -> None:
    """Command to show logs."""
    try:
        service = getattr(args, 'service', None)
        cmd = ["docker", "compose", "logs", "-f"]
        if service:
            cmd.append(service)
        subprocess.run(cmd, check=False)
    except Exception as e:
        error(f"Failed to show logs: {e}")


def cmd_doctor(_: argparse.Namespace) -> None:
    """Command to run basic checks."""
    print_header("System Diagnostics")
    
    try:
        say("Environment:")
        print(f"- INSTANCE_NAME: {INSTANCE_NAME}")
        print(f"- STACK_DIR: {STACK_DIR}")
        print(f"- DATA_ROOT: {DATA_ROOT}")
        
        if COMPOSE_FILE:
            compose_status = "[ok]" if COMPOSE_FILE.exists() else "[missing]"
            print(f"- COMPOSE_FILE: {COMPOSE_FILE} {compose_status}")
        
        if ENV_FILE:
            env_status = "[ok]" if ENV_FILE.exists() else "[missing]"
            print(f"- ENV_FILE: {ENV_FILE} {env_status}")
        
        print()
        if run_stack_tests():
            ok("All system checks passed!")
        else:
            error("Some system checks failed!")
    except Exception as e:
        error(f"Diagnostics failed: {e}")


def cmd_schedule(args: argparse.Namespace) -> None:
    """Command to configure backup schedule."""
    print_header("Backup Schedule Configuration")
    warn("Schedule configuration not yet implemented")


def cmd_setup_pcloud(args: argparse.Namespace) -> None:
    """Command handler for cloud storage setup."""
    try:
        if setup_pcloud_remote():
            ok("Cloud storage setup completed!")
        else:
            error("Cloud storage setup failed!")
    except Exception as e:
        error(f"Cloud storage setup error: {e}")


def install_cron(full: str, incr: str, archive: str) -> None:
    """Install cron jobs for automated backups."""
    warn("Cron installation not yet implemented")


def _handle_no_instances() -> bool:
    """Handle the case when no instances are found. Returns True to continue main loop."""
    say("No instances found. Let's create your first instance!")
    
    # Check cloud storage first
    if not _pcloud_remote_ok():
        print_menu_options([
            ("s", "Set up cloud storage (required)"),
            ("q", "Quit")
        ], "Setup Required")
        
        choice = _read("Choice: ").strip().lower()
        if choice == "s":
            if setup_pcloud_remote():
                ok("Cloud storage setup completed!")
                return True
            else:
                error("Cloud storage setup failed!")
                return True
        elif choice == "q":
            return False
        else:
            warn("Invalid choice")
            return True
    else:
        print_menu_options([
            ("c", "Create new instance"),
            ("s", "Configure cloud storage"),
            ("e", "Explore backups"),
            ("q", "Quit")
        ], "No Instances Found")
        
        choice = _read("Choice: ").strip().lower()
        if choice == "c":
            cmd_create_instance(argparse.Namespace())
        elif choice == "s":
            setup_pcloud_remote()
        elif choice == "e":
            explore_backups()
        elif choice == "q":
            return False
        else:
            warn("Invalid choice")
        
        return True


def _handle_multi_instance_menu(insts) -> bool:
    """Handle the multi-instance menu. Returns True to continue main loop."""
    # Show instances table
    print_instances_table(insts)
    
    # Multi-instance menu
    print_menu_options([
        ("c", "Create new instance"),
        ("s", "Start all instances"),
        ("d", "Stop all instances"),
        ("r", "Delete all instances"),
        ("e", "Explore backups"),
        ("o", "Configure cloud storage"),
        ("q", "Quit")
    ], "Multi-Instance Actions")
    
    choice = _read("Choice: ").strip().lower()
    
    if choice == "c":
        cmd_create_instance(argparse.Namespace())
    elif choice == "s":
        start_all(insts)
        ok("All instances started")
    elif choice == "d":
        stop_all(insts)
        ok("All instances stopped")
    elif choice == "r":
        delete_all(insts)
    elif choice == "e":
        explore_backups()
    elif choice == "o":
        setup_pcloud_remote()
    elif choice == "q":
        return False
    else:
        warn("Invalid choice")
    
    return True


def multi_main() -> None:
    """Main function for multi-instance management."""
    print_header("Paperless-ngx Bulletproof", "Multi-Instance Management Dashboard")
    
    while True:
        insts = find_instances()
        cleanup_orphans()
        
        if not insts:
            if not _handle_no_instances():
                break
        else:
            if not _handle_multi_instance_menu(insts):
                break


def _get_current_instance_info() -> Optional[Instance]:
    """Get current instance information."""
    if not INSTANCE_NAME:
        return None
    
    insts = find_instances()
    return next((inst for inst in insts if inst.name == INSTANCE_NAME), None)


def _display_instance_info(current: Instance) -> None:
    """Display current instance information."""
    status = current.status()
    status_color = COLOR_GREEN if status == "running" else COLOR_RED
    print(f"{COLOR_BOLD}{ICON_INFO} Instance Information{COLOR_OFF}")
    print(f"  Name: {COLOR_BOLD}{current.name}{COLOR_OFF}")
    print(f"  Status: {status_color}{status.title()}{COLOR_OFF}")
    print(f"  Data: {current.data_dir}")
    print(f"  Stack: {current.stack_dir}")
    print()


def _handle_menu_choice(choice: str, current: Optional[Instance]) -> bool:
    """Handle menu choice. Returns False to exit menu."""
    if choice == "1" and current:
        up_instance(current)
    elif choice == "2" and current:
        down_instance(current)
    elif choice == "3":
        cmd_logs(argparse.Namespace(service=None))
    elif choice == "4":
        cmd_backup(argparse.Namespace(mode="incr"))
    elif choice == "5":
        cmd_backup(argparse.Namespace(mode="full"))
    elif choice == "6":
        cmd_backup(argparse.Namespace(mode="archive"))
    elif choice == "7":
        cmd_snapshots(argparse.Namespace(snapshot=None))
    elif choice == "8":
        cmd_restore(argparse.Namespace(snapshot=None))
    elif choice == "9":
        cmd_upgrade(argparse.Namespace())
    elif choice == "10":
        cmd_doctor(argparse.Namespace())
    elif choice == "11":
        cmd_schedule(argparse.Namespace(full=None, incr=None, archive=None))
    elif choice == "12":
        return False
    else:
        warn("Invalid choice")
    
    return True


def menu() -> None:
    """Single instance management menu."""
    if not INSTANCE_NAME:
        warn("No instance context available")
        return
    
    print_header(f"Instance: {INSTANCE_NAME}")
    
    while True:
        # Get current instance information
        current = _get_current_instance_info()
        
        if current:
            _display_instance_info(current)
        
        # Menu options
        print_menu_options([
            ("1", "Start instance"),
            ("2", "Stop instance"),
            ("3", "View logs"),
            ("4", "Backup (incremental)"),
            ("5", "Backup (full)"),
            ("6", "Backup (archive)"),
            ("7", "List snapshots"),
            ("8", "Restore from snapshot"),
            ("9", "Upgrade instance"),
            ("10", "System diagnostics"),
            ("11", "Configure schedule"),
            ("12", "Quit")
        ], "Instance Management")
        
        choice = _read("Choice: ").strip()
        
        if not _handle_menu_choice(choice, current):
            break


# Set up argument parser
parser = argparse.ArgumentParser(
    description="Paperless-ngx bulletproof helper - Multi-instance management with cloud backups"
)
parser.add_argument("--instance", help="instance name to operate on")
sub = parser.add_subparsers(dest="command")

# Backup command
p = sub.add_parser("backup", help="run backup script")
p.add_argument("mode", nargs="?", choices=["full", "incr", "archive"], 
               help="backup mode: full|incr|archive")
p.set_defaults(func=cmd_backup)

# Snapshots command
p = sub.add_parser("snapshots", help="list snapshots and optionally show a manifest")
p.add_argument("snapshot", nargs="?", help="snapshot name or number to show manifest")
p.set_defaults(func=cmd_snapshots)

# Restore command
p = sub.add_parser("restore", help="restore snapshot")
p.add_argument("snapshot", nargs="?", help="snapshot name or number to restore")
p.set_defaults(func=cmd_restore)

# Upgrade command
p = sub.add_parser("upgrade", help="backup then pull images and up -d")
p.set_defaults(func=cmd_upgrade)

# Status command
p = sub.add_parser("status", help="docker status")
p.set_defaults(func=cmd_status)

# Logs command
p = sub.add_parser("logs", help="show logs")
p.add_argument("service", nargs="?", help="specific service to show logs for")
p.set_defaults(func=cmd_logs)

# Doctor command
p = sub.add_parser("doctor", help="basic checks")
p.set_defaults(func=cmd_doctor)

# Schedule command
p = sub.add_parser("schedule", help="configure backup schedule")
p.add_argument("--full", help="time for daily full backup (HH:MM or cron)")
p.add_argument("--incr", help="incremental frequency (hours or cron)")
p.add_argument("--archive", help="cron for monthly archive or blank to disable")
p.set_defaults(func=cmd_schedule)

# Cloud storage setup command
p = sub.add_parser("setup-pcloud", help="set up cloud storage remote for backups")
p.set_defaults(func=cmd_setup_pcloud)

# Create instance command
p = sub.add_parser("create", help="create a new Paperless-ngx instance")
p.set_defaults(func=cmd_create_instance)


if __name__ == "__main__":
    args = parser.parse_args()
    
    if STACK_DIR is None and not args.instance:
        multi_main()
    else:
        if args.instance and STACK_DIR is None:
            insts = find_instances()
            inst = next((i for i in insts if i.name == args.instance), None)
            if not inst:
                die(f"Instance '{args.instance}' not found")
            
            # Assert for type checker - die() never returns, so inst is guaranteed to be not None
            assert inst is not None
            os.environ.update(inst.env_for_subprocess())
            STACK_DIR = inst.stack_dir
            load_env(inst.env_file)
            init_from_env()
        
        if not hasattr(args, "func"):
            if sys.stdin.isatty():
                menu()
            else:
                parser.print_help()
        else:
            args.func(args)