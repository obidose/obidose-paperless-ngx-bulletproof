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
        ("m", "Manage instances"),
        ("c", "Create new instance"),
        ("s", "Start all instances"),
        ("d", "Stop all instances"),
        ("r", "Delete all instances"),
        ("e", "Explore backups"),
        ("o", "Configure cloud storage"),
        ("q", "Quit")
    ], "Multi-Instance Actions")
    
    choice = _read("Choice: ").strip().lower()
    
    # Handle empty input (Ctrl+C or EOF) as quit
    if not choice:
        return False
    
    # Handle menu actions
    if choice == "m":
        _handle_instance_selection_menu(insts)
    elif choice == "c":
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


def _handle_instance_selection_menu(insts) -> None:
    """Handle instance selection submenu."""
    while True:
        print()
        say("Select an instance to manage:")
        print()
        
        # Show numbered list of instances
        for i, inst in enumerate(insts):
            status = inst.status()
            status_icon = "●" if status == "Running" else "○"
            print(f"  {i + 1} │ {status_icon} {inst.name} ({status})")
        
        print()
        print_menu_options([
            ("b", "Back to main menu")
        ], "Instance Selection")
        
        choice = _read("Select instance (number) or action: ").strip()
        
        # Handle empty input as back
        if not choice:
            break
            
        if choice.lower() == "b":
            break
            
        # Handle instance selection
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(insts):
                _handle_single_instance_menu(insts[idx])
            else:
                warn("Invalid instance number")
        else:
            warn("Invalid choice")


def _handle_single_instance_menu(instance: Instance) -> None:
    """Handle single instance management menu."""
    while True:
        print()
        say(f"Managing instance: {instance.name}")
        status = instance.status()
        print(f"Status: {status}")
        print()
        
        # Build menu options based on current state
        options = []
        
        if status == "Running":
            options.append(("p", "Stop instance"))
        else:
            options.append(("s", "Start instance"))
            
        options.extend([
            ("b", "Create backup now"),
            ("r", "Restore from backup"),
            ("h", "Change backup schedule"),
            ("n", "Rename instance"),
            ("d", "Delete instance"),
            ("v", "View logs"),
            ("x", "Back to instance list")
        ])
        
        print_menu_options(options, f"Instance Management - {instance.name}")
        
        choice = _read("Choice: ").strip().lower()
        
        # Handle empty input as back
        if not choice:
            break
            
        if choice == "s" and status != "Running":
            _safely_start_instance(instance)
        elif choice == "p" and status == "Running":
            _safely_stop_instance(instance)
        elif choice == "b":
            _safely_backup_instance(instance)
        elif choice == "r":
            _safely_restore_instance(instance)
        elif choice == "h":
            _change_backup_schedule(instance)
        elif choice == "n":
            _rename_instance(instance)
        elif choice == "d":
            if _safely_delete_instance(instance):
                break  # Instance deleted, return to instance list
        elif choice == "v":
            _view_instance_logs(instance)
        elif choice == "x":
            break
        else:
            warn("Invalid choice")


def multi_main() -> None:
    """Main function for multi-instance management."""
    print_header("Paperless-ngx Bulletproof", "Multi-Instance Management Dashboard")
    
    # Run orphan cleanup only once at startup
    cleanup_orphans()
    
    try:
        while True:
            insts = find_instances()
            
            if not insts:
                if not _handle_no_instances():
                    break
            else:
                if not _handle_multi_instance_menu(insts):
                    break
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)


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


# =============================================================================
# Safe Instance Management Functions
# =============================================================================

def _safely_start_instance(instance: Instance) -> None:
    """Safely start an instance."""
    try:
        say(f"Starting instance: {instance.name}")
        up_instance(instance)
        ok(f"Instance {instance.name} started successfully")
    except Exception as e:
        error(f"Failed to start instance {instance.name}: {e}")


def _safely_stop_instance(instance: Instance) -> None:
    """Safely stop an instance."""
    try:
        say(f"Stopping instance: {instance.name}")
        down_instance(instance)
        ok(f"Instance {instance.name} stopped successfully")
    except Exception as e:
        error(f"Failed to stop instance {instance.name}: {e}")


def _safely_backup_instance(instance: Instance) -> None:
    """Safely backup an instance."""
    was_running = instance.status() == "Running"
    
    try:
        if was_running:
            say(f"Stopping {instance.name} for backup...")
            down_instance(instance)
        
        say(f"Creating backup for {instance.name}...")
        # Use the backup functionality from backup_restore module
        from backup_restore import cmd_backup
        import argparse
        args = argparse.Namespace()
        args.instance = instance.name
        cmd_backup(args)
        ok(f"Backup created for {instance.name}")
        
    except Exception as e:
        error(f"Backup failed for {instance.name}: {e}")
    finally:
        if was_running:
            try:
                say(f"Restarting {instance.name}...")
                up_instance(instance)
                ok(f"Instance {instance.name} restarted")
            except Exception as e:
                error(f"Failed to restart {instance.name}: {e}")


def _safely_restore_instance(instance: Instance) -> None:
    """Safely restore an instance from backup."""
    was_running = instance.status() == "Running"
    
    try:
        # Show available backups using existing functionality
        from backup_restore import fetch_snapshots_for
        snapshots = fetch_snapshots_for(instance.name)
        
        if not snapshots:
            warn(f"No backups found for {instance.name}")
            return
        
        print()
        say("Available backups:")
        for i, (timestamp, size, snap_id) in enumerate(snapshots):
            print(f"  {i + 1}. {timestamp} ({size}) - {snap_id}")
        
        choice = _read("Select backup number (or 'c' to cancel): ").strip()
        
        if choice.lower() == 'c' or not choice:
            return
            
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(snapshots):
                timestamp, size, snap_id = snapshots[idx]
                
                # Confirm destructive action
                if not _read(f"This will replace all data in {instance.name}. Continue? [y/N]: ").strip().lower().startswith('y'):
                    say("Restore cancelled")
                    return
                
                if was_running:
                    say(f"Stopping {instance.name} for restore...")
                    down_instance(instance)
                
                say(f"Restoring {instance.name} from {timestamp}...")
                from backup_restore import cmd_restore
                import argparse
                args = argparse.Namespace()
                args.instance = instance.name
                args.snapshot = snap_id
                cmd_restore(args)
                ok(f"Instance {instance.name} restored from backup")
                
            else:
                warn("Invalid backup number")
        else:
            warn("Invalid choice")
            
    except Exception as e:
        error(f"Restore failed for {instance.name}: {e}")
    finally:
        if was_running:
            try:
                say(f"Restarting {instance.name}...")
                up_instance(instance)
                ok(f"Instance {instance.name} restarted")
            except Exception as e:
                error(f"Failed to restart {instance.name}: {e}")


def _change_backup_schedule(instance: Instance) -> None:
    """Change backup schedule for an instance."""
    say(f"Current backup schedule for {instance.name}:")
    
    # Read current schedule from env file
    env_file = instance.stack_dir / ".env"
    schedule = "Not configured"
    
    if env_file.exists():
        content = env_file.read_text()
        for line in content.split('\n'):
            if line.startswith('BACKUP_SCHEDULE='):
                schedule = line.split('=', 1)[1].strip('"\'')
                break
    
    print(f"  Current: {schedule}")
    print()
    
    print("Schedule options:")
    print("  1. Daily at 2:00 AM")
    print("  2. Weekly on Sunday at 3:00 AM")
    print("  3. Monthly on 1st at 4:00 AM")
    print("  4. Custom cron expression")
    print("  5. Disable backups")
    
    choice = _read("Select option (1-5): ").strip()
    
    schedules = {
        "1": "0 2 * * *",
        "2": "0 3 * * 0", 
        "3": "0 4 1 * *",
        "5": ""
    }
    
    if choice in schedules:
        new_schedule = schedules[choice]
    elif choice == "4":
        new_schedule = _read("Enter cron expression: ").strip()
    else:
        warn("Invalid choice")
        return
    
    try:
        # Update .env file
        if env_file.exists():
            content = env_file.read_text()
            lines = content.split('\n')
            updated = False
            
            for i, line in enumerate(lines):
                if line.startswith('BACKUP_SCHEDULE='):
                    lines[i] = f'BACKUP_SCHEDULE="{new_schedule}"'
                    updated = True
                    break
            
            if not updated:
                lines.append(f'BACKUP_SCHEDULE="{new_schedule}"')
            
            env_file.write_text('\n'.join(lines))
            
            if new_schedule:
                ok(f"Backup schedule updated to: {new_schedule}")
            else:
                ok("Backups disabled")
        else:
            warn("Environment file not found")
            
    except Exception as e:
        error(f"Failed to update schedule: {e}")


def _rename_instance(instance: Instance) -> None:
    """Rename an instance."""
    was_running = instance.status() == "Running"
    current_name = instance.name
    
    new_name = _read(f"Enter new name for '{current_name}': ").strip()
    
    if not new_name:
        return
        
    if new_name == current_name:
        say("Name unchanged")
        return
    
    # Check if new name already exists
    existing_instances = [inst.name for inst in find_instances()]
    if new_name in existing_instances:
        error(f"Instance '{new_name}' already exists")
        return
    
    try:
        if was_running:
            say(f"Stopping {current_name} for rename...")
            down_instance(instance)
        
        say(f"Renaming {current_name} to {new_name}...")
        
        # Rename directories
        docker_dir = Path("/home/docker")
        old_stack_dir = docker_dir / f"{current_name}-setup"
        new_stack_dir = docker_dir / f"{new_name}-setup"
        old_data_dir = docker_dir / current_name
        new_data_dir = docker_dir / new_name
        
        if old_stack_dir.exists():
            old_stack_dir.rename(new_stack_dir)
        if old_data_dir.exists():
            old_data_dir.rename(new_data_dir)
        
        # Update .env file
        env_file = new_stack_dir / ".env"
        if env_file.exists():
            content = env_file.read_text()
            content = content.replace(f'INSTANCE_NAME="{current_name}"', f'INSTANCE_NAME="{new_name}"')
            content = content.replace(f"INSTANCE_NAME={current_name}", f'INSTANCE_NAME="{new_name}"')
            env_file.write_text(content)
        
        ok(f"Instance renamed from {current_name} to {new_name}")
        
        # Update the instance object
        instance.name = new_name
        instance.stack_dir = new_stack_dir
        instance.data_dir = new_data_dir
        
    except Exception as e:
        error(f"Failed to rename instance: {e}")
    finally:
        if was_running:
            try:
                say(f"Starting {new_name}...")
                up_instance(instance)
                ok(f"Instance {new_name} started")
            except Exception as e:
                error(f"Failed to start renamed instance: {e}")


def _safely_delete_instance(instance: Instance) -> bool:
    """Safely delete an instance. Returns True if deleted."""
    say(f"WARNING: This will permanently delete instance '{instance.name}' and ALL its data!")
    
    if not _read("Type 'DELETE' to confirm: ").strip() == "DELETE":
        say("Deletion cancelled")
        return False
    
    try:
        # Stop instance first
        if instance.status() == "Running":
            say(f"Stopping {instance.name}...")
            down_instance(instance)
        
        say(f"Deleting {instance.name}...")
        
        # Remove directories
        docker_dir = Path("/home/docker")
        stack_dir = docker_dir / f"{instance.name}-setup"
        data_dir = docker_dir / instance.name
        
        if stack_dir.exists():
            subprocess.run(["rm", "-rf", str(stack_dir)], check=True)
        if data_dir.exists():
            subprocess.run(["rm", "-rf", str(data_dir)], check=True)
        
        ok(f"Instance {instance.name} deleted successfully")
        return True
        
    except Exception as e:
        error(f"Failed to delete instance: {e}")
        return False


def _view_instance_logs(instance: Instance) -> None:
    """View logs for an instance."""
    say(f"Viewing logs for {instance.name}...")
    
    try:
        # Show recent logs
        result = subprocess.run(
            ["docker", "compose", "logs", "--tail=50"],
            cwd=instance.stack_dir,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
            
        _read("Press Enter to continue...")
        
    except Exception as e:
        error(f"Failed to view logs: {e}")