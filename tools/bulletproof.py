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


def _read(prompt: str) -> str:
    """Read input from the user, handling TTY issues gracefully."""
    # Use sys.stdin/stdout if they're connected to a TTY
    if sys.stdin.isatty() and sys.stdout.isatty():
        print(prompt, end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.strip()
    
    # Fall back to direct TTY access for non-interactive environments
    tty_path = os.environ.get("SUDO_TTY") or "/dev/tty"
    try:
        with open(tty_path, "r+") as tty:
            print(prompt, end="", flush=True, file=tty)
            line = tty.readline()
            if not line:
                raise EOFError
            return line.strip()
    except OSError:
        # Last resort: use stdin/stdout even if not TTY
        print(prompt, end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.strip()



def load_env(path: Path) -> None:
    """Load environment variables from a .env file if present."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

# Enhanced color scheme and visual elements
COLOR_BLUE = "\033[1;34m"
COLOR_GREEN = "\033[1;32m"
COLOR_YELLOW = "\033[1;33m"
COLOR_RED = "\033[1;31m"
COLOR_CYAN = "\033[1;36m"
COLOR_MAGENTA = "\033[1;35m"
COLOR_WHITE = "\033[1;37m"
COLOR_GRAY = "\033[0;90m"
COLOR_BOLD = "\033[1m"
COLOR_DIM = "\033[2m"
COLOR_OFF = "\033[0m"

# Status indicators
STATUS_UP = f"{COLOR_GREEN}●{COLOR_OFF}"
STATUS_DOWN = f"{COLOR_RED}●{COLOR_OFF}"
STATUS_UNKNOWN = f"{COLOR_GRAY}●{COLOR_OFF}"

# Icons and symbols
ICON_INFO = f"{COLOR_BLUE}ℹ{COLOR_OFF}"
ICON_SUCCESS = f"{COLOR_GREEN}✓{COLOR_OFF}"
ICON_WARNING = f"{COLOR_YELLOW}⚠{COLOR_OFF}"
ICON_ERROR = f"{COLOR_RED}✗{COLOR_OFF}"
ICON_ARROW = f"{COLOR_CYAN}→{COLOR_OFF}"
ICON_BULLET = f"{COLOR_WHITE}•{COLOR_OFF}"

def print_header(title: str, subtitle: str = "") -> None:
    """Print a stylized header with optional subtitle."""
    width = max(len(title), len(subtitle)) + 4
    border = "═" * width
    print(f"\n{COLOR_CYAN}╔{border}╗{COLOR_OFF}")
    print(f"{COLOR_CYAN}║{COLOR_OFF} {COLOR_BOLD}{title.center(width-2)}{COLOR_OFF} {COLOR_CYAN}║{COLOR_OFF}")
    if subtitle:
        print(f"{COLOR_CYAN}║{COLOR_OFF} {COLOR_DIM}{subtitle.center(width-2)}{COLOR_OFF} {COLOR_CYAN}║{COLOR_OFF}")
    print(f"{COLOR_CYAN}╚{border}╝{COLOR_OFF}\n")

def print_separator(char: str = "─", length: int = 60) -> None:
    """Print a visual separator."""
    print(f"{COLOR_GRAY}{char * length}{COLOR_OFF}")

def say(msg: str) -> None:
    print(f"{ICON_INFO} {msg}")

def ok(msg: str) -> None:
    print(f"{ICON_SUCCESS} {COLOR_GREEN}{msg}{COLOR_OFF}")

def warn(msg: str) -> None:
    print(f"{ICON_WARNING} {COLOR_YELLOW}{msg}{COLOR_OFF}")


# ===== pCloud Setup Functions =====

RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")


def _get_tty_path() -> str:
    """Best-effort path to a readable/writable TTY."""
    for key in ("TTY", "SSH_TTY", "SUDO_TTY"):
        path = os.environ.get(key)
        if path:
            return path
    for fd in (0, 1, 2):
        try:
            return os.ttyname(fd)
        except OSError:
            continue
    return "/dev/tty"


def _pcloud_prompt(text: str) -> str:
    """Read input from user with proper TTY handling for pCloud setup."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        print(text, end="", flush=True)
        return sys.stdin.readline().strip()
    
    # Fall back to direct TTY access
    try:
        tty_path = _get_tty_path()
        with open(tty_path, "r+") as tty:
            print(text, end="", flush=True, file=tty)
            return tty.readline().strip()
    except OSError:
        # Last resort - return empty string if no TTY available
        print(f"[Warning] {text}")
        return ""


def _sanitize_oneline(text: str) -> str:
    return text.replace("\r", "").replace("\n", "").replace("\0", "")


def _timeout(seconds: int, cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, timeout=seconds, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _pcloud_remote_exists() -> bool:
    try:
        res = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, check=True)
        return any(line.strip() == f"{RCLONE_REMOTE_NAME}:" for line in res.stdout.splitlines())
    except Exception:
        return False


def _pcloud_remote_ok() -> bool:
    if not _pcloud_remote_exists():
        return False
    return _timeout(10, ["rclone", "about", f"{RCLONE_REMOTE_NAME}:"])


def _pcloud_create_oauth_remote(token_json: str, host: str) -> bool:
    """Create pCloud OAuth remote and return success status."""
    # Clean up existing remote thoroughly
    subprocess.run(["rclone", "config", "delete", RCLONE_REMOTE_NAME], 
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Create new remote with full non-interactive environment
    env = os.environ.copy()
    env['RCLONE_CONFIG_REFRESH_TOKEN'] = 'false'
    
    result = subprocess.run(
        [
            "rclone",
            "config",
            "create",
            RCLONE_REMOTE_NAME,
            "pcloud",
            "token",
            token_json,
            "hostname",
            host,
            "--non-interactive",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        input="false\n",  # Answer "no" to any prompts that slip through
    )
    
    if result.returncode != 0:
        warn(f"rclone config create failed: {result.stderr.strip()}")
        if result.stdout.strip():
            warn(f"rclone stdout: {result.stdout.strip()}")
        return False
    
    return True


def _pcloud_set_oauth_token_autoregion(token_json: str) -> bool:
    say("Testing cloud storage connection with both regions...")
    
    # Try Europe first as it's more common for OAuth issues
    regions = [
        ("eapi.pcloud.com", "Europe"),
        ("api.pcloud.com", "Global/US")
    ]
    
    for host, region_name in regions:
        say(f"Trying {region_name} region ({host})...")
        
        # Create remote and check if successful
        if not _pcloud_create_oauth_remote(token_json, host):
            warn(f"Failed to create remote config for {region_name}")
            continue
        
        # Give rclone a moment to process the config
        import time
        time.sleep(1)
        
        # Check if remote was created and exists
        if not _pcloud_remote_exists():
            warn(f"Remote config created but not detected for {region_name}")
            continue
            
        # Test connection with more detailed output
        try:
            result = subprocess.run(
                ["rclone", "about", f"{RCLONE_REMOTE_NAME}:"], 
                timeout=15, 
                capture_output=True, 
                text=True
            )
            if result.returncode == 0:
                ok(f"Cloud storage remote '{RCLONE_REMOTE_NAME}:' configured for {region_name} region.")
                return True
            else:
                error_msg = result.stderr.strip()
                warn(f"Connection test failed for {region_name}: {error_msg}")
                
                # Check for region-specific errors
                if "unauthorized" in error_msg.lower() or "401" in error_msg or "2094" in error_msg:
                    warn(f"Token may not be valid for {region_name} region")
                elif "timeout" in error_msg.lower():
                    warn(f"Network timeout connecting to {region_name} region")
                    
        except subprocess.TimeoutExpired:
            warn(f"Connection test timed out for {region_name} region")
        except Exception as e:
            warn(f"Connection test error for {region_name}: {e}")
    
    # If both regions failed, provide helpful guidance
    warn("Token validation failed for both regions. This could be because:")
    warn("• Your cloud account is in a different region than expected")
    warn("• The OAuth token was generated incorrectly")
    warn("• Network connectivity issues")
    say("Try generating a new token with: rclone authorize \"<provider>\"")
    say("Or use option 3 (WebDAV) which works regardless of region.")
    
    return False


def _pcloud_webdav_create(email: str, password: str, host: str) -> None:
    obscured = subprocess.check_output(["rclone", "obscure", password], text=True).strip()
    subprocess.run(["rclone", "config", "delete", RCLONE_REMOTE_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        [
            "rclone",
            "config",
            "create",
            RCLONE_REMOTE_NAME,
            "webdav",
            "--non-interactive",
            "--",
            "vendor",
            "other",
            "url",
            host,
            "user",
            email,
            "pass",
            obscured,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _pcloud_webdav_try_both(email: str, password: str) -> bool:
    for host in ["https://webdav.pcloud.com", "https://ewebdav.pcloud.com"]:
        _pcloud_webdav_create(email, password, host)
        if _pcloud_remote_ok():
            ok(f"pCloud remote configured for {host}")
            return True
    return False


def setup_pcloud_remote() -> bool:
    """Interactive cloud storage setup. Returns True if setup successful."""
    global RCLONE_REMOTE_NAME
    
    # Ensure remote name is properly initialized for multi-main mode
    if not RCLONE_REMOTE_NAME:
        RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    
    if _pcloud_remote_ok():
        ok(f"Cloud storage remote '{RCLONE_REMOTE_NAME}:' is already configured and working.")
        return True

    print_header("Cloud Storage Setup Required")
    
    say("Choose how to connect to your cloud storage:")
    print("  1) Paste OAuth token JSON (recommended)")
    print("  2) Headless OAuth helper")
    print("  3) Try legacy WebDAV")
    print("  4) Skip for now")
    
    while True:
        choice = _pcloud_prompt("Choose [1-4] [1]: ") or "1"

        if choice in {"1", "2"}:
            if choice == "1":
                say('On any machine with a browser, run:  rclone authorize "<provider>"')
                say("")
                say("ℹ Replace <provider> with your cloud storage type:")
                say("  • pcloud, googledrive, dropbox, onedrive, s3, etc.")
            else:
                say("Headless OAuth setup:")
                say("1. On a machine with a browser, install rclone")
                say("2. Run: rclone authorize \"<provider>\"")
                say("3. Copy the JSON token output and paste it below")
                say("4. The token looks like: {\"access_token\":\"...\",\"token_type\":\"bearer\"...}")
            
            say("")
            say("ℹ Note: For pCloud, the system will test both regions automatically:")
            say("  • Global/US region (api.pcloud.com)")
            say("  • Europe region (eapi.pcloud.com)")
            say("")
            
            token = _sanitize_oneline(_pcloud_prompt("Paste token JSON here: "))
            if not token:
                warn("Empty token.")
                continue
            try:
                import json
                parsed = json.loads(token)
                if "access_token" not in parsed:
                    warn("Token missing access_token field.")
                    continue
            except Exception:
                warn("Token does not look like valid JSON.")
                continue
            if _pcloud_set_oauth_token_autoregion(token):
                return True
            warn("If the token keeps failing, your cloud account might be in a")
            warn("different region, or you might need to use WebDAV (option 3).")

        elif choice == "3":
            email = _pcloud_prompt("Cloud storage login email: ").strip()
            if not email:
                warn("Email required.")
                continue
            import getpass

            # Try to use TTY for password input if available
            try:
                tty_path = _get_tty_path()
                with open(tty_path, "r+") as tty:
                    password = getpass.getpass("Cloud storage password (or App Password): ", stream=tty)
            except OSError:
                password = getpass.getpass("Cloud storage password (or App Password): ")
            if not password:
                warn("Password required.")
                continue
            if _pcloud_webdav_try_both(email, password) and _pcloud_remote_ok():
                ok("Cloud storage remote configured.")
                return True
            warn("Authentication failed on both endpoints.")

        elif choice == "4":
            warn("Skipping cloud storage configuration. Some features will be unavailable.")
            return False

        else:
            warn("Invalid choice.")


# ===== End Cloud Storage Setup Functions =====


def cmd_setup_pcloud(args: argparse.Namespace) -> None:
    """Command to set up cloud storage remote."""
    setup_pcloud_remote()


def cmd_create_instance(args: argparse.Namespace) -> None:
    """Command to create a new Paperless-ngx instance."""
    print_header("Create New Instance")
    
    # Ensure pCloud is set up first
    if not _pcloud_remote_ok():
        say("pCloud remote not configured. Setting up now...")
        if not setup_pcloud_remote():
            warn("pCloud setup failed. Instance creation requires pCloud for backups.")
            return
    
    # Get instance name
    while True:
        name = _read("Instance name: ").strip()
        if not name:
            warn("Instance name cannot be empty.")
            continue
        
        # Check if instance already exists
        existing_instances = find_instances()
        if any(inst.name == name for inst in existing_instances):
            warn(f"Instance '{name}' already exists.")
            continue
        
        break
    
    # Check for existing backups first
    remote_instances = list_remote_instances()
    if name in remote_instances:
        restore_choice = _read(f"Found backup for '{name}'. Restore from backup? [y/N]: ").strip().lower()
        if restore_choice.startswith('y'):
            # Restore from backup
            say(f"Restoring instance '{name}' from backup...")
            
            # Get available snapshots
            snapshots = fetch_snapshots_for(name)
            if not snapshots:
                warn(f"No snapshots found for instance '{name}'")
                return
            
            # Show available snapshots and let user choose
            say("Available snapshots:")
            for i, (snap_name, mode, parent) in enumerate(snapshots, 1):
                print(f"  {i}) {snap_name} ({mode})")
            
            latest_snap = snapshots[-1][0]  # Get latest snapshot name
            snap_choice = _read(f"Choose snapshot [1-{len(snapshots)}] or press Enter for latest ({latest_snap}): ").strip()
            
            if snap_choice:
                try:
                    snap_index = int(snap_choice) - 1
                    if 0 <= snap_index < len(snapshots):
                        selected_snap = snapshots[snap_index][0]
                    else:
                        warn("Invalid selection, using latest snapshot")
                        selected_snap = latest_snap
                except ValueError:
                    warn("Invalid input, using latest snapshot")
                    selected_snap = latest_snap
            else:
                selected_snap = latest_snap
            
            # Set up paths and ensure directories exist
            stack_dir = Path(f"/home/docker/{name}-setup")
            data_dir = Path(f"/home/docker/{name}")
            
            # Create directories if they don't exist
            stack_dir.mkdir(parents=True, exist_ok=True)
            data_dir.mkdir(parents=True, exist_ok=True)
            
            # Set up global environment variables for this restore operation
            global STACK_DIR, DATA_ROOT, ENV_FILE, COMPOSE_FILE, REMOTE
            
            STACK_DIR = stack_dir
            DATA_ROOT = data_dir
            ENV_FILE = stack_dir / ".env"
            COMPOSE_FILE = stack_dir / "docker-compose.yml"
            
            # Update environment variables
            os.environ.update({
                "INSTANCE_NAME": name,
                "STACK_DIR": str(stack_dir),
                "DATA_ROOT": str(data_dir),
                "RCLONE_REMOTE_PATH": f"backups/paperless/{name}"
            })
            
            # Re-initialize from updated environment
            init_from_env()
            
            # Create argparse namespace for cmd_restore
            restore_args = argparse.Namespace()
            restore_args.snapshot = selected_snap
            
            # Perform the restore
            say(f"Restoring snapshot '{selected_snap}'...")
            try:
                cmd_restore(restore_args)
                ok(f"Instance '{name}' restored from backup!")
            except Exception as e:
                warn(f"Restore failed: {e}")
                # Clean up any partially created files
                import shutil
                if stack_dir.exists():
                    shutil.rmtree(stack_dir, ignore_errors=True)
                if data_dir.exists():
                    shutil.rmtree(data_dir, ignore_errors=True)
            return
    
    say("Creating new instance from scratch...")
    say("This will guide you through creating a new Paperless-ngx instance.")
    
    # Basic configuration prompts
    timezone = _read("Timezone [UTC]: ").strip() or "UTC"
    
    # Path configuration
    data_root = _read(f"Data directory [/home/docker/{name}]: ").strip() or f"/home/docker/{name}"
    stack_dir = _read(f"Stack directory [/home/docker/{name}-setup]: ").strip() or f"/home/docker/{name}-setup"
    
    # Admin credentials
    admin_user = _read("Admin username [admin]: ").strip() or "admin"
    admin_password = _read("Admin password: ").strip()
    if not admin_password:
        warn("Admin password cannot be empty.")
        return
    
    # Database password
    db_password = _read("Database password [auto-generated]: ").strip()
    if not db_password:
        import secrets
        import string
        db_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        say(f"Generated database password: {db_password}")
    
    # HTTPS/Traefik configuration
    use_https = _read("Enable HTTPS with Traefik? [y/N]: ").strip().lower().startswith('y')
    domain = ""
    email = ""
    
    if use_https:
        domain = _read("Domain name: ").strip()
        if not domain:
            warn("Domain name required for HTTPS.")
            return
        email = _read("Email for Let's Encrypt: ").strip()
        if not email:
            warn("Email required for Let's Encrypt.")
            return
    
    # Create directories
    data_path = Path(data_root)
    stack_path = Path(stack_dir)
    
    try:
        data_path.mkdir(parents=True, exist_ok=True)
        stack_path.mkdir(parents=True, exist_ok=True)
        say(f"Created directories: {data_path}, {stack_path}")
    except Exception as e:
        warn(f"Failed to create directories: {e}")
        return
    
    # Generate .env file
    env_content = f"""# Paperless-ngx Configuration for {name}
PAPERLESS_TIME_ZONE={timezone}
PAPERLESS_ADMIN_USER={admin_user}
PAPERLESS_ADMIN_PASSWORD={admin_password}
POSTGRES_PASSWORD={db_password}
"""
    
    if use_https:
        env_content += f"""DOMAIN={domain}
EMAIL={email}
TRAEFIK_ENABLED=yes
"""
    
    env_file = stack_path / ".env"
    env_file.write_text(env_content)
    say(f"Created configuration file: {env_file}")
    
    # TODO: Generate docker-compose.yml based on configuration
    # TODO: Start the stack
    # TODO: Set up backup schedule
    
    ok(f"Instance '{name}' created successfully!")
    say("Next steps:")
    say(f"  1. cd {stack_dir}")
    say("  2. Review the configuration in .env")
    say("  3. Run 'bulletproof' to manage this instance")


# ===== Enhanced Multi-Instance Management =====

def die(msg: str) -> None:
    print(f"{ICON_ERROR} {COLOR_RED}{msg}{COLOR_OFF}")
    raise SystemExit(1)


STACK_DIR: Path | None = None
DATA_ROOT: Path | None = None
ENV_FILE: Path | None = None
COMPOSE_FILE: Path | None = None

INSTANCE_NAME = ""
RCLONE_REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")  # Initialize with default
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
    if STACK_DIR:
        ENV_FILE = Path(os.environ.get("ENV_FILE", str(STACK_DIR / ".env")))
        COMPOSE_FILE = Path(os.environ.get("COMPOSE_FILE", str(STACK_DIR / "docker-compose.yml")))
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
    """Parse .env file into a dictionary without modifying os.environ."""
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
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


def cleanup_orphans() -> None:
    """Remove stack/data dirs left from aborted installs."""
    leftovers: list[tuple[str, Path, Path]] = []
    for stack in BASE_DIR.glob(f"*{INSTANCE_SUFFIX}"):
        env_file = stack / ".env"
        compose = stack / "docker-compose.yml"
        if env_file.exists() and compose.exists():
            continue
        name = stack.name.replace(INSTANCE_SUFFIX, "")
        data = BASE_DIR / name
        leftovers.append((name, stack, data))
    if leftovers:
        warn("Found incomplete installs:")
        for name, _, _ in leftovers:
            warn(f" - {name}")
        try:
            if _read("Remove these leftovers? (y/N): ").lower().startswith("y"):
                for _, stack, data in leftovers:
                    subprocess.run(["rm", "-rf", str(stack)], check=False)
                    subprocess.run(["rm", "-rf", str(data)], check=False)
        except EOFError:
            pass


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
    
    # Instead of running the full installer, use a simpler approach
    say(f"Creating instance '{name}'...")
    
    # Set environment for this instance
    env = os.environ.copy()
    env.update({
        "INSTANCE_NAME": name,
        "STACK_DIR": str(stack_dir),
        "DATA_ROOT": str(data_dir),
        "BP_BRANCH": BRANCH,
    })
    
    # Run the installer but bypass the CLI detection by setting a flag
    env["BP_FORCE_INSTALL"] = "1"
    
    url = (
        "https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/"
        f"{BRANCH}/install.py"
    )
    cmd = f"curl -fsSL {url} | python3 - --branch {BRANCH}"
    say(f"Installing instance '{name}' from branch {BRANCH}")
    result = subprocess.run(["bash", "-lc", cmd], env=env, check=False)
    
    if result.returncode != 0:
        warn(f"Installation of '{name}' failed")
    else:
        ok(f"Instance '{name}' installed successfully")


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
            net_name = f"paperless_{inst.name}_net"
            subprocess.run(["docker", "network", "rm", net_name], check=False)
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
        # Clean up any remaining networks (each instance has its own)
        for inst in insts:
            try:
                net_name = f"paperless_{inst.name}_net"
                subprocess.run(["docker", "network", "rm", net_name], check=False)
            except Exception:
                pass
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

    # update instance in-place so callers can continue using the same object
    inst.name = new
    inst.stack_dir = new_stack
    inst.data_dir = new_data
    inst.env = env

    ok(f"Renamed to {new}")
    if was_up:
        up_instance(inst)


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
            cleanup_orphans()
            insts = find_instances()
        
        if not insts:
            print_header("Paperless-ngx Instances", "No instances found")
            
            # Check pCloud status and add appropriate options
            pcloud_ok = _pcloud_remote_ok()
            if pcloud_ok:
                pcloud_status = f"{COLOR_GREEN}✓{COLOR_OFF} Cloud storage configured"
                options = [
                    ("1", "Add instance"),
                    ("2", "Explore backups"),
                    ("3", "Reconfigure cloud storage"),
                    ("0", "Quit")
                ]
                choice_range = "[1-3, 0]"
            else:
                pcloud_status = f"{COLOR_YELLOW}!{COLOR_OFF} Cloud storage setup required for all backup operations"
                options = [
                    ("1", "Set up cloud storage"),
                    ("2", "Add instance (requires cloud storage)"),
                    ("3", "Explore backups (requires cloud storage)"),
                    ("0", "Quit")
                ]
                choice_range = "[1-3, 0]"
            
            print(f"\n{COLOR_GRAY}Status:{COLOR_OFF} {pcloud_status}")
            print_menu_options(options)
            
            try:
                choice = _read(f"{COLOR_WHITE}Select action{COLOR_OFF} {COLOR_GRAY}{choice_range}{COLOR_OFF}: ").strip()
            except EOFError:
                print()
                return
                
            if not pcloud_ok:
                # When cloud storage is not set up
                if choice == "1":
                    if setup_pcloud_remote():
                        say("Cloud storage setup completed successfully!")
                    else:
                        warn("Cloud storage setup failed.")
                elif choice == "2":
                    say("Cloud storage setup required for backup functionality.")
                    if setup_pcloud_remote():
                        cmd_create_instance(argparse.Namespace())
                    else:
                        warn("Cannot create instance without cloud storage setup.")
                elif choice == "3":
                    say("Cloud storage setup required to explore backups.")
                    if setup_pcloud_remote():
                        explore_backups()
                    else:
                        warn("Cannot explore backups without cloud storage setup.")
                elif choice == "0":
                    break
                else:
                    warn("Invalid choice")
            else:
                # When cloud storage is already set up
                if choice == "1":
                    cmd_create_instance(argparse.Namespace())
                elif choice == "2":
                    explore_backups()
                elif choice == "3":
                    if setup_pcloud_remote():
                        say("pCloud reconfigured successfully!")
                    else:
                        warn("pCloud reconfiguration failed.")
                elif choice == "0":
                    break
                else:
                    warn("Invalid choice")
            continue
        
        # Display instances table
        print_instances_table(insts)
        
        # Display action menu
        options = [
            ("1", "Manage instance"),
            ("2", "Backup instance"),
            ("3", "Backup all"),
            ("4", "Add instance"),
            ("5", "Start all"),
            ("6", "Stop all"),
            ("7", "Delete all"),
            ("8", "Explore backups"),
            ("0", "Quit")
        ]
        print_menu_options(options)

        try:
            choice = _read(f"{COLOR_WHITE}Select action{COLOR_OFF} {COLOR_GRAY}[1-8, 0]{COLOR_OFF}: ").strip()
        except EOFError:
            print()
            return
        
        # Handle menu choices when instances exist
        if choice == "0":
            break
        elif choice == "1":
            # Manage instance
            if len(insts) == 1:
                # Auto-select the single instance
                inst = insts[0]
                manage_instance(inst)
            else:
                # Let user choose which instance to manage
                try:
                    inst_choice = _read("Instance number to manage: ").strip()
                    inst_num = int(inst_choice)
                    if 1 <= inst_num <= len(insts):
                        inst = insts[inst_num - 1]
                        manage_instance(inst)
                    else:
                        warn("Invalid instance number")
                except ValueError:
                    warn("Invalid input")
        elif choice == "2":
            # Backup instance
            if len(insts) == 1:
                inst = insts[0]
                backup_instance(inst, "full")
            else:
                try:
                    inst_choice = _read("Instance number to backup: ").strip()
                    inst_num = int(inst_choice)
                    if 1 <= inst_num <= len(insts):
                        inst = insts[inst_num - 1]
                        backup_instance(inst, "full")
                    else:
                        warn("Invalid instance number")
                except ValueError:
                    warn("Invalid input")
        elif choice == "3":
            # Backup all
            for inst in insts:
                say(f"Backing up {inst.name}...")
                backup_instance(inst, "full")
        elif choice == "4":
            # Add instance
            cmd_create_instance(argparse.Namespace())
        elif choice == "5":
            # Start all
            for inst in insts:
                say(f"Starting {inst.name}...")
                try:
                    subprocess.run(["docker", "compose", "-f", str(inst.stack_dir / "docker-compose.yml"), "up", "-d"], 
                                 cwd=str(inst.stack_dir), env=inst.env_for_subprocess(), check=True)
                except subprocess.CalledProcessError:
                    warn(f"Failed to start {inst.name}")
        elif choice == "6":
            # Stop all
            for inst in insts:
                say(f"Stopping {inst.name}...")
                try:
                    subprocess.run(["docker", "compose", "-f", str(inst.stack_dir / "docker-compose.yml"), "down"], 
                                 cwd=str(inst.stack_dir), env=inst.env_for_subprocess(), check=True)
                except subprocess.CalledProcessError:
                    warn(f"Failed to stop {inst.name}")
        elif choice == "7":
            # Delete all
            confirm = _read("Are you sure you want to delete ALL instances? [y/N]: ").strip().lower()
            if confirm.startswith('y'):
                for inst in insts:
                    say(f"Deleting {inst.name}...")
                    delete_instance(inst)
        elif choice == "8":
            # Explore backups
            explore_backups()
        else:
            warn("Invalid choice")
def print_instances_table(insts: list[Instance]) -> None:
    """Print a beautiful table of instances with enhanced formatting."""
    if not insts:
        print_header("Paperless-ngx Instances", "No instances found")
        return
    
    print_header("Paperless-ngx Instances", f"{len(insts)} instance{'s' if len(insts) != 1 else ''} found")
    
    # Calculate column widths
    name_width = max(20, max(len(inst.name) for inst in insts) + 2)
    
    # Table header
    header = f"{'#':>3} │ {'NAME':<{name_width}} │ {'STATUS':<8} │ BACKUP SCHEDULE"
    print(f"{COLOR_CYAN}{header}{COLOR_OFF}")
    print(f"{COLOR_GRAY}{'─' * 3}─┼─{'─' * name_width}─┼─{'─' * 8}─┼─{'─' * 30}{COLOR_OFF}")
    
    # Table rows
    for idx, inst in enumerate(insts, 1):
        status = inst.status()
        status_icon = STATUS_UP if status == "up" else STATUS_DOWN
        status_text = f"{status_icon} {status}"
        
        schedule = inst.schedule()
        if len(schedule) > 50:
            schedule = schedule[:47] + "..."
        
        row = f"{COLOR_WHITE}{idx:>3}{COLOR_OFF} │ {COLOR_BOLD}{inst.name:<{name_width}}{COLOR_OFF} │ {status_text:<15} │ {COLOR_DIM}{schedule}{COLOR_OFF}"
        print(row)
    
    print()

def print_menu_options(options: list[tuple[str, str]], title: str = "Actions") -> None:
    """Print menu options with enhanced formatting."""
    print(f"{COLOR_CYAN}┌─ {title} {'─' * (50 - len(title))}┐{COLOR_OFF}")
    
    for i, (key, desc) in enumerate(options):
        if key == "0":
            # Separator before quit option
            print(f"{COLOR_CYAN}├{'─' * 51}┤{COLOR_OFF}")
        
        icon = ICON_ARROW if key != "0" else "◦"
        print(f"{COLOR_CYAN}│{COLOR_OFF} {icon} {COLOR_WHITE}{key}{COLOR_OFF}) {desc:<40} {COLOR_CYAN}│{COLOR_OFF}")
    
    print(f"{COLOR_CYAN}└{'─' * 51}┘{COLOR_OFF}")


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
    if not _pcloud_remote_ok():
        warn("Cloud storage not configured. Use 'bulletproof setup-pcloud' first.")
        return
        
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


def restore_db(dump: Path) -> None:
    say("Restoring database…")
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
        
        # Print header
        print_header(f"Bulletproof Instance - {INSTANCE_NAME}")
        
        # Instance info
        print(f"{COLOR_BOLD}{ICON_INFO} Instance Information{COLOR_OFF}")
        print(f"  {COLOR_GRAY}Remote:{COLOR_OFF} {COLOR_CYAN}{REMOTE}{COLOR_OFF}")
        print(f"  {COLOR_GRAY}Snapshots:{COLOR_OFF} {COLOR_WHITE}{len(snaps)}{COLOR_OFF} (latest: {COLOR_GREEN if snaps else COLOR_RED}{latest}{COLOR_OFF})")
        
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

        print(f"  {COLOR_GRAY}Schedule:{COLOR_OFF} {COLOR_YELLOW}{desc(CRON_FULL_TIME, CRON_INCR_TIME, CRON_ARCHIVE_TIME)}{COLOR_OFF}")
        
        print_separator()
        
        # Print menu options with enhanced formatting
        menu_options = [
            ("1", "Start instance containers"),
            ("2", "Stop instance containers"),
            ("3", "Create backup snapshot"),
            ("4", "List all backup snapshots"),
            ("5", "Restore from backup"),
            ("6", "Change instance name"),
            ("7", "Remove entire instance"),
            ("8", "Update to latest version"),
            ("9", "Show container status"),
            ("10", "View container logs"),
            ("11", "Run diagnostic checks"),
            ("12", "Configure backup timing"),
            ("13", "Exit to main menu")
        ]
        print_menu_options(menu_options, "Instance Actions")
        
        choice = _read(f"{ICON_ARROW} Choose [1-13]: ").strip()
        current = Instance(INSTANCE_NAME, STACK_DIR, DATA_ROOT, os.environ)
        if choice == "3":
            print(f"\n{COLOR_BOLD}{ICON_INFO} Backup Mode Selection{COLOR_OFF}")
            print(f"  {COLOR_GREEN}Full{COLOR_OFF}        - Complete backup (slower, independent)")
            print(f"  {COLOR_YELLOW}Incremental{COLOR_OFF} - Changes only (faster, depends on previous)")
            print(f"  {COLOR_CYAN}Archive{COLOR_OFF}     - Full backup + cleanup old incrementals")
            mode_in = _read(f"{ICON_ARROW} Full, Incremental, or Archive? [incr]: ").strip().lower()
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
            if not snaps:
                print(f"{ICON_WARNING} No snapshots available")
                continue
            print(f"\n{COLOR_BOLD}{ICON_INFO} Available Snapshots{COLOR_OFF}")
            for idx, (name, mode, parent) in enumerate(snaps, 1):
                detail = f"{mode}" if mode != "incr" else f"{mode}<-{parent}"
                mode_color = COLOR_GREEN if mode == "full" else COLOR_YELLOW if mode == "incr" else COLOR_CYAN
                print(f"  {COLOR_WHITE}{idx:2d}){COLOR_OFF} {COLOR_BOLD}{name}{COLOR_OFF} ({mode_color}{detail}{COLOR_OFF})")
            choice_snap = _read(f"{ICON_ARROW} Snapshot number or name (blank=latest): ").strip()
            if choice_snap.isdigit():
                idx = int(choice_snap)
                snap = snaps[idx - 1][0] if 1 <= idx <= len(snaps) else None
            else:
                snap = choice_snap or None
            cmd_restore(argparse.Namespace(snapshot=snap))
        elif choice == "6":
            new = _read(f"{ICON_ARROW} New instance name: ").strip()
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
            svc = _read(f"{ICON_ARROW} Service name (blank=all): ").strip() or None
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
            print(f"{ICON_ERROR} {COLOR_RED}Invalid choice. Please select 1-13.{COLOR_OFF}")
            continue


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

p = sub.add_parser("setup-pcloud", help="set up cloud storage remote for backups")
p.set_defaults(func=cmd_setup_pcloud)

p = sub.add_parser("create", help="create a new Paperless-ngx instance")
p.set_defaults(func=cmd_create_instance)


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
