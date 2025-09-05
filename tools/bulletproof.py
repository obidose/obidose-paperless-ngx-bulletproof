#!/usr/bin/env python3
"""Bulletproof helper CLI implemented in Python."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

try:
    TTY = open("/dev/tty", "r+")
    if not sys.stdin.isatty():
        sys.stdin = sys.stdout = sys.stderr = TTY
except OSError:
    TTY = sys.stdin


def _read(prompt: str) -> str:
    print(prompt, end="", flush=True, file=TTY)
    return TTY.readline().strip()



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
        if _read("Remove and continue? (y/N): ").lower().startswith("y"):
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
    if _read(f"Delete instance '{inst.name}'? (y/N): ").lower().startswith("y"):
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


def start_all(insts: list[Instance]) -> None:
    for inst in insts:
        up_instance(inst)


def stop_all(insts: list[Instance]) -> None:
    for inst in insts:
        down_instance(inst)


def delete_all(insts: list[Instance]) -> None:
    if _read("Delete ALL instances? (y/N): ").lower().startswith("y"):
        for inst in insts:
            delete_instance(inst)
        subprocess.run(["docker", "network", "rm", "paperless_net"], check=False)
        ok("All instances removed")


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


def restore_instance(inst: Instance, snap: str | None = None, source: str | None = None) -> None:
    env = inst.env_for_subprocess()
    if source:
        env["RCLONE_REMOTE_PATH"] = f"backups/paperless/{source}"
        env["REMOTE"] = f"{env.get('RCLONE_REMOTE_NAME', RCLONE_REMOTE_NAME)}:{env['RCLONE_REMOTE_PATH']}"
    cmd = [str(Path(__file__)), "--instance", inst.name, "restore"]
    if snap:
        cmd.append(snap)
    subprocess.run(cmd, env=env, check=False)


def multi_main() -> None:
    while True:
        insts = find_instances()
        if not insts:
            print()
            print(f"{COLOR_BLUE}=== Paperless-ngx Instances ==={COLOR_OFF}")
            print("No instances found.")
            print()
            print("Actions:")
            print(" 1) Add instance")
            print(" 2) Explore backups")
            print(" 0) Quit")
            try:
                choice = _read("Select action: ").strip()
            except EOFError:
                print()
                return
            if choice == "1":
                try:
                    name = (
                        _read("New instance name [paperless]: ").strip() or "paperless"
                    )
                except EOFError:
                    return
                install_instance(name)
            elif choice == "2":
                explore_backups()
            elif choice == "0":
                break
            else:
                warn("Unknown choice")
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
        print(" 5) Start all")
        print(" 6) Stop all")
        print(" 7) Delete all")
        print(" 8) Explore backups")
        print(" 0) Quit")

        try:
            choice = _read("Select action: ").strip()
        except EOFError:
            print()
            return
        if choice == "4":
            mode = _read("Add from scratch or backup? (s/b) [s]: ").strip().lower()
            if mode.startswith("b"):
                picked = pick_remote_snapshot()
                if not picked:
                    continue
                source, snap = picked
                default_name = source
                existing = {i.name for i in insts}
                while default_name in existing:
                    default_name += "-copy"
                name = _read(f"New instance name [{default_name}]: ").strip() or default_name
                install_instance(name)
                new_inst = next((i for i in find_instances() if i.name == name), None)
                if new_inst:
                    restore_instance(new_inst, snap, source)
            else:
                name = _read("New instance name: ").strip()
                if name:
                    install_instance(name)
        elif choice == "7":
            delete_all(insts)
        elif choice == "3":
            mode = _read("Full or Incremental? [incr]: ").strip().lower()
            mode = "full" if mode.startswith("f") else "incr"
            for inst in insts:
                say(f"Backing up {inst.name}")
                backup_instance(inst, mode)
        elif choice == "2":
            idx = _read("Instance number to back up: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                mode = _read("Full or Incremental? [incr]: ").strip().lower()
                mode = "full" if mode.startswith("f") else "incr"
                backup_instance(insts[int(idx) - 1], mode)
        elif choice == "5":
            start_all(insts)
        elif choice == "6":
            stop_all(insts)
        elif choice == "8":
            explore_backups()
        elif choice == "1":
            idx = _read("Instance number to manage: ").strip()
            if idx.isdigit() and 1 <= int(idx) <= len(insts):
                manage_instance(insts[int(idx) - 1])
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


def list_remote_instances() -> list[str]:
    """List instance names that have backups on the remote."""
    try:
        res = subprocess.run(
            ["rclone", "lsd", f"{RCLONE_REMOTE_NAME}:backups/paperless"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    names: list[str] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if parts:
            names.append(parts[-1].rstrip("/"))
    return sorted(names)


def fetch_snapshots_for(name: str) -> list[tuple[str, str, str]]:
    """Fetch snapshots for a given remote instance name."""
    remote = f"{RCLONE_REMOTE_NAME}:backups/paperless/{name}"
    res = subprocess.run(
        ["rclone", "lsd", remote], capture_output=True, text=True, check=False
    )
    snaps: list[tuple[str, str, str]] = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        snap_name = parts[-1].rstrip("/")
        mode = parent = "?"
        cat = subprocess.run(
            ["rclone", "cat", f"{remote}/{snap_name}/manifest.yaml"],
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
        snaps.append((snap_name, mode, parent))
    return sorted(snaps, key=lambda x: x[0])


def pick_remote_snapshot() -> tuple[str, str] | None:
    """Interactively choose a remote instance and snapshot."""
    rem_insts = list_remote_instances()
    if not rem_insts:
        warn("No backups found on remote")
        return None
    for i, name in enumerate(rem_insts, 1):
        print(f"{i}) {name}")
    sel = _read("Source instance number or name (blank=cancel): ").strip()
    if not sel:
        return None
    if sel.isdigit() and 1 <= int(sel) <= len(rem_insts):
        source = rem_insts[int(sel) - 1]
    else:
        source = sel
    snaps = fetch_snapshots_for(source)
    if not snaps:
        warn("No snapshots for that instance")
        return None
    for i, (n, m, p) in enumerate(snaps, 1):
        detail = m if m != "incr" else f"{m}<-{p}"
        print(f"{i}) {n} ({detail})")
    sel_snap = _read("Snapshot number or name (blank=latest): ").strip()
    if sel_snap.isdigit() and 1 <= int(sel_snap) <= len(snaps):
        snap = snaps[int(sel_snap) - 1][0]
    else:
        snap = sel_snap or snaps[-1][0]
    return source, snap


def verify_snapshot(source: str, snap: str) -> None:
    """Download a snapshot and run tar integrity checks."""
    remote = f"{RCLONE_REMOTE_NAME}:backups/paperless/{source}/{snap}"
    tmp = Path(tempfile.mkdtemp(prefix="paperless-verify."))
    try:
        say(f"Verifying {source}/{snap}…")
        subprocess.run(["rclone", "sync", remote, str(tmp)], check=True)
        from modules.backup import verify_archives

        if verify_archives(tmp):
            ok("Archives verified")
        else:
            warn("Archive verification failed")
    except subprocess.CalledProcessError:
        warn("Failed to download snapshot for verification")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def explore_backups() -> None:
    rem_insts = list_remote_instances()
    if not rem_insts:
        warn("No backups found on remote")
        return
    for i, name in enumerate(rem_insts, 1):
        print(f"{i}) {name}")
    sel = _read("Instance number to inspect (blank=cancel): ").strip()
    if not sel:
        return
    if sel.isdigit() and 1 <= int(sel) <= len(rem_insts):
        inst = rem_insts[int(sel) - 1]
    else:
        inst = sel
    snaps = fetch_snapshots_for(inst)
    if not snaps:
        warn("No snapshots for that instance")
        return
    print(f"{'#':>3} {'NAME':<32} {'MODE':<8} PARENT")
    for idx, (name, mode, parent) in enumerate(snaps, 1):
        parent_disp = parent if mode == 'incr' else '-'
        print(f"{idx:>3} {name:<32} {mode:<8} {parent_disp}")
    choice = _read("Snapshot number to verify (blank=exit): ").strip()
    if not choice:
        return
    if choice.isdigit() and 1 <= int(choice) <= len(snaps):
        snap = snaps[int(choice) - 1][0]
    else:
        snap = choice
    verify_snapshot(inst, snap)


def run_stack_tests() -> bool:
    ok = True
    try:
        subprocess.run(dc("ps"), check=True)
    except Exception:
        ok = False
    try:
        subprocess.run(
            dc("exec", "-T", "paperless", "python", "manage.py", "check"),
            check=True,
        )
    except Exception:
        ok = False
    return ok


def extract_tar(tar_path: Path, dest: Path) -> None:
    subprocess.run(
        ["tar", "--listed-incremental=/dev/null", "-xpf", str(tar_path), "-C", str(dest)],
        check=True,
    )


def ensure_network() -> None:
    """Ensure the shared docker network exists."""
    try:
        subprocess.run(
            ["docker", "network", "inspect", "paperless_net"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError:
        subprocess.run(["docker", "network", "create", "paperless_net"], check=False)


def restore_db(dump: Path) -> None:
    say("Restoring database…")
    ensure_network()
    subprocess.run(dc("up", "-d", "db"), check=True)
    time.sleep(5)
    subprocess.run(
        dc(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            os.environ.get("POSTGRES_USER", "paperless"),
            "-d",
            os.environ.get("POSTGRES_DB", "paperless"),
            "-c",
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
        ),
        check=False,
    )
    if dump.suffix == ".gz":
        proc = subprocess.Popen(["gunzip", "-c", str(dump)], stdout=subprocess.PIPE)
        subprocess.run(
            dc(
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                os.environ.get("POSTGRES_USER", "paperless"),
                "-d",
                os.environ.get("POSTGRES_DB", "paperless"),
            ),
            stdin=proc.stdout,
            check=False,
        )
    else:
        with open(dump, "rb") as fh:
            subprocess.run(
                dc(
                    "exec",
                    "-T",
                    "db",
                    "psql",
                    "-U",
                    os.environ.get("POSTGRES_USER", "paperless"),
                    "-d",
                    os.environ.get("POSTGRES_DB", "paperless"),
                ),
                stdin=fh,
                check=False,
            )


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
        snap = _read("Snapshot number for manifest (blank=exit): ").strip() or None
    if not snap:
        return
    if snap.isdigit() and 1 <= int(snap) <= len(snaps):
        snap = snaps[int(snap) - 1][0]
    subprocess.run(["rclone", "cat", f"{REMOTE}/{snap}/manifest.yaml"], check=True)


def cmd_restore(args: argparse.Namespace) -> None:
    snap = args.snapshot
    snaps = fetch_snapshots()
    if not snaps:
        die(f"No snapshots found in {REMOTE}")
    names = [n for n, _, _ in snaps]
    if not snap:
        snap = names[-1]
    if snap not in names:
        die(f"Snapshot {snap} not found")
    meta = {n: (m, p) for n, m, p in snaps}
    chain: list[str] = []
    cur = snap
    while True:
        chain.append(cur)
        mode, parent = meta.get(cur, (None, None))
        if mode == "full":
            break
        if not parent or parent not in meta:
            die(f"Required parent snapshot {parent} for {cur} not found")
        cur = parent
    chain.reverse()
    say("Restoring chain: " + " -> ".join(chain))
    if COMPOSE_FILE.exists():
        subprocess.run(dc("down"), check=False)
    ensure_network()
    dump_dir = Path(tempfile.mkdtemp(prefix="paperless-restore-dump."))
    final_dump: Path | None = None
    try:
        first = True
        for item in chain:
            tmp = Path(tempfile.mkdtemp(prefix="paperless-restore."))
            subprocess.run(["rclone", "sync", f"{REMOTE}/{item}", str(tmp)], check=True)
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
                final_dump = dump_dir / dump.name
                shutil.move(str(dump), final_dump)
            shutil.rmtree(tmp)
        if final_dump:
            restore_db(final_dump)
    finally:
        shutil.rmtree(dump_dir, ignore_errors=True)
    ensure_network()
    subprocess.run(dc("up", "-d"), check=False)
    if run_stack_tests():
        ok("Restore complete")
    else:
        warn("Restore complete, but self-test failed")


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
        raw = _read(f"{msg} [{default}]: ").strip() or default
        try:
            return _normalize_time(raw)
        except ValueError as e:
            print(f"Invalid time: {e}")




def prompt_full_schedule(current: str) -> str:
    freq = _read(
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
        dow = _read("Day of week (0=Sun..6=Sat) [0]: ").strip() or "0"
        h, m = _prompt_time("Time (HH:MM)", "03:30")
        return f"{m} {h} * * {dow}"
    if freq.startswith("m"):
        dom = _read("Day of month (1-31) [1]: ").strip() or "1"
        h, m = _prompt_time("Time (HH:MM)", "03:30")
        return f"{m} {h} {dom} * *"
    if freq.startswith("c"):
        return _read(f"Cron expression [{current}]: ").strip() or current
    return freq


def prompt_incr_schedule(current: str) -> str:
    freq = _read(
        "Incremental backup frequency (hourly/daily/weekly/cron) [daily]: "
    ).strip().lower()
    if not freq:
        freq = "daily"
    if " " in freq:
        return freq
    if freq.startswith("h"):
        n = _read("Every how many hours? [1]: ").strip() or "1"
        return f"0 */{int(n)} * * *"
    if freq.startswith("d"):
        h, m = _prompt_time("Time (HH:MM)", "00:00")
        return f"{m} {h} * * *"
    if freq.startswith("w"):
        dow = _read("Day of week (0=Sun..6=Sat) [0]: ").strip() or "0"
        h, m = _prompt_time("Time (HH:MM)", "00:00")
        return f"{m} {h} * * {dow}"
    if freq.startswith("c"):
        return _read(f"Cron expression [{current}]: ").strip() or current
    return freq


def prompt_archive_schedule(current: str) -> str:
    enable = _read("Enable monthly archive backup? (y/N): ").strip().lower()
    if enable.startswith("y"):
        dom = _read("Day of month [1]: ").strip() or "1"
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
        print("1) Start")
        print("2) Stop")
        print("3) Backup")
        print("4) Snapshots")
        print("5) Restore snapshot")
        print("6) Rename instance")
        print("7) Delete instance")
        print("8) Upgrade")
        print("9) Status")
        print("10) Logs")
        print("11) Doctor")
        print("12) Backup schedule")
        print("13) Quit")
        choice = _read("Choose [1-13]: ").strip()
        current = Instance(INSTANCE_NAME, STACK_DIR, DATA_ROOT, os.environ)
        if choice == "3":
            mode_in = _read("Full, Incremental, or Archive? [incr]: ").strip().lower()
            if mode_in.startswith("f"):
                mode = "full"
            elif mode_in.startswith("a"):
                mode = "archive"
            else:
                mode = "incr"
            cmd_backup(argparse.Namespace(mode=mode))
        elif choice == "4":
            cmd_snapshots(argparse.Namespace(snapshot=None))
        elif choice == "5":
            snaps = fetch_snapshots()
            for idx, (name, mode, parent) in enumerate(snaps, 1):
                detail = f"{mode}" if mode != "incr" else f"{mode}<-{parent}"
                print(f"{idx}) {name} ({detail})")
            choice_snap = _read("Snapshot number or name (blank=latest): ").strip()
            if choice_snap.isdigit():
                idx = int(choice_snap)
                snap = snaps[idx - 1][0] if 1 <= idx <= len(snaps) else None
            else:
                snap = choice_snap or None
            cmd_restore(argparse.Namespace(snapshot=snap))
        elif choice == "6":
            new = _read("New name: ").strip()
            if new:
                rename_instance(current, new)
                break
        elif choice == "7":
            delete_instance(current)
            break
        elif choice == "8":
            cmd_upgrade(argparse.Namespace())
        elif choice == "9":
            cmd_status(argparse.Namespace())
        elif choice == "10":
            svc = _read("Service (blank=all): ").strip() or None
            cmd_logs(argparse.Namespace(service=svc))
        elif choice == "11":
            cmd_doctor(argparse.Namespace())
        elif choice == "12":
            cmd_schedule(argparse.Namespace(full=None, incr=None, archive=None))
        elif choice == "1":
            up_instance(current)
        elif choice == "2":
            down_instance(current)
        elif choice == "13":
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
