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

# Look for .env in order of priority:
# 1. Relative to script location (when restore.py is in stack_dir)
# 2. From STACK_DIR env var (when called via run_restore_with_env)
# 3. From explicit ENV_FILE env var
# 4. Legacy default
if (SCRIPT_DIR / ".env").exists():
    ENV_FILE = SCRIPT_DIR / ".env"
elif os.environ.get("STACK_DIR") and (Path(os.environ["STACK_DIR"]) / ".env").exists():
    ENV_FILE = Path(os.environ["STACK_DIR"]) / ".env"
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
    
    # Completely drop and recreate the database to handle corrupted catalogs
    # Using postgres database to issue DROP/CREATE commands
    say("Dropping and recreating database...")
    subprocess.run(
        _compose_cmd("exec", "-T", "db", "psql", "-U", POSTGRES_USER, "-d", "postgres",
                     "-c", f"DROP DATABASE IF EXISTS {POSTGRES_DB};"),
        check=False
    )
    subprocess.run(
        _compose_cmd("exec", "-T", "db", "psql", "-U", POSTGRES_USER, "-d", "postgres",
                     "-c", f"CREATE DATABASE {POSTGRES_DB} OWNER {POSTGRES_USER};"),
        check=True
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
    # RESTORE_SYNCTHING: Override syncthing behavior for system restore
    # When true, restore syncthing config even if skip_config is true
    force_syncthing_restore = os.environ.get("RESTORE_SYNCTHING", "no") == "yes"
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
            
            # Restore syncthing-config if it exists in backup (consume folder sync config)
            # Skip for clones (MERGE_CONFIG=yes without RESTORE_SYNCTHING) - clones need fresh setup
            # But DO restore for system restore (MERGE_CONFIG=yes WITH RESTORE_SYNCTHING=yes)
            syncthing_tarfile = next(tmp.glob("syncthing-config.tar*"), None)
            if syncthing_tarfile:
                if skip_config and not force_syncthing_restore:
                    say("Skipping syncthing-config (clone needs fresh consume folder setup)")
                else:
                    syncthing_config_dir = STACK_DIR / "syncthing-config"
                    syncthing_config_dir.mkdir(parents=True, exist_ok=True)
                    extract_tar(syncthing_tarfile, STACK_DIR)
                    ok("Restored syncthing-config")
            
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
            # Incremental snapshots - skip syncthing-config for clones
            for name in ["data", "media", "export"]:
                tarfile_path = next(tmp.glob(f"{name}.tar*"), None)
                if tarfile_path:
                    extract_tar(tarfile_path, DATA_ROOT)
            # Only restore syncthing-config for same-instance restores
            if not skip_config:
                syncthing_tarfile = next(tmp.glob("syncthing-config.tar*"), None)
                if syncthing_tarfile:
                    extract_tar(syncthing_tarfile, STACK_DIR)
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
    
    # Restart Syncthing if it was enabled (syncthing-config was restored)
    # Skip for clones (skip_config=True without force_syncthing_restore)
    # But DO restart for system restore (force_syncthing_restore=True)
    syncthing_config_dir = STACK_DIR / "syncthing-config"
    if (not skip_config or force_syncthing_restore) and syncthing_config_dir.exists():
        try:
            from lib.installer.consume import (
                load_consume_config, start_syncthing_container, stop_syncthing_container,
                SyncthingConfig, fix_syncthing_gui_address, get_next_available_port,
                save_consume_config
            )
            from lib.installer.tailscale import get_ip as get_tailscale_ip
            
            consume_config = load_consume_config(ENV_FILE)
            if consume_config.syncthing.enabled:
                say("Restarting Syncthing container...")
                
                # Stop the old Syncthing container first (it's not part of docker-compose)
                stop_syncthing_container(INSTANCE_NAME)
                
                consume_path = DATA_ROOT / "consume"
                consume_path.mkdir(parents=True, exist_ok=True)
                
                # Get ports from config
                gui_port = consume_config.syncthing.gui_port
                sync_port = consume_config.syncthing.sync_port
                ports_changed = False
                
                # Get Tailscale IP (GUI binds to Tailscale if available, localhost otherwise)
                tailscale_ip = get_tailscale_ip()
                
                # Check for port conflicts with OTHER instances
                # (Our own container is stopped, so we're checking if another instance took our ports)
                def is_port_in_use(port: int, hosts: list[str]) -> bool:
                    """Check if port is in use on any of the given hosts."""
                    import socket
                    for host in hosts:
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                                s.settimeout(0.5)
                                s.connect((host, port))
                                return True  # Something is listening
                        except (socket.error, socket.timeout):
                            continue
                    return False
                
                # GUI port can be bound to Tailscale IP or localhost - check both
                gui_hosts = ["127.0.0.1"]
                if tailscale_ip:
                    gui_hosts.append(tailscale_ip)
                if is_port_in_use(gui_port, gui_hosts):
                    old_port = gui_port
                    gui_port = get_next_available_port(8384)
                    warn(f"Syncthing GUI port {old_port} in use by another instance, using {gui_port}")
                    ports_changed = True
                
                # Sync port binds to 0.0.0.0, check on localhost
                if is_port_in_use(sync_port, ["127.0.0.1"]):
                    old_port = sync_port
                    sync_port = get_next_available_port(22000)
                    warn(f"Syncthing sync port {old_port} in use by another instance, using {sync_port}")
                    ports_changed = True
                
                # Fix the GUI address in restored config.xml BEFORE starting container
                fix_syncthing_gui_address(syncthing_config_dir, gui_port)
                
                # Create config from env settings (with potentially updated ports)
                config = SyncthingConfig(
                    enabled=True,
                    folder_id=consume_config.syncthing.folder_id,
                    folder_label=consume_config.syncthing.folder_label,
                    device_id=consume_config.syncthing.device_id,
                    api_key=consume_config.syncthing.api_key,
                    sync_port=sync_port,
                    gui_port=gui_port,
                )
                
                # Save updated ports to .env if they changed
                if ports_changed:
                    consume_config.syncthing = config
                    save_consume_config(consume_config, ENV_FILE)
                
                if start_syncthing_container(INSTANCE_NAME, config, consume_path, syncthing_config_dir):
                    ok("Syncthing container restarted")
                else:
                    warn("Failed to restart Syncthing - start manually from Consume menu")
        except Exception as e:
            warn(f"Could not restart Syncthing: {e}")


def _refresh_globals_from_env():
    """Re-read global configuration from environment variables.
    
    This must be called when using restore_snapshot() programmatically
    because Python caches module globals at import time. Without this,
    restoring multiple instances in sequence would use the first instance's
    paths for all subsequent instances.
    """
    global INSTANCE_NAME, PROJECT_NAME, STACK_DIR, DATA_ROOT, COMPOSE_FILE
    global RCLONE_REMOTE_NAME, RCLONE_REMOTE_PATH, POSTGRES_DB, POSTGRES_USER, REMOTE, ENV_FILE
    
    ENV_FILE = Path(os.environ.get("ENV_FILE", "/home/docker/paperless-setup/.env"))
    
    # Load env file if it exists to get additional settings
    if ENV_FILE.exists():
        load_env_to_environ(ENV_FILE)
    
    INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
    PROJECT_NAME = f"paperless-{INSTANCE_NAME}"
    STACK_DIR = Path(os.environ.get("STACK_DIR", f"/home/docker/{INSTANCE_NAME}-setup"))
    DATA_ROOT = Path(os.environ.get("DATA_ROOT", f"/home/docker/{INSTANCE_NAME}"))
    COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))
    RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    RCLONE_REMOTE_PATH = os.environ.get("RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}")
    POSTGRES_DB = os.environ.get("POSTGRES_DB", "paperless")
    POSTGRES_USER = os.environ.get("POSTGRES_USER", "paperless")
    REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"


def restore_snapshot(snapshot_name: str) -> None:
    """Entry point for restoring a specific snapshot.
    
    Note: This refreshes all globals from environment before running,
    making it safe to call multiple times for different instances.
    """
    # Re-read globals from environment (critical for multi-instance restore!)
    _refresh_globals_from_env()
    
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
