#!/usr/bin/env python3
"""
Restore Paperless-ngx data from an rclone snapshot.

Handles fetching snapshots, building incremental restore chains,
restoring database and data directories, and running health checks.
"""
import os
import sys
import shutil
import tempfile
import subprocess
import time
from pathlib import Path

# Add the library path so we can import from lib.*
sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")

from lib.utils.common import load_env, load_env_to_environ, say, ok, warn, die
from lib.utils.selftest import run_stack_tests


# Auto-detect stack directory from script location (restore.py is copied to each instance's stack_dir)
SCRIPT_DIR = Path(__file__).resolve().parent

# Look for .env relative to script location first, then fall back to explicit ENV_FILE or legacy default
if (SCRIPT_DIR / ".env").exists():
    ENV_FILE = SCRIPT_DIR / ".env"
else:
    ENV_FILE = Path(os.environ.get("ENV_FILE", "/home/docker/paperless-setup/.env"))

# Only try to load env if it exists, warn but continue if it doesn't
if ENV_FILE.exists():
    load_env_to_environ(ENV_FILE)
else:
    warn(f"No .env at {ENV_FILE} — continuing with environment defaults")

# STACK_DIR should match where this script lives (for multi-instance support)
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
PROJECT_NAME = f"paperless-{INSTANCE_NAME}"
STACK_DIR = Path(os.environ.get("STACK_DIR", str(SCRIPT_DIR)))
DATA_ROOT = Path(os.environ.get("DATA_ROOT", f"/home/docker/{INSTANCE_NAME}"))
COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))
RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
RCLONE_REMOTE_PATH = os.environ.get("RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "paperless")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "paperless")

REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"


def _compose_cmd(*args: str) -> list[str]:
    """Build docker compose command for this instance."""
    return ["docker", "compose", "--project-name", PROJECT_NAME, "-f", str(COMPOSE_FILE), *args]


def fetch_snapshots() -> list[tuple[str, str, str]]:
    res = subprocess.run(
        ["rclone", "lsd", REMOTE], capture_output=True, text=True, check=False
    )
    snaps: list[tuple[str, str, str]] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        name = parts[-1].rstrip("/")
        mode = parent = "?"
        cat = subprocess.run(
            ["rclone", "cat", f"{REMOTE}/{name}/manifest.yaml"],
            capture_output=True,
            text=True,
            check=False,
        )
        if cat.returncode == 0:
            for mline in cat.stdout.splitlines():
                if ":" in mline:
                    k, v = mline.split(":", 1)
                    if k.strip() == "mode":
                        mode = v.strip()
                    elif k.strip() == "parent":
                        parent = v.strip()
        snaps.append((name, mode, parent))
    return sorted(snaps, key=lambda x: x[0])


def extract_tar(tar_path: Path, dest: Path) -> None:
    subprocess.run(
        ["tar", "--listed-incremental=/dev/null", "-xpf", str(tar_path), "-C", str(dest)],
        check=True,
    )


def restore_db(dump: Path) -> None:
    say("Restoring database...")
    subprocess.run(_compose_cmd("up", "-d", "db"), check=True)
    time.sleep(5)
    
    # Clear existing schema to avoid duplicate key errors
    subprocess.run(
        _compose_cmd("exec", "-T", "db", "psql", "-U", POSTGRES_USER, "-d", POSTGRES_DB,
                     "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"),
        check=False
    )
    
    # Restore from dump (handle gzipped or plain SQL)
    if dump.suffix == ".gz":
        proc = subprocess.Popen(["gunzip", "-c", str(dump)], stdout=subprocess.PIPE)
        subprocess.run(
            _compose_cmd("exec", "-T", "db", "psql", "-U", POSTGRES_USER, "-d", POSTGRES_DB),
            stdin=proc.stdout,
            check=False
        )
    else:
        with open(dump, "rb") as fh:
            subprocess.run(
                _compose_cmd("exec", "-T", "db", "psql", "-U", POSTGRES_USER, "-d", POSTGRES_DB),
                stdin=fh,
                check=False
            )


def main() -> None:
    snaps = fetch_snapshots()
    if not snaps:
        die(f"No snapshots found in {REMOTE}")
    names = [n for n, _, _ in snaps]
    target = sys.argv[1] if len(sys.argv) > 1 else names[-1]
    if target not in names:
        die(f"Snapshot {target} not found")
    meta = {n: (m, p) for n, m, p in snaps}
    chain = []
    cur = target
    while True:
        chain.append(cur)
        mode, parent = meta.get(cur, ("full", ""))
        if mode == "full" or not parent:
            break
        cur = parent
    chain.reverse()
    say("Restoring chain: " + " -> ".join(chain))
    
    # Check restore mode:
    # - MERGE_CONFIG=yes: Skip .env and docker-compose.yml restoration (new instance restore)
    #   The manager already created these with user's chosen settings + credentials from backup
    # - MERGE_CONFIG=no: Fully overwrite .env and docker-compose.yml from backup (same instance restore)
    skip_config = os.environ.get("MERGE_CONFIG", "no") == "yes"
    if skip_config:
        say("Keeping instance configuration (already configured by manager)")
    
    # Stop existing containers if compose file exists
    if not COMPOSE_FILE.exists():
        warn(f"Docker compose file not found at {COMPOSE_FILE}")
    else:
        subprocess.run(_compose_cmd("down"), check=False)
    dump_dir = Path(tempfile.mkdtemp(prefix="paperless-restore-dump."))
    final_dump: Path | None = None
    first = True
    for snap in chain:
        tmp = Path(tempfile.mkdtemp(prefix="paperless-restore."))
        subprocess.run(["rclone", "sync", f"{REMOTE}/{snap}", str(tmp)], check=True)
        if first:
            # Handle .env restoration
            backup_env = tmp / ".env"
            if backup_env.exists():
                STACK_DIR.mkdir(parents=True, exist_ok=True)
                if skip_config:
                    # New instance restore: manager already created .env with correct settings
                    say("Keeping instance .env (configured by manager)")
                else:
                    # Same instance restore: replace .env from backup
                    (STACK_DIR / ".env").write_text(backup_env.read_text())
                    ok("Restored .env from backup")
            
            # Restore data directories
            for name in ["data", "media", "export"]:
                dest = DATA_ROOT / name
                dest.mkdir(parents=True, exist_ok=True)  # Ensure destination exists
                if dest.exists() and any(dest.iterdir()):  # Only remove if not empty
                    subprocess.run(["rm", "-rf", str(dest)], check=False)
                dest.mkdir(parents=True, exist_ok=True)  # Recreate after removal
                tarfile_path = next(tmp.glob(f"{name}.tar*"), None)
                if tarfile_path:
                    extract_tar(tarfile_path, DATA_ROOT)
                    ok(f"Restored {name} data")
            
            # Handle docker-compose.yml restoration
            compose_snap = tmp / "compose.snapshot.yml"
            if compose_snap.exists():
                if skip_config:
                    # New instance restore: manager created docker-compose.yml with correct network/port config
                    say("Keeping instance docker-compose.yml (configured by manager)")
                else:
                    # Same instance restore: replace docker-compose.yml from backup
                    COMPOSE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    compose_snap.replace(COMPOSE_FILE)
                    ok("Restored docker-compose.yml from backup")
            first = False
        else:
            for name in ["data", "media", "export"]:
                tarfile_path = next(tmp.glob(f"{name}.tar*"), None)
                if tarfile_path:
                    extract_tar(tarfile_path, DATA_ROOT)
        dump = next(tmp.glob("postgres.sql*"), None)
        if dump:
            final_dump = dump_dir / dump.name
            shutil.move(str(dump), final_dump)
        shutil.rmtree(tmp)
    if final_dump:
        restore_db(final_dump)
    shutil.rmtree(dump_dir, ignore_errors=True)
    
    # Start services and run health check
    if COMPOSE_FILE.exists():
        subprocess.run(_compose_cmd("up", "-d"), check=False)
        if run_stack_tests(COMPOSE_FILE, ENV_FILE):
            ok("Restore complete")
        else:
            warn("Restore complete, but self-test failed")
    else:
        warn("Docker compose file not found - services not started")


def restore_snapshot(snapshot_name: str) -> None:
    """Entry point for restoring a specific snapshot."""
    sys.argv = ["restore.py", snapshot_name]
    main()


if __name__ == "__main__":
    import shutil

    tmp = None
    dump_dir = None
    try:
        main()
    except Exception as e:
        die(f"Restore failed: {e}")
    finally:
        # These are cleaned up inside main(), but just in case
        pass
