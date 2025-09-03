#!/usr/bin/env python3
"""Bulletproof helper CLI implemented in Python."""
import argparse
import os
import subprocess
import sys
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
CRON_TIME = os.environ.get("CRON_TIME", "30 3 * * *")


def dc(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


def fetch_snapshots() -> list[tuple[str, str, str]]:
    try:
        res = subprocess.run(
            ["rclone", "lsd", REMOTE], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        warn("rclone not installed")
        return []
    snaps: list[tuple[str, str, str]] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        name = parts[-1]
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
                    k = k.strip()
                    v = v.strip()
                    if k == "mode":
                        mode = v
                    elif k == "parent":
                        parent = v
        snaps.append((name, mode, parent))
    return sorted(snaps, key=lambda x: x[0])


def cmd_backup(args: argparse.Namespace) -> None:
    script = STACK_DIR / "backup.py"
    if not script.exists():
        die(f"Backup script not found at {script}")
    run = [str(script)]
    if args.mode:
        run.append(args.mode)
    subprocess.run(run, check=True)


def cmd_snapshots(args: argparse.Namespace) -> None:
    snaps = fetch_snapshots()
    if not snaps:
        warn("No snapshots found")
        return
    if args.snapshot:
        subprocess.run(
            ["rclone", "cat", f"{REMOTE}/{args.snapshot}/manifest.yaml"], check=True
        )
        return
    print(f"{'NAME':<32} {'MODE':<8} PARENT")
    for name, mode, parent in snaps:
        parent_disp = parent if mode == "incr" else "-"
        print(f"{name:<32} {mode:<8} {parent_disp}")


def cmd_restore(args: argparse.Namespace) -> None:
    script = STACK_DIR / "restore.py"
    if not script.exists():
        die(f"Restore script not found at {script}")
    run = [str(script)]
    snap = args.snapshot
    if not snap:
        snaps = fetch_snapshots()
        if snaps:
            print("Available snapshots:")
            for name, mode, parent in snaps:
                detail = f"{mode}" if mode != "incr" else f"{mode}<-{parent}"
                print(f"- {name} ({detail})")
    else:
        run.append(snap)
    subprocess.run(run, check=True)


def cmd_upgrade(_: argparse.Namespace) -> None:
    say("Running backup before upgrade")
    cmd_backup(argparse.Namespace(mode="full"))
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


def install_cron(cron: str) -> None:
    line = f"{cron} root {STACK_DIR}/backup.py >> {STACK_DIR}/backup.log 2>&1"
    crontab = Path("/etc/crontab")
    lines = [
        l
        for l in (crontab.read_text().splitlines() if crontab.exists() else [])
        if f"{STACK_DIR}/backup.py" not in l
    ]
    lines.append(line)
    crontab.write_text("\n".join(lines) + "\n")
    if ENV_FILE.exists():
        env_lines = [
            l for l in ENV_FILE.read_text().splitlines() if not l.startswith("CRON_TIME=")
        ]
        env_lines.append(f"CRON_TIME={cron}")
        ENV_FILE.write_text("\n".join(env_lines) + "\n")
    subprocess.run(["systemctl", "restart", "cron"], check=False)
    ok("Backup schedule updated")


def cmd_schedule(args: argparse.Namespace) -> None:
    cron = args.cron or input(f"Cron time (current {CRON_TIME}): ").strip() or CRON_TIME
    install_cron(cron)


def menu() -> None:
    """Interactive menu for easier use."""
    while True:
        snaps = fetch_snapshots()
        latest = snaps[-1][0] if snaps else "none"
        print(f"{COLOR_BLUE}=== Bulletproof ({INSTANCE_NAME}) ==={COLOR_OFF}")
        print(f"Remote: {REMOTE}")
        print(f"Snapshots: {len(snaps)} (latest: {latest})")
        print(f"Schedule: {CRON_TIME}\n")
        print("1) Backup")
        print("2) Snapshots")
        print("3) Restore snapshot")
        print("4) Upgrade")
        print("5) Status")
        print("6) Logs")
        print("7) Doctor")
        print("8) Backup schedule")
        print("9) Quit")
        choice = input("Choose [1-9]: ").strip()
        if choice == "1":
            mode_in = input("Full or Incremental? [incr]: ").strip().lower()
            if mode_in.startswith("f"):
                mode = "full"
            else:
                mode = "incr"
            cmd_backup(argparse.Namespace(mode=mode))
        elif choice == "2":
            cmd_snapshots(argparse.Namespace(snapshot=None))
            snap = input("Show manifest for snapshot (blank=back): ").strip()
            if snap:
                cmd_snapshots(argparse.Namespace(snapshot=snap))
        elif choice == "3":
            snaps = fetch_snapshots()
            for idx, (name, mode, parent) in enumerate(snaps, 1):
                detail = f"{mode}" if mode != "incr" else f"{mode}<-{parent}"
                print(f"{idx}) {name} ({detail})")
            choice_snap = input("Snapshot number or name (blank=latest): ").strip()
            if choice_snap.isdigit():
                idx = int(choice_snap)
                snap = snaps[idx - 1][0] if 1 <= idx <= len(snaps) else None
            else:
                snap = choice_snap or None
            cmd_restore(argparse.Namespace(snapshot=snap))
        elif choice == "4":
            cmd_upgrade(argparse.Namespace())
        elif choice == "5":
            cmd_status(argparse.Namespace())
        elif choice == "6":
            svc = input("Service (blank=all): ").strip() or None
            cmd_logs(argparse.Namespace(service=svc))
        elif choice == "7":
            cmd_doctor(argparse.Namespace())
        elif choice == "8":
            cmd_schedule(argparse.Namespace(cron=None))
        elif choice == "9":
            break
        else:
            print("Invalid choice")


parser = argparse.ArgumentParser(description="Paperless-ngx bulletproof helper")
sub = parser.add_subparsers(dest="command")

p = sub.add_parser("backup", help="run backup script")
p.add_argument("mode", nargs="?", choices=["full", "incr"], help="full|incr")
p.set_defaults(func=cmd_backup)

p = sub.add_parser("snapshots", help="list snapshots or show manifest")
p.add_argument("snapshot", nargs="?", help="snapshot to show manifest")
p.set_defaults(func=cmd_snapshots)

p = sub.add_parser("restore", help="restore snapshot")
p.add_argument("snapshot", nargs="?")
p.set_defaults(func=cmd_restore)

p = sub.add_parser("upgrade", help="backup then pull images and up -d")
p.set_defaults(func=cmd_upgrade)

p = sub.add_parser("status", help="docker status")
p.set_defaults(func=cmd_status)

p = sub.add_parser("logs", help="show logs")
p.add_argument("service", nargs="?")
p.set_defaults(func=cmd_logs)

p = sub.add_parser("doctor", help="basic checks")
p.set_defaults(func=cmd_doctor)

p = sub.add_parser("schedule", help="configure backup cron schedule")
p.add_argument("cron", nargs="?")
p.set_defaults(func=cmd_schedule)


if __name__ == "__main__":
    args = parser.parse_args()
    if not hasattr(args, "func"):
        if sys.stdin.isatty():
            menu()
        else:
            parser.print_help()
    else:
        args.func(args)
