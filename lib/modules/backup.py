#!/usr/bin/env python3
"""
Snapshot Paperless-ngx data and upload to an rclone remote.

Creates full, incremental, or archive backups including:
- PostgreSQL database dump
- Incremental tarballs of data directories
- Configuration files and Docker image versions
- Manifest with metadata and integrity verification
"""
import os
import sys
import tempfile
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone

# Add the library path so we can import from lib.*
sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")

from lib.utils.common import load_env_to_environ, say, ok, warn, die


# ─── Configuration ────────────────────────────────────────────────────────────

# Auto-detect stack directory from script location (backup.py is copied to each instance's stack_dir)
SCRIPT_DIR = Path(__file__).resolve().parent

# Look for .env relative to script location first, then fall back to explicit ENV_FILE or legacy default
if (SCRIPT_DIR / ".env").exists():
    ENV_FILE = SCRIPT_DIR / ".env"
else:
    ENV_FILE = Path(os.environ.get("ENV_FILE", "/home/docker/paperless-setup/.env"))

load_env_to_environ(ENV_FILE)
if not ENV_FILE.exists():
    warn(f"No .env at {ENV_FILE} — using defaults")

# STACK_DIR should match where this script lives (for multi-instance support)
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
STACK_DIR = Path(os.environ.get("STACK_DIR", str(SCRIPT_DIR)))
DATA_ROOT = Path(os.environ.get("DATA_ROOT", f"/home/docker/{INSTANCE_NAME}"))
DIR_EXPORT = DATA_ROOT / "export"
DIR_MEDIA = DATA_ROOT / "media"
DIR_DATA = DATA_ROOT / "data"
DIR_SYNCTHING_CONFIG = STACK_DIR / "syncthing-config"  # Consume folder Syncthing config
COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))
RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
RCLONE_REMOTE_PATH = os.environ.get("RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}")
RCLONE_ARCHIVE_PATH = os.environ.get(
    "RCLONE_ARCHIVE_PATH", f"{RCLONE_REMOTE_PATH}/archive"
)
POSTGRES_DB = os.environ.get("POSTGRES_DB", "paperless")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "paperless")

# Backup retention configuration (smart tiered retention)
# Keep ALL snapshots (incr/full/archive) for RETENTION_DAYS (default 30)
# After that, only monthly archives are kept for RETENTION_MONTHLY_DAYS (default 180)
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
RETENTION_MONTHLY_DAYS = int(os.environ.get("RETENTION_MONTHLY_DAYS", "180"))

REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"
ARCHIVE_REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_ARCHIVE_PATH}"


# ─── Helper Functions ─────────────────────────────────────────────────────────

def list_snapshots() -> list[str]:
    """List available snapshots on remote."""
    result = subprocess.run(
        ["rclone", "lsd", REMOTE],
        capture_output=True, text=True, check=False
    )
    snapshots = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if parts:
            snapshots.append(parts[-1].rstrip("/"))
    return sorted(snapshots)


def ensure_remote_path(remote: str) -> None:
    subprocess.run(["rclone", "mkdir", remote], check=False)


def dump_db(work: Path) -> None:
    say("Dumping Postgres database…")
    if COMPOSE_FILE.exists():
        try:
            with open(work / "postgres.sql", "wb") as fh:
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(COMPOSE_FILE),
                        "exec",
                        "-T",
                        "db",
                        "pg_dump",
                        "-U",
                        POSTGRES_USER,
                        POSTGRES_DB,
                    ],
                    check=True,
                    stdout=fh,
                )
        except Exception:
            warn("pg_dump failed (continuing without DB dump)")
    else:
        warn("Compose file not found; skipping DB dump")


def tar_dir(src: Path, name: str, work: Path, mode: str) -> None:
    if not src.exists():
        warn(f"Skip {name}: directory not found at {src}")
        return
    say(f"Archiving {name}…")
    snarf = work / f"{name}.snar"
    if mode == "full" and snarf.exists():
        snarf.unlink()
    subprocess.run(
        [
            "tar",
            "--listed-incremental",
            str(snarf),
            "-czf",
            str(work / f"{name}.tar.gz"),
            "-C",
            str(src.parent),
            name,
        ],
        check=True,
    )


def verify_archives(work: Path) -> bool:
    """Run `tar -t` on produced archives to ensure integrity."""
    all_ok = True
    for tarball in work.glob("*.tar.gz"):
        if (
            subprocess.run(
                ["tar", "-tzf", str(tarball)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            != 0
        ):
            warn(f"Archive verification failed: {tarball.name}")
            all_ok = False
    return all_ok


def capture_docker_versions(work: Path) -> None:
    """Capture Docker image versions for restoration."""
    if not COMPOSE_FILE.exists():
        return
    
    say("Capturing Docker image versions…")
    try:
        # Get currently running images with their digests
        result = subprocess.run(
            [
                "docker", "compose",
                "-f", str(COMPOSE_FILE),
                "images", "--format", "json"
            ],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            # Store raw docker images output
            (work / "docker-images.json").write_text(result.stdout)
        
        # Also capture from compose file for reference
        result = subprocess.run(
            [
                "docker", "compose",
                "-f", str(COMPOSE_FILE),
                "config", "--images"
            ],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            (work / "docker-images.txt").write_text(result.stdout)
            
    except Exception as e:
        warn(f"Failed to capture Docker versions: {e}")


def test_db_restore(work: Path) -> bool:
    """Attempt to restore the dumped DB into a temporary container."""
    dump = work / "postgres.sql"
    if not dump.exists():
        return True
    name = f"paperless-restore-test-{int(time.time())}"
    say("Verifying database dump…")
    try:
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=test", "postgres"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(5)
        with open(dump, "rb") as fh:
            subprocess.run(
                ["docker", "exec", "-i", name, "psql", "-U", "postgres"],
                stdin=fh,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
        return True
    except Exception:
        warn("DB restore test failed")
        subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
        return False


def main() -> Path:
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode not in {"full", "incr", "archive"}:
        die("Usage: backup.py [full|incr|archive]")
    ensure_remote_path(ARCHIVE_REMOTE if mode == "archive" else REMOTE)
    snaps = list_snapshots()
    parent = snaps[-1] if snaps else ""
    if mode == "incr" and not snaps:
        mode = "full"
    snap = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    work = Path(tempfile.mkdtemp(prefix="paperless-backup."))
    if mode == "incr" and parent:
        subprocess.run(
            ["rclone", "copy", f"{REMOTE}/{parent}", str(work), "--include", "*.snar"],
            check=False,
        )
    say(f"Creating {mode} snapshot {snap}")

    dump_db(work)
    tar_mode = "full" if mode in {"full", "archive"} else "incr"
    tar_dir(DIR_MEDIA, "media", work, tar_mode)
    tar_dir(DIR_DATA, "data", work, tar_mode)
    tar_dir(DIR_EXPORT, "export", work, tar_mode)
    
    # Backup Syncthing config if it exists (for consume folder sync)
    if DIR_SYNCTHING_CONFIG.exists():
        tar_dir(DIR_SYNCTHING_CONFIG, "syncthing-config", work, tar_mode)

    if ENV_FILE.exists():
        (work / ".env").write_text(ENV_FILE.read_text())
    else:
        warn(f"No .env found at {ENV_FILE}")

    if COMPOSE_FILE.exists():
        (work / "compose.snapshot.yml").write_text(COMPOSE_FILE.read_text())
    
    # Capture Docker image versions for restoration
    capture_docker_versions(work)

    manifest_lines = [f"mode: {mode}", f"created: {datetime.now(timezone.utc).isoformat()}"]
    if mode == "incr" and parent:
        manifest_lines.append(f"parent: {parent}")
    (work / "manifest.yaml").write_text("\n".join(manifest_lines) + "\n")

    passed = verify_archives(work) and test_db_restore(work)
    status = "status.ok" if passed else "status.fail"
    (work / status).write_text(datetime.now(timezone.utc).isoformat() + "\n")
    if passed:
        ok("Integrity checks passed")
    else:
        warn("Integrity checks failed")

    dest_root = ARCHIVE_REMOTE if mode == "archive" else REMOTE
    dest = f"{dest_root}/{snap}"
    say(f"Uploading to {dest}")
    subprocess.run(
        [
            "rclone",
            "copy",
            str(work),
            dest,
            "--checksum",
            "--transfers",
            "4",
            "--checkers",
            "8",
            "--fast-list",
        ],
        check=True,
    )

    # Run retention cleanup after backup
    if RETENTION_DAYS > 0:
        run_retention_cleanup()

    ok("Backup completed")
    return work


def list_archive_snapshots() -> list[str]:
    """List available archive snapshots on remote."""
    result = subprocess.run(
        ["rclone", "lsd", ARCHIVE_REMOTE],
        capture_output=True, text=True, check=False
    )
    snapshots = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if parts:
            snapshots.append(parts[-1].rstrip("/"))
    return sorted(snapshots)


def parse_snapshot_date(snap_name: str) -> datetime | None:
    """Parse snapshot name (YYYY-MM-DD_HH-MM-SS) to datetime."""
    try:
        return datetime.strptime(snap_name[:19], "%Y-%m-%d_%H-%M-%S")
    except (ValueError, IndexError):
        return None


def is_first_of_month(snap_name: str) -> bool:
    """Check if snapshot was taken on the 1st of the month."""
    dt = parse_snapshot_date(snap_name)
    return dt is not None and dt.day == 1


def run_retention_cleanup() -> None:
    """
    Smart tiered retention cleanup:
    - Keep ALL snapshots (standard + archive) for RETENTION_DAYS
    - After RETENTION_DAYS, delete non-archive snapshots
    - Keep monthly archives (1st of month) for RETENTION_MONTHLY_DAYS
    - Delete archives older than RETENTION_MONTHLY_DAYS
    """
    say("Running retention cleanup...")
    now = datetime.now()
    
    # 1. Clean up standard backups older than RETENTION_DAYS
    if RETENTION_DAYS > 0:
        say(f"  Cleaning standard backups older than {RETENTION_DAYS} days...")
        subprocess.run(
            [
                "rclone", "delete", REMOTE,
                "--min-age", f"{RETENTION_DAYS}d",
                "--fast-list",
            ],
            check=False,
        )
        subprocess.run(["rclone", "rmdirs", REMOTE, "--leave-root"], check=False)
    
    # 2. Clean up archive backups with tiered retention
    if RETENTION_MONTHLY_DAYS > 0:
        # Check if archive path exists before attempting cleanup
        archive_check = subprocess.run(
            ["rclone", "lsd", ARCHIVE_REMOTE],
            capture_output=True, text=True, check=False
        )
        if archive_check.returncode != 0 or not archive_check.stdout.strip():
            say("  No archive directory yet, skipping archive cleanup")
        else:
            archives = list_archive_snapshots()
            deleted_count = 0
            kept_monthly = []
            
            for snap in archives:
                snap_date = parse_snapshot_date(snap)
                if snap_date is None:
                    continue
                
                age_days = (now - snap_date).days
                
                # Within RETENTION_DAYS: keep all archives
                if age_days <= RETENTION_DAYS:
                    continue
                
                # Between RETENTION_DAYS and RETENTION_MONTHLY_DAYS: keep only 1st-of-month
                if age_days <= RETENTION_MONTHLY_DAYS:
                    if is_first_of_month(snap):
                        kept_monthly.append(snap)
                        continue
                    else:
                        # Delete non-monthly archives older than retention period
                        say(f"  Removing archive {snap} (not monthly, {age_days}d old)...")
                        subprocess.run(
                            ["rclone", "purge", f"{ARCHIVE_REMOTE}/{snap}"],
                            check=False, capture_output=True
                        )
                        deleted_count += 1
                else:
                    # Older than RETENTION_MONTHLY_DAYS: delete even monthly archives
                    say(f"  Removing archive {snap} ({age_days}d old, exceeds {RETENTION_MONTHLY_DAYS}d)...")
                    subprocess.run(
                        ["rclone", "purge", f"{ARCHIVE_REMOTE}/{snap}"],
                        check=False, capture_output=True
                    )
                    deleted_count += 1
            
            if deleted_count > 0:
                ok(f"  Removed {deleted_count} old archive(s)")
            if kept_monthly:
                say(f"  Kept {len(kept_monthly)} monthly archive(s)")
    
    subprocess.run(["rclone", "rmdirs", ARCHIVE_REMOTE, "--leave-root"], check=False)
    ok("Retention cleanup complete")


def cleanup_main() -> None:
    """Standalone cleanup entry point (can be called via cron or manually)."""
    run_retention_cleanup()


if __name__ == "__main__":
    import shutil

    # Support cleanup mode
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup_main()
        sys.exit(0)

    work = None
    try:
        work = main()
    except Exception as e:
        die(f"Backup failed: {e}")
    finally:
        if work is not None and Path(work).exists():
            shutil.rmtree(work)
