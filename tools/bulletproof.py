#!/usr/bin/env python3
"""Bulletproof helper CLI implemented in Python."""
import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
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


STACK_DIR: Path | None = None
DATA_ROOT: Path | None = None
ENV_FILE: Path | None = None
COMPOSE_FILE: Path | None = None

INSTANCE_NAME = ""
RCLONE_REMOTE_NAME = ""
RCLONE_REMOTE_PATH = ""
REMOTE = ""
CRON_FULL_TIME = ""
CRON_INCR_TIME = ""
CRON_ARCHIVE_TIME = ""


def init_from_env() -> None:
    global INSTANCE_NAME, DATA_ROOT, ENV_FILE, COMPOSE_FILE
    global RCLONE_REMOTE_NAME, RCLONE_REMOTE_PATH, REMOTE
    global CRON_FULL_TIME, CRON_INCR_TIME, CRON_ARCHIVE_TIME

    INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "paperless")
    DATA_ROOT = Path(os.environ.get("DATA_ROOT", f"/home/docker/{INSTANCE_NAME}"))
    ENV_FILE = Path(os.environ.get("ENV_FILE", STACK_DIR / ".env"))
    COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", STACK_DIR / "docker-compose.yml"))
    RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    RCLONE_REMOTE_PATH = os.environ.get(
        "RCLONE_REMOTE_PATH", f"backups/paperless/{INSTANCE_NAME}"
    )
    REMOTE = f"{RCLONE_REMOTE_NAME}:{RCLONE_REMOTE_PATH}"
    CRON_FULL_TIME = os.environ.get("CRON_FULL_TIME", "30 3 * * 0")
    CRON_INCR_TIME = os.environ.get("CRON_INCR_TIME", "0 0 * * *")
    CRON_ARCHIVE_TIME = os.environ.get("CRON_ARCHIVE_TIME", "")


if "STACK_DIR" in os.environ:
    STACK_DIR = Path(os.environ["STACK_DIR"])
elif Path(".env").exists():
    STACK_DIR = Path.cwd()
else:
    STACK_DIR = None

if STACK_DIR:
    ENV_FILE = Path(os.environ.get("ENV_FILE", STACK_DIR / ".env"))
    load_env(ENV_FILE)
    init_from_env()

BASE_DIR = Path(os.environ.get("BP_BASE_DIR", "/home/docker"))
INSTANCE_SUFFIX = os.environ.get("BP_INSTANCE_SUFFIX", "-setup")
BRANCH = os.environ.get("BP_BRANCH", "main")


def _cron_desc(expr: str) -> str:
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, mon, dow = parts
    try:
        h_i, m_i = int(hour), int(minute)
    except ValueError:
        return expr
    time = f"{h_i:02d}:{m_i:02d}"
    if dom == mon == "*" and dow == "*":
        return f"every day at {time}"
    if dom == "*" and mon == "*" and dow != "*":
        names = [
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ]
        try:
            dow_name = names[int(dow)]
        except Exception:
            dow_name = dow
        return f"every {dow_name} at {time}"
    if dom != "*" and mon == "*" and dow == "*":
        return f"day {int(dom)} every month at {time}"
    return expr


@dataclass
class Instance:
    name: str
    stack_dir: Path
    data_dir: Path
    env: dict[str, str]

    @property
    def env_file(self) -> Path:
        return self.stack_dir / ".env"

    @property
    def compose_file(self) -> Path:
        return self.stack_dir / "docker-compose.yml"

    def env_for_subprocess(self) -> dict[str, str]:
        e = os.environ.copy()
        e.update(
            {
                "INSTANCE_NAME": self.name,
                "STACK_DIR": str(self.stack_dir),
                "DATA_ROOT": str(self.data_dir),
                "ENV_FILE": str(self.env_file),
                "COMPOSE_FILE": str(self.compose_file),
            }
        )
        e.update(self.env)
        return e

    def status(self) -> str:
        res = subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file), "ps", "--status", "running"],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [l for l in res.stdout.splitlines() if l.strip()]
        return "up" if len(lines) > 1 else "down"

    def schedule(self) -> str:
        full = _cron_desc(self.env.get("CRON_FULL_TIME", "?"))
        incr = _cron_desc(self.env.get("CRON_INCR_TIME", "?"))
        arch = self.env.get("CRON_ARCHIVE_TIME")
        parts = [f"Full: {full}", f"Incr: {incr}"]
        if arch:
            parts.append(f"Archive: {_cron_desc(arch)}")
        return ", ".join(parts)


def parse_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v
    return env


def find_instances() -> list[Instance]:
    insts: list[Instance] = []
    for stack in BASE_DIR.glob(f"*{INSTANCE_SUFFIX}"):
        env_file = stack / ".env"
        compose = stack / "docker-compose.yml"
        if not env_file.exists() or not compose.exists():
            continue
        env = parse_env(env_file)
        name = env.get("INSTANCE_NAME", stack.name.replace(INSTANCE_SUFFIX, ""))
        data_root = Path(env.get("DATA_ROOT", str(BASE_DIR / name)))
        insts.append(Instance(name=name, stack_dir=stack, data_dir=data_root, env=env))
    return sorted(insts, key=lambda i: i.name)


def install_instance(name: str) -> None:
    insts = find_instances()
    if any(i.name == name for i in insts):
        warn(f"Instance '{name}' already exists")
        return
    stack_dir = BASE_DIR / f"{name}{INSTANCE_SUFFIX}"
    data_dir = BASE_DIR / name
    if stack_dir.exists() or data_dir.exists():
        warn(f"Directories for '{name}' already exist")
        if input("Remove and continue? (y/N): ").lower().startswith("y"):
            subprocess.run(["rm", "-rf", str(stack_dir)], check=False)
            subprocess.run(["rm", "-rf", str(data_dir)], check=False)
        else:
            return
    env = os.environ.copy()
    env.update(
        {
            "INSTANCE_NAME": name,
            "STACK_DIR": str(stack_dir),
            "DATA_ROOT": str(data_dir),
            "BP_BRANCH": BRANCH,
        }
    )
    url = (
        "https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/"
        f"{BRANCH}/install.py"
    )
    cmd = f"curl -fsSL {url} | python3 - --branch {BRANCH}"
    say(f"Installing instance '{name}' from branch {BRANCH}")
    subprocess.run(["bash", "-lc", cmd], env=env, check=True)


def backup_instance(inst: Instance, mode: str) -> None:
    script = inst.stack_dir / "backup.py"
    if not script.exists():
        warn(f"No backup script for {inst.name}")
        return
    subprocess.run([str(script), mode], env=inst.env_for_subprocess(), check=False)


def manage_instance(inst: Instance) -> None:
    subprocess.run([str(Path(__file__)), "--instance", inst.name])


def delete_instance(inst: Instance) -> None:
    if input(f"Delete instance '{inst.name}'? (y/N): ").lower().startswith("y"):
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(inst.compose_file),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            env=inst.env_for_subprocess(),
            check=False,
        )
        try:
            subprocess.run(["docker", "network", "rm", "paperless_net"], check=False)
        except Exception:
            pass
        subprocess.run(["rm", "-rf", str(inst.stack_dir)], check=False)
        subprocess.run(["rm", "-rf", str(inst.data_dir)], check=False)
        ok(f"Deleted {inst.name}")


def down_instance(inst: Instance) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(inst.compose_file), "down"],
        env=inst.env_for_subprocess(),
        check=False,
    )


def up_instance(inst: Instance) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(inst.compose_file), "up", "-d"],
        env=inst.env_for_subprocess(),
        check=False,
    )


def rename_instance(inst: Instance, new: str) -> None:
    if new == inst.name:
        warn("New name is the same as the current name")
        return
    if any(i.name == new for i in find_instances()):
        warn(f"Instance '{new}' already exists")
        return
    new_stack = BASE_DIR / f"{new}{INSTANCE_SUFFIX}"
    new_data = BASE_DIR / new
    if new_stack.exists() or new_data.exists():
        warn(f"Directories for '{new}' already exist")
        return
    was_up = inst.status() == "up"
    if was_up:
        down_instance(inst)

    inst.stack_dir.rename(new_stack)
    inst.data_dir.rename(new_data)
    env = inst.env
    env["INSTANCE_NAME"] = new
    env["STACK_DIR"] = str(new_stack)
    env["DATA_ROOT"] = str(new_data)
    if "RCLONE_REMOTE_PATH" in env:
        env["RCLONE_REMOTE_PATH"] = f"backups/paperless/{new}"
    lines = [f"{k}={v}" for k, v in env.items()]
    (new_stack / ".env").write_text("\n".join(lines) + "\n")
    ok(f"Renamed to {new}")
    if was_up:
        up_instance(
            Instance(new, new_stack, new_data, env)
        )


def multi_main() -> None:
    while True:
        insts = find_instances()
        if not insts:
            name = (
                input("No instances found. Name for new instance [paperless]: ")
                .strip()
                or "paperless"
            )
            install_instance(name)
            continue
        print()
        print(f"{COLOR_BLUE}=== Paperless-ngx Instances ==={COLOR_OFF}")
        print(f"{'#':>2} {'NAME':<20} {'STAT':<4} SCHEDULE")
        for idx, inst in enumerate(insts, 1):
            status = inst.status()
            color = COLOR_GREEN if status == "up" else COLOR_RED
            print(
                f"{idx:>2} {inst.name:<20} {color}{status:<4}{COLOR_OFF} {inst.schedule()}"
            )

        print()
        print("Actions:")
        print(" 1) Manage instance")
        print(" 2) Backup instance")
        print(" 3) Backup all")
        print(" 4) Add instance")
        print(" 5) Rename instance")
        print(" 6) Delete instance")
        print(" 7) Start instance")
        print(" 8) Stop instance")
        print(" 9) Start all")
        print("10) Stop all")
        print(" 0) Quit")

        choice = input("Select action: ").strip()
        if choice == "4":
            name = input("New instance name: ").strip()
            if name:
                install_instance(name)
        elif choice == "6":
            idx = input("Instance number to delete: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                delete_instance(insts[int(idx) - 1])
        elif choice == "5":
            idx = input("Instance number to rename: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                new = input("New name: ").strip()
                if new:
                    rename_instance(insts[int(idx) - 1], new)
        elif choice == "2":
            idx = input("Instance number to backup: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                mode = input("Full or Incremental? [incr]: ").strip().lower()
                mode = "full" if mode.startswith("f") else "incr"
                backup_instance(insts[int(idx) - 1], mode)
        elif choice == "3":
            mode = input("Full or Incremental? [incr]: ").strip().lower()
            mode = "full" if mode.startswith("f") else "incr"
            for inst in insts:
                say(f"Backing up {inst.name}")
                backup_instance(inst, mode)
        elif choice == "1":
            idx = input("Instance number to manage: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                manage_instance(insts[int(idx) - 1])
        elif choice == "7":
            idx = input("Instance number to start: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                up_instance(insts[int(idx) - 1])
        elif choice == "8":
            idx = input("Instance number to stop: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                down_instance(insts[int(idx) - 1])
        elif choice == "9":
            for inst in insts:
                up_instance(inst)
        elif choice == "10":
            for inst in insts:
                down_instance(inst)
        elif choice == "0":
            break
        else:
            warn("Unknown choice")


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

    print(f"{'#':>3} {'NAME':<32} {'MODE':<8} PARENT")
    for idx, (name, mode, parent) in enumerate(snaps, 1):
        parent_disp = parent if mode == "incr" else "-"
        print(f"{idx:>3} {name:<32} {mode:<8} {parent_disp}")

    snap = args.snapshot
    if snap is None and sys.stdin.isatty():
        snap = input("Snapshot number for manifest (blank=exit): ").strip() or None
    if not snap:
        return
    if snap.isdigit() and 1 <= int(snap) <= len(snaps):
        snap = snaps[int(snap) - 1][0]
    subprocess.run(["rclone", "cat", f"{REMOTE}/{snap}/manifest.yaml"], check=True)


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


def install_cron(full: str, incr: str, archive: str) -> None:
    full_line = (
        f"{full} root {STACK_DIR}/backup.py full >> {STACK_DIR}/backup.log 2>&1"
    )
    incr_line = (
        f"{incr} root {STACK_DIR}/backup.py incr >> {STACK_DIR}/backup.log 2>&1"
    )
    archive_line = (
        f"{archive} root {STACK_DIR}/backup.py archive >> {STACK_DIR}/backup.log 2>&1"
        if archive
        else None
    )
    crontab = Path("/etc/crontab")
    lines = [
        l
        for l in (crontab.read_text().splitlines() if crontab.exists() else [])
        if f"{STACK_DIR}/backup.py" not in l
    ]
    lines.extend([full_line, incr_line])
    if archive_line:
        lines.append(archive_line)
    crontab.write_text("\n".join(lines) + "\n")
    if ENV_FILE.exists():
        env_lines = [
            l
            for l in ENV_FILE.read_text().splitlines()
            if not l.startswith("CRON_FULL_TIME=")
            and not l.startswith("CRON_INCR_TIME=")
            and not l.startswith("CRON_ARCHIVE_TIME=")
        ]
        env_lines.append(f"CRON_FULL_TIME={full}")
        env_lines.append(f"CRON_INCR_TIME={incr}")
        env_lines.append(f"CRON_ARCHIVE_TIME={archive}")
        ENV_FILE.write_text("\n".join(env_lines) + "\n")
    subprocess.run(["systemctl", "restart", "cron"], check=False)
    global CRON_FULL_TIME, CRON_INCR_TIME, CRON_ARCHIVE_TIME
    CRON_FULL_TIME = full
    CRON_INCR_TIME = incr
    CRON_ARCHIVE_TIME = archive
    ok("Backup schedule updated")


def _normalize_time(t: str) -> tuple[int, int]:
    """Return (hour, minute) from 'HH:MM' or 'HHMM' input."""
    t = t.strip()
    if ":" in t:
        h, m = t.split(":", 1)
    elif t.isdigit() and len(t) in (3, 4):
        h, m = t[:-2], t[-2:]
    else:
        raise ValueError("Use HH:MM or HHMM")
    h_i, m_i = int(h), int(m)
    if not (0 <= h_i <= 23 and 0 <= m_i <= 59):
        raise ValueError("Hour 0-23 and minute 0-59")
    return h_i, m_i


def _prompt_time(msg: str, default: str) -> tuple[int, int]:
    while True:
        raw = input(f"{msg} [{default}]: ").strip() or default
        try:
            return _normalize_time(raw)
        except ValueError as e:
            print(f"Invalid time: {e}")




def prompt_full_schedule(current: str) -> str:
    freq = input(
        "Full backup frequency (daily/weekly/monthly/cron) [weekly]: "
    ).strip().lower()
    if not freq:
        freq = "weekly"
    if " " in freq:
        return freq
    if freq.startswith("d"):
        h, m = _prompt_time("Time (HH:MM)", "03:30")
        return f"{m} {h} * * *"
    if freq.startswith("w"):
        dow = input("Day of week (0=Sun..6=Sat) [0]: ").strip() or "0"
        h, m = _prompt_time("Time (HH:MM)", "03:30")
        return f"{m} {h} * * {dow}"
    if freq.startswith("m"):
        dom = input("Day of month (1-31) [1]: ").strip() or "1"
        h, m = _prompt_time("Time (HH:MM)", "03:30")
        return f"{m} {h} {dom} * *"
    if freq.startswith("c"):
        return input(f"Cron expression [{current}]: ").strip() or current
    return freq


def prompt_incr_schedule(current: str) -> str:
    freq = input(
        "Incremental backup frequency (hourly/daily/weekly/cron) [daily]: "
    ).strip().lower()
    if not freq:
        freq = "daily"
    if " " in freq:
        return freq
    if freq.startswith("h"):
        n = input("Every how many hours? [1]: ").strip() or "1"
        return f"0 */{int(n)} * * *"
    if freq.startswith("d"):
        h, m = _prompt_time("Time (HH:MM)", "00:00")
        return f"{m} {h} * * *"
    if freq.startswith("w"):
        dow = input("Day of week (0=Sun..6=Sat) [0]: ").strip() or "0"
        h, m = _prompt_time("Time (HH:MM)", "00:00")
        return f"{m} {h} * * {dow}"
    if freq.startswith("c"):
        return input(f"Cron expression [{current}]: ").strip() or current
    return freq


def prompt_archive_schedule(current: str) -> str:
    enable = input("Enable monthly archive backup? (y/N): ").strip().lower()
    if enable.startswith("y"):
        dom = input("Day of month [1]: ").strip() or "1"
        h, m = _prompt_time("Time (HH:MM)", "04:00")
        return f"{m} {h} {dom} * *"
    return ""


def cmd_schedule(args: argparse.Namespace) -> None:
    print("Configure when backups run.")
    full = args.full or prompt_full_schedule(CRON_FULL_TIME)
    incr = args.incr or prompt_incr_schedule(CRON_INCR_TIME)
    if args.archive is not None:
        archive = args.archive
    else:
        archive = prompt_archive_schedule(CRON_ARCHIVE_TIME)
    install_cron(full, incr, archive)


def menu() -> None:
    """Interactive menu for easier use."""
    while True:
        snaps = fetch_snapshots()
        latest = snaps[-1][0] if snaps else "none"
        print(f"{COLOR_BLUE}=== Bulletproof ({INSTANCE_NAME}) ==={COLOR_OFF}")
        print(f"Remote: {REMOTE}")
        print(f"Snapshots: {len(snaps)} (latest: {latest})")
        def desc(full: str, incr: str, arch: str) -> str:
            try:
                m, h, dom, mon, dow = full.split()
                if dom == mon == dow == "*":
                    full_txt = f"daily at {int(h):02d}:{int(m):02d}"
                elif dom == mon == "*" and dow != "*":
                    full_txt = f"weekly {dow} at {int(h):02d}:{int(m):02d}"
                elif dom != "*" and mon == dow == "*":
                    full_txt = f"monthly {dom} at {int(h):02d}:{int(m):02d}"
                else:
                    full_txt = full
            except Exception:
                full_txt = full
            try:
                parts = incr.split()
                if parts[0] == "0" and parts[1].startswith("*/"):
                    hrs = parts[1][2:]
                    incr_txt = f"every {int(hrs)}h"
                elif parts[0].isdigit() and parts[1].isdigit() and parts[2] == parts[3] == parts[4] == "*":
                    incr_txt = f"daily at {int(parts[1]):02d}:{int(parts[0]):02d}"
                elif parts[0].isdigit() and parts[1].isdigit() and parts[2] == parts[3] == "*" and parts[4] != "*":
                    incr_txt = f"weekly {parts[4]} at {int(parts[1]):02d}:{int(parts[0]):02d}"
                else:
                    incr_txt = incr
            except Exception:
                incr_txt = incr
            if arch:
                try:
                    am, ah, adom, amon, adow = arch.split()
                    if adom != "*" and amon == adow == "*":
                        arch_txt = f"monthly {int(adom)} at {int(ah):02d}:{int(am):02d}"
                    else:
                        arch_txt = arch
                except Exception:
                    arch_txt = arch
                return f"full {full_txt}, incr {incr_txt}, archive {arch_txt}"
            return f"full {full_txt}, incr {incr_txt}"

        print(f"Schedule: {desc(CRON_FULL_TIME, CRON_INCR_TIME, CRON_ARCHIVE_TIME)}\n")
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
            mode_in = input("Full, Incremental, or Archive? [incr]: ").strip().lower()
            if mode_in.startswith("f"):
                mode = "full"
            elif mode_in.startswith("a"):
                mode = "archive"
            else:
                mode = "incr"
            cmd_backup(argparse.Namespace(mode=mode))
        elif choice == "2":
            cmd_snapshots(argparse.Namespace(snapshot=None))
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
            cmd_schedule(argparse.Namespace(full=None, incr=None, archive=None))
        elif choice == "9":
            break
        else:
            print("Invalid choice")


parser = argparse.ArgumentParser(description="Paperless-ngx bulletproof helper")
parser.add_argument("--instance", help="instance name to operate on")
sub = parser.add_subparsers(dest="command")

p = sub.add_parser("backup", help="run backup script")
p.add_argument(
    "mode", nargs="?", choices=["full", "incr", "archive"], help="full|incr|archive"
)
p.set_defaults(func=cmd_backup)

p = sub.add_parser("snapshots", help="list snapshots and optionally show a manifest")
p.add_argument(
    "snapshot",
    nargs="?",
    help="snapshot name or number to show manifest",
)
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

p = sub.add_parser("schedule", help="configure backup schedule")
p.add_argument("--full", help="time for daily full backup (HH:MM or cron)")
p.add_argument("--incr", help="incremental frequency (hours or cron)")
p.add_argument("--archive", help="cron for monthly archive or blank to disable")
p.set_defaults(func=cmd_schedule)


if __name__ == "__main__":
    args = parser.parse_args()
    if STACK_DIR is None and not args.instance:
        multi_main()
    else:
        if args.instance and STACK_DIR is None:
            insts = find_instances()
            inst = next((i for i in insts if i.name == args.instance), None)
            if not inst:
                die(f"Instance '{args.instance}' not found")
            os.environ.update(inst.env_for_subprocess())
            STACK_DIR = inst.stack_dir
            load_env(inst.env_file)
            init_from_env()
        if not hasattr(args, "func"):
            if sys.stdin.isatty():
                menu()
            else:
                parser.print_help()
        else:
            args.func(args)
