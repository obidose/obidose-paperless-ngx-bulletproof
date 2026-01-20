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


# ─── Snapshot Data Structure ──────────────────────────────────────────────────

from dataclasses import dataclass

@dataclass
class Snapshot:
    """Represents a backup snapshot with metadata."""
    name: str
    mode: str  # 'full', 'incr', or 'archive'
    parent: str  # Parent snapshot name for incremental backups
    created: str  # ISO timestamp
    has_docker_versions: bool  # Whether docker-images.txt exists


class BackupManager:
    """Manages backup and restore operations for a Paperless-NGX instance."""
    
    def __init__(self, instance: Instance):
        self.instance = instance
        self.remote_name = instance.get_env_value("RCLONE_REMOTE_NAME", "pcloud")
        # Always use the standard backup path structure
        self.remote_path = f"backups/paperless/{instance.name}"
        self.remote_base = f"{self.remote_name}:{self.remote_path}"

    @staticmethod
    def fetch_snapshots_for_path(remote_path: str) -> list[Snapshot]:
        """Fetch snapshots from a specific remote path.
        
        This is a static method that can be used without an Instance object,
        useful for the backup explorer which works with arbitrary paths.
        
        Args:
            remote_path: Full rclone path like 'pcloud:backups/paperless/john'
            
        Returns:
            List of Snapshot objects sorted by name (oldest first)
        """
        result = subprocess.run(
            ["rclone", "lsd", remote_path],
            capture_output=True, text=True, check=False
        )
        
        if result.returncode != 0:
            return []
        
        snapshots = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 1:
                snap_name = parts[-1]
                
                # Get manifest info
                mode = "full"
                parent = ""
                created = ""
                
                manifest_result = subprocess.run(
                    ["rclone", "cat", f"{remote_path}/{snap_name}/manifest.yaml"],
                    capture_output=True, text=True, check=False, timeout=10
                )
                
                if manifest_result.returncode == 0:
                    for mline in manifest_result.stdout.splitlines():
                        if ":" in mline:
                            k, v = mline.split(":", 1)
                            k, v = k.strip(), v.strip()
                            if k == "mode":
                                mode = v
                            elif k == "parent":
                                parent = v
                            elif k == "created":
                                created = v[:19]  # Just date/time portion
                
                # Check for docker versions file
                has_docker = subprocess.run(
                    ["rclone", "lsf", f"{remote_path}/{snap_name}/docker-images.txt"],
                    capture_output=True, check=False
                ).returncode == 0
                
                snapshots.append(Snapshot(
                    name=snap_name,
                    mode=mode,
                    parent=parent,
                    created=created,
                    has_docker_versions=has_docker
                ))
        
        # Sort by name (which is date-based)
        snapshots.sort(key=lambda x: x.name)
        return snapshots

    def fetch_snapshots(self) -> list[tuple[str, str, str]]:
        """Fetch list of available backup snapshots.
        
        Returns:
            List of tuples: (snapshot_name, mode, parent)
            where mode is 'full', 'incr', or 'archive'
        """
        snapshots = self.fetch_snapshots_for_path(self.remote_base)
        return [(s.name, s.mode, s.parent) for s in snapshots]

    def fetch_snapshots_detailed(self) -> list[Snapshot]:
        """Fetch detailed snapshot info including created time and docker versions.
        
        Returns:
            List of Snapshot objects
        """
        return self.fetch_snapshots_for_path(self.remote_base)

    def run_backup(self, mode: str = "incr") -> bool:
        """Run a backup operation.
        
        Args:
            mode: Backup mode - 'incr' for incremental, 'full' for full backup, 'archive' for archive
            
        Returns:
            True if backup succeeded
        """
        say(f"Running {mode} backup for {self.instance.name}...")
        
        env = os.environ.copy()
        
        # Set critical environment variables that backup module needs
        env["INSTANCE_NAME"] = self.instance.name
        env["STACK_DIR"] = str(self.instance.stack_dir)
        env["DATA_ROOT"] = str(self.instance.data_root)
        env["RCLONE_REMOTE_NAME"] = self.remote_name
        env["RCLONE_REMOTE_PATH"] = self.remote_path
        env["ENV_FILE"] = str(self.instance.env_file)
        env["COMPOSE_FILE"] = str(self.instance.compose_file)
        
        # Also load any additional vars from the .env file
        env_file = self.instance.env_file
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    # Don't override the critical vars we set above
                    if k.strip() not in env:
                        env[k.strip()] = v.strip()
        
        # Build backup command - call _refresh_globals_from_env() to pick up our env vars
        backup_cmd = f"import sys; sys.argv = ['backup.py', '{mode}']; from lib.modules.backup import _refresh_globals_from_env, main; _refresh_globals_from_env(); main()"
        
        result = subprocess.run(
            ["python3", "-c", backup_cmd],
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
        
        # Set critical environment variables that restore module needs
        env["INSTANCE_NAME"] = self.instance.name
        env["STACK_DIR"] = str(self.instance.stack_dir)
        env["DATA_ROOT"] = str(self.instance.data_root)
        env["RCLONE_REMOTE_NAME"] = self.remote_name
        env["RCLONE_REMOTE_PATH"] = self.remote_path
        env["ENV_FILE"] = str(self.instance.env_file)
        env["COMPOSE_FILE"] = str(self.instance.compose_file)
        
        # Also load any additional vars from the .env file
        env_file = self.instance.env_file
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    # Don't override the critical vars we set above
                    if k.strip() not in env:
                        env[k.strip()] = v.strip()
        
        # Build the restore command - call _refresh_globals_from_env() to pick up our env vars
        if snapshot:
            restore_cmd = f"import sys; sys.argv = ['restore.py', '{snapshot}']; from lib.modules.restore import _refresh_globals_from_env, main; _refresh_globals_from_env(); main()"
        else:
            restore_cmd = "from lib.modules.restore import _refresh_globals_from_env, main; _refresh_globals_from_env(); main()"
        
        result = subprocess.run(
            ["python3", "-c", restore_cmd],
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
    
    if fresh_config:
        env["FRESH_CONFIG"] = "true"
    
    # Build the restore command - pass snapshot via sys.argv as restore module expects
    if snapshot:
        restore_cmd = f"import sys; sys.argv = ['restore.py', '{snapshot}']; from lib.modules.restore import main; main()"
    else:
        restore_cmd = "from lib.modules.restore import main; main()"
    
    # Run the restore module
    result = subprocess.run(
        ["python3", "-c", restore_cmd],
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
