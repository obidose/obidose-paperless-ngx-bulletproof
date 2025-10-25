#!/usr/bin/env python3
"""Snapshot Paperless-ngx data and upload to an rclone remote."""
import os
import sys
import tempfile
import subprocess
import time
from pathlib import Path
from datetime import datetime


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


def fetch_snapshots() -> list[tuple[str, str]]:
    res = subprocess.run(
        ["rclone", "lsd", REMOTE], capture_output=True, text=True, check=False
    )
    snaps: list[tuple[str, str]] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        name = parts[-1].rstrip("/")
        mode = "?"
        cat = subprocess.run(
            ["rclone", "cat", f"{REMOTE}/{name}/manifest.yaml"],
            capture_output=True,
            text=True,
            check=False,
        )
        if cat.returncode == 0:
            for mline in cat.stdout.splitlines():
                if mline.startswith("mode:"):
                    mode = mline.split(":", 1)[1].strip()
                    break
        snaps.append((name, mode))
    return sorted(snaps, key=lambda x: x[0])


def prune_snapshots(keep_fulls: int, keep_incs: int) -> None:
    if keep_fulls <= 0 and keep_incs <= 0:
        return
    snaps = fetch_snapshots()
    groups: list[list[str]] = []
    cur: list[str] = []
    for name, mode in snaps:
        if mode == "full":
            if cur:
                groups.append(cur)
            cur = [name]
        elif mode == "incr":
            if cur:
                cur.append(name)
            else:
                cur = [name]
    if cur:
        groups.append(cur)

    to_delete: list[str] = []
    if keep_fulls > 0 and len(groups) > keep_fulls:
        for grp in groups[:-keep_fulls]:
            to_delete.extend(grp)
        groups = groups[-keep_fulls:]

    if keep_incs >= 0:
        for grp in groups:
            incs = grp[1:]
            if keep_incs == 0:
                to_delete.extend(incs)
            elif len(incs) > keep_incs:
                to_delete.extend(incs[:-keep_incs])

    for snap in to_delete:
        subprocess.run(["rclone", "purge", f"{REMOTE}/{snap}"], check=False)
    if to_delete:
        subprocess.run(["rclone", "rmdirs", REMOTE, "--leave-root"], check=False)


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

INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
STACK_DIR = Path(os.environ.get("STACK_DIR", "/home/docker/paperless-setup"))
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/home/docker/paperless"))
DIR_EXPORT = DATA_ROOT / "export"
DIR_MEDIA = DATA_ROOT / "media"
DIR_DATA = DATA_ROOT / "data"
COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))
RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
RCLONE_REMOTE_PATH = os.environ.get("RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}")
RCLONE_ARCHIVE_PATH = os.environ.get(
    "RCLONE_ARCHIVE_PATH", f"{RCLONE_REMOTE_PATH}/archive"
)
POSTGRES_DB = os.environ.get("POSTGRES_DB", "paperless")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "paperless")
KEEP_FULLS = int(os.environ.get("KEEP_FULLS", "3"))
KEEP_INCS = int(os.environ.get("KEEP_INCS", "7"))

REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"
ARCHIVE_REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_ARCHIVE_PATH}"


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
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode not in {"full", "incr", "archive"}:
        die("Usage: backup.py [full|incr|archive]")
    ensure_remote_path(ARCHIVE_REMOTE if mode == "archive" else REMOTE)
    snaps = list_snapshots()
    parent = snaps[-1] if snaps else ""
    if mode == "incr" and not snaps:
        mode = "full"
    snap = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{mode}"
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

    if ENV_FILE.exists():
        (work / ".env").write_text(ENV_FILE.read_text())
    else:
        warn(f"No .env found at {ENV_FILE}")

    if COMPOSE_FILE.exists():
        shutil_path = work / "compose.snapshot.yml"
        shutil_path.write_text(COMPOSE_FILE.read_text())

    manifest_lines = [f"mode: {mode}", f"created: {datetime.utcnow().isoformat()}"]
    if mode == "incr" and parent:
        manifest_lines.append(f"parent: {parent}")
    (work / "manifest.yaml").write_text("\n".join(manifest_lines) + "\n")

    passed = verify_archives(work) and test_db_restore(work)
    status = "status.ok" if passed else "status.fail"
    (work / status).write_text(datetime.utcnow().isoformat() + "\n")
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

    if mode != "archive":
        prune_snapshots(KEEP_FULLS, KEEP_INCS)

    ok("Backup completed")


if __name__ == "__main__":
    import shutil

    try:
        main()
    finally:
        if 'work' in locals() and Path(work).exists():
            shutil.rmtree(work)
