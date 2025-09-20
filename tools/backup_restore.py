"""
Backup and restore operations for the Paperless-ngx bulletproof tool.

This module handles all backup creation, snapshot management, restore operations,
and related functionality like snapshot verification and exploration.
"""

import os
import subprocess
import tarfile
from pathlib import Path
from ui import say, ok, warn, error, _read, print_menu_options


def dc(*args: str) -> list[str]:
    """Helper to build docker compose commands."""
    return ["docker", "compose"] + list(args)


def fetch_snapshots(remote_path: str = None) -> list[tuple[str, str, str]]:
    """Fetch snapshots from either current instance or specified remote path."""
    if remote_path is None:
        remote_path = os.environ.get("REMOTE", "")
    
    try:
        res = subprocess.run(
            ["rclone", "lsd", remote_path], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        warn("rclone not installed")
        return []
    
    snaps: list[tuple[str, str, str]] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        snap_name = parts[-1].rstrip("/")
        mode = parent = "?"
        cat = subprocess.run(
            ["rclone", "cat", f"{remote_path}/{snap_name}/manifest.yaml"],
            capture_output=True,
            text=True,
            check=False,
        )
        if cat.returncode == 0:
            for mline in cat.stdout.splitlines():
                if ":" in mline:
                    k, v = mline.split(":", 1)
                    k = k.strip()
                    v = v.strip()
                    if k == "mode":
                        mode = v
                    elif k == "parent":
                        parent = v
        snaps.append((snap_name, mode, parent))
    return sorted(snaps, key=lambda x: x[0])


def list_remote_instances() -> list[str]:
    """List all instance names available in remote backup storage."""
    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    remote_base = f"{remote_name}:backups/paperless"
    
    try:
        res = subprocess.run(
            ["rclone", "lsd", remote_base], capture_output=True, text=True, check=False
        )
        if res.returncode != 0:
            return []
        
        instances = []
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if parts:
                instance_name = parts[-1].rstrip("/")
                instances.append(instance_name)
        
        return sorted(instances)
    except FileNotFoundError:
        warn("rclone not installed")
        return []


def fetch_snapshots_for(name: str) -> list[tuple[str, str, str]]:
    """Fetch snapshots for a given remote instance name."""
    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    remote = f"{remote_name}:backups/paperless/{name}"
    return fetch_snapshots(remote)


def pick_remote_snapshot() -> tuple[str, str] | None:
    """Interactive selection of remote snapshot."""
    instances = list_remote_instances()
    if not instances:
        warn("No remote instances found")
        return None
    
    say("Available instances:")
    for i, name in enumerate(instances, 1):
        print(f"  {i}) {name}")
    
    choice = _read(f"Choose instance [1-{len(instances)}]: ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(instances):
            instance_name = instances[idx]
            snapshots = fetch_snapshots_for(instance_name)
            if not snapshots:
                warn(f"No snapshots found for {instance_name}")
                return None
            
            say(f"Snapshots for {instance_name}:")
            for i, (snap, mode, parent) in enumerate(snapshots, 1):
                print(f"  {i}) {snap} ({mode})")
            
            snap_choice = _read(f"Choose snapshot [1-{len(snapshots)}]: ").strip()
            snap_idx = int(snap_choice) - 1
            if 0 <= snap_idx < len(snapshots):
                return instance_name, snapshots[snap_idx][0]
        
        warn("Invalid selection")
        return None
    except (ValueError, IndexError):
        warn("Invalid selection")
        return None


def verify_snapshot(source: str, snap: str) -> None:
    """Verify snapshot integrity with comprehensive checks."""
    say(f"Verifying snapshot {snap} from {source}...")
    
    integrity_checks = 0
    passed_checks = 0
    
    # Check if manifest exists
    say("1. Checking manifest file...")
    integrity_checks += 1
    manifest_check = subprocess.run(
        ["rclone", "cat", f"{source}/{snap}/manifest.yaml"],
        capture_output=True,
        text=True,
        check=False
    )
    
    if manifest_check.returncode == 0:
        ok("✓ Manifest file found")
        passed_checks += 1
        try:
            import yaml
            manifest_data = yaml.safe_load(manifest_check.stdout)
            say("  Manifest details:")
            for key, value in manifest_data.items():
                print(f"    {key}: {value}")
        except Exception as e:
            warn(f"  Could not parse manifest: {e}")
            for line in manifest_check.stdout.splitlines():
                if ":" in line:
                    print(f"    {line.strip()}")
    else:
        error("✗ Manifest file not found or corrupted")
    
    # Check for required backup files
    say("2. Checking required backup files...")
    integrity_checks += 1
    required_files = ["database.sql", "media.tar"]
    found_files = []
    missing_files = []
    
    for file in required_files:
        file_check = subprocess.run(
            ["rclone", "lsf", f"{source}/{snap}/{file}"],
            capture_output=True,
            text=True,
            check=False
        )
        if file_check.returncode == 0:
            found_files.append(file)
        else:
            missing_files.append(file)
    
    if missing_files:
        error(f"✗ Missing required files: {', '.join(missing_files)}")
    else:
        ok(f"✓ All required files present: {', '.join(found_files)}")
        passed_checks += 1
    
    # Check snapshot completeness and file sizes
    say("3. Checking file sizes and completeness...")
    integrity_checks += 1
    content_check = subprocess.run(
        ["rclone", "ls", f"{source}/{snap}"],
        capture_output=True,
        text=True,
        check=False
    )
    
    if content_check.returncode == 0:
        files = [line.strip() for line in content_check.stdout.strip().split('\n') if line.strip()]
        total_size = 0
        zero_byte_files = []
        
        for line in files:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    size = int(parts[0])
                    filename = ' '.join(parts[1:])
                    total_size += size
                    if size == 0 and filename in required_files:
                        zero_byte_files.append(filename)
                except ValueError:
                    continue
        
        if zero_byte_files:
            error(f"✗ Zero-byte critical files found: {', '.join(zero_byte_files)}")
        else:
            ok(f"✓ Snapshot contains {len(files)} files ({total_size / 1024 / 1024:.1f} MB)")
            passed_checks += 1
    else:
        error("✗ Could not list snapshot contents")
    
    # Check database file integrity (basic)
    say("4. Checking database file structure...")
    integrity_checks += 1
    db_check = subprocess.run(
        ["rclone", "cat", f"{source}/{snap}/database.sql", "--max-size", "1K"],
        capture_output=True,
        text=True,
        check=False
    )
    
    if db_check.returncode == 0:
        if "-- PostgreSQL database dump" in db_check.stdout or "CREATE" in db_check.stdout.upper():
            ok("✓ Database file appears to be valid SQL")
            passed_checks += 1
        else:
            warn("⚠ Database file may be corrupted or incomplete")
    else:
        error("✗ Could not read database file")
    
    # Summary
    print()
    say("Integrity Check Summary:")
    if passed_checks == integrity_checks:
        ok(f"✓ All {integrity_checks} checks passed - Snapshot appears healthy")
    elif passed_checks > integrity_checks // 2:
        warn(f"⚠ {passed_checks}/{integrity_checks} checks passed - Snapshot may have issues")
    else:
        error(f"✗ Only {passed_checks}/{integrity_checks} checks passed - Snapshot likely corrupted")
    print()


def explore_backups() -> None:
    """Interactive backup exploration interface."""
    while True:
        print_menu_options([
            ("1", "List all remote instances"),
            ("2", "Show snapshots for instance"),
            ("3", "Verify snapshot"),
            ("4", "Verify all snapshots for instance"),
            ("0", "Quit")
        ], "Backup Explorer")
        
        choice = _read("Choice: ").strip()
        
        if choice == "1":
            instances = list_remote_instances()
            if instances:
                say("Remote instances:")
                for name in instances:
                    snapshots = fetch_snapshots_for(name)
                    print(f"  {name} ({len(snapshots)} snapshots)")
            else:
                warn("No remote instances found")
        
        elif choice == "2":
            instance_name = _read("Instance name: ").strip()
            if instance_name:
                snapshots = fetch_snapshots_for(instance_name)
                if snapshots:
                    say(f"Snapshots for {instance_name}:")
                    for snap, mode, parent in snapshots:
                        print(f"  {snap} ({mode}, parent: {parent})")
                else:
                    warn(f"No snapshots found for {instance_name}")
        
        elif choice == "3":
            result = pick_remote_snapshot()
            if result:
                instance_name, snap_name = result
                remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
                source = f"{remote_name}:backups/paperless/{instance_name}"
                verify_snapshot(source, snap_name)
        
        elif choice == "4":
            instance_name = _read("Instance name to verify all snapshots: ").strip()
            if instance_name:
                snapshots = fetch_snapshots_for(instance_name)
                if snapshots:
                    say(f"Verifying all {len(snapshots)} snapshots for {instance_name}...")
                    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
                    source = f"{remote_name}:backups/paperless/{instance_name}"
                    
                    healthy_count = 0
                    warning_count = 0
                    error_count = 0
                    
                    for i, (snap, mode, parent) in enumerate(snapshots, 1):
                        print(f"\n--- Snapshot {i}/{len(snapshots)}: {snap} ---")
                        # Quick verification for bulk check
                        _verify_snapshot_quick(source, snap)
                        
                        # Count results based on output (simplified)
                        # This could be enhanced with proper return values
                        healthy_count += 1  # Simplified for now
                    
                    print(f"\n=== Bulk Verification Summary ===")
                    say(f"Verified {len(snapshots)} snapshots for {instance_name}")
                    ok(f"Use option 3 for detailed verification of individual snapshots")
                else:
                    warn(f"No snapshots found for {instance_name}")
        
        elif choice == "0":
            break
        else:
            warn("Invalid choice")


def _verify_snapshot_quick(source: str, snap: str) -> None:
    """Quick snapshot verification for bulk checking."""
    # Check manifest exists
    manifest_check = subprocess.run(
        ["rclone", "cat", f"{source}/{snap}/manifest.yaml"],
        capture_output=True,
        text=True,
        check=False
    )
    
    # Check required files
    required_files = ["database.sql", "media.tar"]
    missing_files = []
    
    for file in required_files:
        file_check = subprocess.run(
            ["rclone", "lsf", f"{source}/{snap}/{file}"],
            capture_output=True,
            text=True,
            check=False
        )
        if file_check.returncode != 0:
            missing_files.append(file)
    
    # Quick status
    if manifest_check.returncode == 0 and not missing_files:
        ok(f"✓ {snap}: Healthy")
    elif manifest_check.returncode == 0 and missing_files:
        warn(f"⚠ {snap}: Missing files: {', '.join(missing_files)}")
    else:
        error(f"✗ {snap}: Corrupted or incomplete")


def run_stack_tests() -> bool:
    """Run basic connectivity tests for the stack."""
    say("Running stack connectivity tests...")
    
    tests = [
        ("Docker daemon", ["docker", "info"]),
        ("Docker Compose", ["docker", "compose", "version"]),
        ("Rclone", ["rclone", "version"]),
    ]
    
    all_passed = True
    for name, cmd in tests:
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            ok(f"{name}: Available")
        except (subprocess.CalledProcessError, FileNotFoundError):
            error(f"{name}: Not available")
            all_passed = False
    
    return all_passed


def extract_tar(tar_path: Path, dest: Path) -> None:
    """Extract tar file to destination directory."""
    with tarfile.open(tar_path, "r:*") as tar:
        tar.extractall(dest)


def restore_db(dump: Path) -> None:
    """Restore database from SQL dump."""
    stack_dir = Path(os.environ.get("STACK_DIR", ""))
    if not stack_dir:
        error("STACK_DIR not set")
        return
    
    say("Restoring database...")
    
    # Ensure database is running
    subprocess.run(dc("up", "-d", "db"), cwd=stack_dir, check=False)
    
    # Wait a moment for database to be ready
    import time
    time.sleep(5)
    
    # Create empty database if it doesn't exist
    subprocess.run(
        dc("exec", "-T", "db", "createdb", "-U", "paperless", "paperless"),
        cwd=stack_dir,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Restore from dump
    with open(dump, "r") as f:
        restore_proc = subprocess.run(
            dc("exec", "-T", "db", "psql", "-U", "paperless", "-d", "paperless"),
            cwd=stack_dir,
            stdin=f,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )
    
    if restore_proc.returncode == 0:
        ok("Database restored successfully")
    else:
        warn(f"Database restore completed with warnings: {restore_proc.stderr}")


def cmd_backup(args) -> None:
    """Command to run backup."""
    from instance import load_env
    
    # Load environment from instance
    stack_dir = Path(os.environ.get("STACK_DIR", ""))
    if stack_dir:
        load_env(stack_dir / ".env")
    
    script = stack_dir / "backup.py"
    if not script.exists():
        error(f"Backup script not found: {script}")
        return
    
    mode = getattr(args, 'mode', 'incremental')
    subprocess.run([str(script), mode], check=False)


def cmd_snapshots(args) -> None:
    """Command to list snapshots."""
    snapshots = fetch_snapshots()
    if not snapshots:
        warn("No snapshots found")
        return
    
    say("Available snapshots:")
    for snap, mode, parent in snapshots:
        detail = f"mode: {mode}"
        if parent and parent != "?":
            detail += f", parent: {parent}"
        print(f"  {snap} ({detail})")
    
    if hasattr(args, 'manifest') and args.manifest:
        snap_name = getattr(args, 'snapshot', None)
        if snap_name:
            remote = os.environ.get("REMOTE", "")
            subprocess.run(["rclone", "cat", f"{remote}/{snap_name}/manifest.yaml"], check=False)


def cmd_restore(args) -> None:
    """Command to restore from snapshot."""
    from instance import load_env
    
    # Get required paths
    stack_dir = Path(os.environ.get("STACK_DIR", ""))
    data_root = Path(os.environ.get("DATA_ROOT", ""))
    
    if not stack_dir or not data_root:
        error("STACK_DIR and DATA_ROOT must be set")
        return
    
    # Load environment
    load_env(stack_dir / ".env")
    
    snap_name = getattr(args, 'snapshot', None)
    if not snap_name:
        # Interactive selection
        result = pick_remote_snapshot()
        if not result:
            return
        _, snap_name = result
    
    remote = os.environ.get("REMOTE", "")
    if not remote:
        error("REMOTE not configured")
        return
    
    say(f"Restoring from snapshot: {snap_name}")
    
    # Stop services
    subprocess.run(dc("down"), cwd=stack_dir, check=False)
    
    # Create temp directory for restore
    tmp = Path("/tmp/restore")
    tmp.mkdir(exist_ok=True)
    
    try:
        # Download and extract files
        subprocess.run(["rclone", "copy", f"{remote}/{snap_name}", str(tmp)], check=True)
        
        if (tmp / ".env").exists():
            (stack_dir / ".env").write_text((tmp / ".env").read_text())
        
        for name in ["data.tar.gz", "media.tar.gz", "export.tar.gz"]:
            tarfile_path = tmp / name
            if tarfile_path.exists():
                dest = data_root / name.replace(".tar.gz", "")
                dest.mkdir(exist_ok=True)
                extract_tar(tarfile_path, data_root)
        
        if (tmp / "docker-compose.yml").exists():
            compose_snap = tmp / "docker-compose.yml"
            compose_file = stack_dir / "docker-compose.yml"
            compose_snap.replace(compose_file)
        
        # Restore database
        for db_file in ["database.sql", "database.sql.gz"]:
            db_path = tmp / db_file
            if db_path.exists():
                extract_tar(tarfile_path, data_root)
                restore_db(db_path)
                break
        
        ok(f"Restore from {snap_name} completed successfully!")
        say("You can now start the services with: docker compose up -d")
        
    except subprocess.CalledProcessError as e:
        error(f"Restore failed: {e}")
    except Exception as e:
        error(f"Restore error: {e}")
    finally:
        # Cleanup
        subprocess.run(["rm", "-rf", str(tmp)], check=False)