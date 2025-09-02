#!/usr/bin/env python3
"""Bulletproof helper CLI implemented in Python."""
import argparse
import os
import subprocess
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
    raise SystemExit(1)


STACK_DIR = Path(os.environ.get("STACK_DIR", "/home/docker/paperless-setup"))
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/home/docker/paperless"))
ENV_FILE = Path(os.environ.get("ENV_FILE", STACK_DIR / ".env"))
COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))

load_env(ENV_FILE)

INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
RCLONE_REMOTE_PATH = os.environ.get(
    "RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}"
)
REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"


def dc(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


def cmd_backup(args: argparse.Namespace) -> None:
    script = STACK_DIR / "backup.py"
    if not script.exists():
        die(f"Backup script not found at {script}")
    run = [str(script)]
    if args.retention:
        run.append(args.retention)
    subprocess.run(run, check=True)


def cmd_list(_: argparse.Namespace) -> None:
    subprocess.run(["rclone", "lsd", REMOTE], check=True)


def cmd_restore(args: argparse.Namespace) -> None:
    script = STACK_DIR / "restore.py"
    if not script.exists():
        die(f"Restore script not found at {script}")
    run = [str(script)]
    if args.snapshot:
        run.append(args.snapshot)
    subprocess.run(run, check=True)


def cmd_manifest(args: argparse.Namespace) -> None:
    snap = args.snapshot
    if not snap:
        res = subprocess.run(
            ["rclone", "lsd", REMOTE], capture_output=True, text=True, check=True
        )
        snaps = [line.split()[-1] for line in res.stdout.strip().splitlines() if line]
        if not snaps:
            die("No snapshots found")
        snap = snaps[-1]
    subprocess.run(
        ["rclone", "cat", f"{REMOTE}/{snap}/manifest.yaml"], check=True
    )


def cmd_upgrade(_: argparse.Namespace) -> None:
    say("Running backup before upgrade")
    cmd_backup(argparse.Namespace(retention="auto"))
    say("Pulling images")
    subprocess.run(dc("pull"), check=False)
    say("Recreating containers")
    subprocess.run(dc("up", "-d"), check=False)
    ok("Upgrade completed")


def cmd_status(_: argparse.Namespace) -> None:
    subprocess.run(dc("ps"), check=False)
    print()
    subprocess.run(
        ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
        check=False,
    )


def cmd_logs(args: argparse.Namespace) -> None:
    if args.service:
        subprocess.run(dc("logs", "--tail", "200", "--timestamps", args.service), check=False)
    else:
        subprocess.run(dc("logs", "--tail", "200", "--timestamps"), check=False)


def cmd_doctor(_: argparse.Namespace) -> None:
    say("Doctor: quick checks")
    print(f"- STACK_DIR: {STACK_DIR}")
    print(
        f"- COMPOSE_FILE: {COMPOSE_FILE} {'[ok]' if COMPOSE_FILE.exists() else '[missing]'}"
    )
    print(
        f"- ENV_FILE: {ENV_FILE} {'[ok]' if ENV_FILE.exists() else '[missing]'}"
    )
    subprocess.run(["rclone", "lsd", f"{RCLONE_REMOTE_NAME}:"], check=False)
    subprocess.run(["docker", "info"], check=False)


parser = argparse.ArgumentParser(description="Paperless-ngx bulletproof helper")
sub = parser.add_subparsers(dest="command")

p = sub.add_parser("backup", help="run backup script")
p.add_argument("retention", nargs="?", help="daily|weekly|monthly|auto")
p.set_defaults(func=cmd_backup)

p = sub.add_parser("list", help="list snapshots")
p.set_defaults(func=cmd_list)

p = sub.add_parser("restore", help="restore snapshot")
p.add_argument("snapshot", nargs="?")
p.set_defaults(func=cmd_restore)

p = sub.add_parser("manifest", help="show snapshot manifest")
p.add_argument("snapshot", nargs="?")
p.set_defaults(func=cmd_manifest)

p = sub.add_parser("upgrade", help="backup then pull images and up -d")
p.set_defaults(func=cmd_upgrade)

p = sub.add_parser("status", help="docker status")
p.set_defaults(func=cmd_status)

p = sub.add_parser("logs", help="show logs")
p.add_argument("service", nargs="?")
p.set_defaults(func=cmd_logs)

p = sub.add_parser("doctor", help="basic checks")
p.set_defaults(func=cmd_doctor)


if __name__ == "__main__":
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
    else:
        args.func(args)
