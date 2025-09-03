#!/usr/bin/env python3
"""Restore Paperless-ngx data from an rclone snapshot."""
import os
import sys
import shutil
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
    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], check=False)
    final_dump: Path | None = None
    first = True
    for snap in chain:
        tmp = Path(tempfile.mkdtemp(prefix="paperless-restore."))
        subprocess.run(["rclone", "sync", f"{REMOTE}/{snap}", str(tmp)], check=True)
        if first:
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
            compose_snap = tmp / "compose.snapshot.yml"
            if compose_snap.exists():
                compose_snap.replace(COMPOSE_FILE)
            first = False
        else:
            for name in ["data", "media", "export"]:
                tarfile_path = next(tmp.glob(f"{name}.tar*"), None)
                if tarfile_path:
                    extract_tar(tarfile_path, DATA_ROOT)
        dump = next(tmp.glob("postgres.sql*"), None)
        if dump:
            final_dump = dump
        shutil.rmtree(tmp)
    if final_dump:
        restore_db(final_dump)
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
