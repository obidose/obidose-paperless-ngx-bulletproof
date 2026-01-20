#!/usr/bin/env python3
"""
Backup management for Paperless-NGX Bulletproof.

Provides backup and restore operations for instances.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from lib.ui import Colors, colorize, say, ok, warn, error
from lib.instance import Instance

if TYPE_CHECKING:
    pass


class BackupManager:
    """Manages backup and restore operations for a Paperless-NGX instance."""
    
    def __init__(self, instance: Instance):
        self.instance = instance
        self.remote_name = instance.get_env_value("RCLONE_REMOTE_NAME", "pcloud")
        self.remote_path = instance.get_env_value("RCLONE_REMOTE_PATH", f"backups/paperless/{instance.name}")
        self.remote_base = f"{self.remote_name}:{self.remote_path}"

    def fetch_snapshots(self) -> list[tuple[str, str, str]]:
        """Fetch list of available backup snapshots.
        
        Returns:
            List of tuples: (snapshot_name, date_str, type)
        """
        result = subprocess.run(
            ["rclone", "lsd", self.remote_base],
            capture_output=True, text=True, check=False
        )
        
        if result.returncode != 0:
            return []
        
        snapshots = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 4:
                name = parts[-1]
                # Parse snapshot name format: YYYYMMDD-HHMMSS-type
                try:
                    if "-" in name:
                        date_part = name.split("-")[0]
                        time_part = name.split("-")[1] if len(name.split("-")) > 1 else "000000"
                        backup_type = name.split("-")[2] if len(name.split("-")) > 2 else "unknown"
                        
                        dt = datetime.strptime(f"{date_part}-{time_part}", "%Y%m%d-%H%M%S")
                        date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        date_str = name
                        backup_type = "unknown"
                    
                    snapshots.append((name, date_str, backup_type))
                except ValueError:
                    snapshots.append((name, name, "unknown"))
        
        # Sort by name (which is date-based) descending
        snapshots.sort(key=lambda x: x[0], reverse=True)
        return snapshots

    def run_backup(self, mode: str = "incr") -> bool:
        """Run a backup operation.
        
        Args:
            mode: Backup mode - 'incr' for incremental, 'full' for full backup
            
        Returns:
            True if backup succeeded
        """
        say(f"Running {mode} backup for {self.instance.name}...")
        
        # Use the backup module
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        
        env = os.environ.copy()
        env_file = self.instance.stack_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        
        result = subprocess.run(
            ["python3", "-c", f"from lib.modules.backup import run_backup; run_backup('{mode}')"],
            env=env, cwd="/usr/local/lib/paperless-bulletproof",
            capture_output=False, check=False
        )
        
        return result.returncode == 0

    def run_restore(self, snapshot: Optional[str] = None) -> bool:
        """Run a restore operation.
        
        Args:
            snapshot: Specific snapshot to restore, or None for latest
            
        Returns:
            True if restore succeeded
        """
        say(f"Running restore for {self.instance.name}...")
        
        env = os.environ.copy()
        env_file = self.instance.stack_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        
        if snapshot:
            env["RESTORE_SNAPSHOT"] = snapshot
        
        result = subprocess.run(
            ["python3", "-c", "from lib.modules.restore import run_restore; run_restore()"],
            env=env, cwd="/usr/local/lib/paperless-bulletproof",
            capture_output=False, check=False
        )
        
        return result.returncode == 0


def run_restore_with_env(
    stack_dir: Path,
    data_root: Path,
    instance_name: str,
    remote_name: str,
    remote_path: str,
    snapshot: str = None,
    fresh_config: bool = False
) -> bool:
    """
    Run restore operation with explicit environment configuration.
    
    This is used for restoring instances where we need to set up the
    environment before the restore operation.
    
    Args:
        stack_dir: Path to the instance's stack directory
        data_root: Path to the instance's data directory
        instance_name: Name of the instance
        remote_name: Rclone remote name
        remote_path: Path on the remote
        snapshot: Specific snapshot to restore (optional)
        fresh_config: Whether this is a fresh config that needs special handling
        
    Returns:
        True if restore succeeded
    """
    say(f"Restoring {instance_name} from {remote_name}:{remote_path}...")
    
    # Build environment
    env = os.environ.copy()
    env["STACK_DIR"] = str(stack_dir)
    env["DATA_ROOT"] = str(data_root)
    env["INSTANCE_NAME"] = instance_name
    env["RCLONE_REMOTE_NAME"] = remote_name
    env["RCLONE_REMOTE_PATH"] = remote_path
    
    # Load existing .env if available
    env_file = stack_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    
    if snapshot:
        env["RESTORE_SNAPSHOT"] = snapshot
    
    if fresh_config:
        env["FRESH_CONFIG"] = "true"
    
    # Run the restore module
    result = subprocess.run(
        ["python3", "-c", "from lib.modules.restore import run_restore; run_restore()"],
        env=env, cwd="/usr/local/lib/paperless-bulletproof",
        capture_output=False, check=False
    )
    
    return result.returncode == 0


def get_backup_size(remote_path: str) -> str:
    """Get the total size of backups for an instance."""
    result = subprocess.run(
        ["rclone", "size", remote_path, "--json"],
        capture_output=True, text=True, check=False
    )
    
    if result.returncode != 0:
        return "unknown"
    
    try:
        import json
        data = json.loads(result.stdout)
        bytes_size = data.get("bytes", 0)
        
        # Convert to human readable
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_size < 1024:
                return f"{bytes_size:.1f} {unit}"
            bytes_size /= 1024
        return f"{bytes_size:.1f} PB"
    except Exception:
        return "unknown"


def count_snapshots(remote_path: str) -> int:
    """Count the number of snapshots for an instance."""
    result = subprocess.run(
        ["rclone", "lsd", remote_path],
        capture_output=True, text=True, check=False
    )
    
    if result.returncode != 0:
        return 0
    
    return len([l for l in result.stdout.splitlines() if l.strip()])


def delete_snapshot(remote_path: str, snapshot_name: str) -> bool:
    """Delete a specific snapshot."""
    full_path = f"{remote_path}/{snapshot_name}"
    result = subprocess.run(
        ["rclone", "purge", full_path],
        capture_output=True, check=False
    )
    return result.returncode == 0
