#!/usr/bin/env python3
"""Restore Paperless-ngx data from an rclone snapshot."""
import os
import sys
import tarfile
import tempfile
import subprocess
import time
from pathlib import Path


def load_env(path: Path) -> None:
    """Load environment variables from a .env file if present."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)


def run_stack_tests(compose_file: Path, env_file: Path) -> bool:
    ok = True
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "ps",
            ],
            check=True,
        )
    except Exception:
        ok = False
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "exec",
                "-T",
                "paperless",
                "python",
                "manage.py",
                "check",
            ],
            check=True,
        )
    except Exception:
        ok = False
    return ok

COLOR_BLUE = "\033[1;34m"
COLOR_GREEN = "\033[1;32m"
COLOR_YELLOW = "\033[1;33m"
COLOR_RED = "\033[1;31m"
COLOR_OFF = "\033[0m"


def say(msg: str) -> None:
    print(f"{COLOR_BLUE}[*]{COLOR_OFF} {msg}")


def ok(msg: str) -> None:
    print(f"{COLOR_GREEN}[ok]{COLOR_OFF} {msg}")


def warn(msg: str) -> None:
    print(f"{COLOR_YELLOW}[!]{COLOR_OFF} {msg}")


def die(msg: str) -> None:
    print(f"{COLOR_RED}[x]{COLOR_OFF} {msg}")
    sys.exit(1)


ENV_FILE = Path(os.environ.get("ENV_FILE", "/home/docker/paperless-setup/.env"))
load_env(ENV_FILE)
if not ENV_FILE.exists():
    warn(f"No .env at {ENV_FILE}")

INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
STACK_DIR = Path(os.environ.get("STACK_DIR", "/home/docker/paperless-setup"))
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/home/docker/paperless"))
COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))
RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
RCLONE_REMOTE_PATH = os.environ.get("RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "paperless")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "paperless")

REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"


def list_snapshots() -> list[str]:
    res = subprocess.run(
        ["rclone", "lsd", REMOTE], capture_output=True, text=True, check=False
    )
    snaps = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if parts:
            snaps.append(parts[-1].rstrip("/"))
    return sorted(snaps)


def extract_tar(tar_path: Path, dest: Path) -> None:
    with tarfile.open(tar_path, "r:*") as tar:
        tar.extractall(path=dest)


def restore_db(dump: Path) -> None:
    say("Restoring database…")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "db"],
        check=True,
    )
    time.sleep(5)
    # Clear existing schema to avoid duplicate key errors when restoring over an
    # existing database volume.
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            POSTGRES_USER,
            "-d",
            POSTGRES_DB,
            "-c",
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
        ],
        check=False,
    )
    if dump.suffix == ".gz":
        proc = subprocess.Popen(["gunzip", "-c", str(dump)], stdout=subprocess.PIPE)
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                POSTGRES_USER,
                "-d",
                POSTGRES_DB,
            ],
            stdin=proc.stdout,
            check=False,
        )
    else:
        with open(dump, "rb") as fh:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE_FILE),
                    "exec",
                    "-T",
                    "db",
                    "psql",
                    "-U",
                    POSTGRES_USER,
                    "-d",
                    POSTGRES_DB,
                ],
                stdin=fh,
                check=False,
            )


def main() -> None:
    snaps = list_snapshots()
    if not snaps:
        die(f"No snapshots found in {REMOTE}")
    snap = sys.argv[1] if len(sys.argv) > 1 else snaps[-1]
    if snap not in snaps:
        die(f"Snapshot {snap} not found")
    say(f"Restoring snapshot {snap}")
    tmp = Path(tempfile.mkdtemp(prefix="paperless-restore."))
    subprocess.run(["rclone", "sync", f"{REMOTE}/{snap}", str(tmp)], check=True)

    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], check=False)

    if (tmp / ".env").exists():
        (STACK_DIR / ".env").write_text((tmp / ".env").read_text())
        ok("Restored .env")

    for name in ["data", "media", "export"]:
        dest = DATA_ROOT / name
        if dest.exists():
            subprocess.run(["rm", "-rf", str(dest)], check=False)
        tarfile_path = next(tmp.glob(f"{name}.tar*"), None)
        if tarfile_path:
            extract_tar(tarfile_path, DATA_ROOT)

    dump = next(tmp.glob("postgres.sql*"), None)
    if dump:
        restore_db(dump)

    compose_snap = tmp / "compose.snapshot.yml"
    if compose_snap.exists():
        compose_snap.replace(COMPOSE_FILE)

    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], check=False)
    if run_stack_tests(COMPOSE_FILE, ENV_FILE):
        ok("Restore complete")
    else:
        warn("Restore complete, but self-test failed")


if __name__ == "__main__":
    try:
        main()
    finally:
        if 'tmp' in locals() and Path(tmp).exists():
            subprocess.run(["rm", "-rf", str(tmp)])
