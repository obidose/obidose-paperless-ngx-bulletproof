#!/usr/bin/env python3
"""Snapshot Paperless-ngx data and upload to an rclone remote."""
import os
import sys
import tarfile
import tempfile
import subprocess
import time
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from utils.env import load_env

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
    warn(f"No .env at {ENV_FILE} — falling back to defaults.")

INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
STACK_DIR = Path(os.environ.get("STACK_DIR", "/home/docker/paperless-setup"))
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/home/docker/paperless"))
DIR_EXPORT = DATA_ROOT / "export"
DIR_MEDIA = DATA_ROOT / "media"
DIR_DATA = DATA_ROOT / "data"
COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))
RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
RCLONE_REMOTE_PATH = os.environ.get("RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "paperless")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "paperless")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))

REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"


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


def tar_dir(src: Path, name: str, work: Path) -> None:
    if not src.exists():
        warn(f"Skip {name}: directory not found at {src}")
        return
    say(f"Archiving {name}…")
    with tarfile.open(work / f"{name}.tar.gz", "w:gz") as tar:
        tar.add(src, arcname=name)


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


def main() -> None:
    retention_class = sys.argv[1] if len(sys.argv) > 1 else "auto"
    ensure_remote_path(REMOTE)
    snap = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    work = Path(tempfile.mkdtemp(prefix="paperless-backup."))
    say(f"Creating snapshot {snap}")

    dump_db(work)
    tar_dir(DIR_MEDIA, "media", work)
    tar_dir(DIR_DATA, "data", work)
    tar_dir(DIR_EXPORT, "export", work)

    if ENV_FILE.exists():
        (work / ".env").write_text(ENV_FILE.read_text())
    else:
        warn(f"No .env found at {ENV_FILE}")

    if COMPOSE_FILE.exists():
        shutil_path = work / "compose.snapshot.yml"
        shutil_path.write_text(COMPOSE_FILE.read_text())

    (work / "manifest.yaml").write_text(
        f"mode: full\nretention: {retention_class}\ncreated: {datetime.utcnow().isoformat()}\n"
    )

    passed = verify_archives(work) and test_db_restore(work)
    status = "status.ok" if passed else "status.fail"
    (work / status).write_text(datetime.utcnow().isoformat() + "\n")
    if passed:
        ok("Integrity checks passed")
    else:
        warn("Integrity checks failed")

    dest = f"{REMOTE}/{snap}"
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

    if RETENTION_DAYS > 0:
        subprocess.run(
            [
                "rclone",
                "delete",
                REMOTE,
                "--min-age",
                f"{RETENTION_DAYS}d",
                "--fast-list",
            ],
            check=False,
        )
        subprocess.run(["rclone", "rmdirs", REMOTE, "--leave-root"], check=False)

    ok("Backup completed")


if __name__ == "__main__":
    import shutil

    try:
        main()
    finally:
        if 'work' in locals() and Path(work).exists():
            shutil.rmtree(work)
