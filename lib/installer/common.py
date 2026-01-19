import os
import sys
from urllib.request import urlopen
import secrets
from dataclasses import dataclass
from pathlib import Path

try:
    TTY = open("/dev/tty")
except OSError:
    TTY = None

# ----- Pretty output -----
COLOR_BLUE = "\033[1;34m"
COLOR_GREEN = "\033[1;32m"
COLOR_YELLOW = "\033[1;33m"
COLOR_RED = "\033[1;31m"
COLOR_OFF = "\033[0m"

def say(msg: str) -> None:
    print(f"{COLOR_BLUE}[*]{COLOR_OFF} {msg}")

def log(msg: str) -> None:
    say(msg)

def ok(msg: str) -> None:
    print(f"{COLOR_GREEN}[ok]{COLOR_OFF} {msg}")

def warn(msg: str) -> None:
    print(f"{COLOR_YELLOW}[!]{COLOR_OFF} {msg}")

def error(msg: str) -> None:
    print(f"{COLOR_RED}[ERROR]{COLOR_OFF} {msg}")

def die(msg: str, code: int = 1) -> None:
    print(f"{COLOR_RED}[x]{COLOR_OFF} {msg}")
    sys.exit(code)

# ----- Helpers -----

def need_root() -> None:
    if os.geteuid() != 0:
        die("Run as root (sudo -i).")


def preflight_ubuntu() -> None:
    if not Path("/etc/os-release").exists():
        warn("/etc/os-release not found; continuing anyway.")
        return
    data = dict(
        line.strip().split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line
    )
    version = data.get("VERSION_ID", "").strip('"')
    if version not in {"22.04", "24.04"}:
        warn(f"Ubuntu {version} detected; tested on 22.04/24.04.")


def randpass(length: int = 22) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#%+=?"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _read(prompt: str) -> str:
    if TTY is None:
        return ""
    print(prompt, end="", flush=True)
    return TTY.readline().strip()


def prompt(msg: str, default: str | None = None) -> str:
    if default:
        ans = _read(f"{msg} [{default}]: ") or default
    else:
        ans = _read(f"{msg}: ")
    return ans.strip()


def is_valid_domain(domain: str) -> tuple[bool, str]:
    """Validate a domain name.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    import re
    
    if not domain:
        return False, "Domain cannot be empty"
    
    # Check for @ symbol (common mistake: entering email instead of domain)
    if '@' in domain:
        return False, "Domain cannot contain '@' - did you enter an email address?"
    
    # Check for spaces
    if ' ' in domain:
        return False, "Domain cannot contain spaces"
    
    # Check for protocol prefix
    if domain.startswith(('http://', 'https://')):
        return False, "Domain should not include http:// or https://"
    
    # Check for path
    if '/' in domain:
        return False, "Domain should not include a path (no '/' allowed)"
    
    # Basic domain format validation
    domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
    if not re.match(domain_pattern, domain):
        return False, "Invalid domain format (e.g., paperless.example.com)"
    
    return True, ""


def prompt_domain(msg: str, default: str | None = None) -> str:
    """Prompt for and validate a domain name."""
    while True:
        domain = prompt(msg, default)
        
        is_valid, error_msg = is_valid_domain(domain)
        if is_valid:
            return domain
        
        error(error_msg)


def is_valid_email(email: str) -> tuple[bool, str]:
    """Validate email format.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    import re
    
    if not email:
        return False, "Email cannot be empty"
    
    if ' ' in email:
        return False, "Email cannot contain spaces"
    
    if email.count('@') != 1:
        return False, "Email must contain exactly one '@' symbol"
    
    local, domain = email.split('@')
    
    if not local:
        return False, "Email local part (before @) cannot be empty"
    
    if not domain:
        return False, "Email domain (after @) cannot be empty"
    
    if '.' not in domain:
        return False, "Email domain must include a TLD (e.g., .com, .org)"
    
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Invalid email format (e.g., admin@example.com)"
    
    return True, ""


def prompt_email(msg: str, default: str | None = None) -> str:
    """Prompt for and validate an email address."""
    while True:
        email = prompt(msg, default)
        
        is_valid, error_msg = is_valid_email(email)
        if is_valid:
            return email
        
        error(error_msg)


def is_valid_port(port: str) -> tuple[bool, str]:
    """Validate port number.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not port:
        return False, "Port cannot be empty"
    
    if not port.isdigit():
        return False, "Port must be a number"
    
    port_num = int(port)
    
    if port_num < 1 or port_num > 65535:
        return False, "Port must be between 1 and 65535"
    
    if port_num < 1024:
        return False, "Port must be 1024 or higher (privileged ports not allowed)"
    
    return True, ""


def prompt_port(msg: str, default: str | None = None) -> str:
    """Prompt for and validate a port number."""
    while True:
        port = prompt(msg, default)
        
        is_valid, error_msg = is_valid_port(port)
        if is_valid:
            return port
        
        error(error_msg)


def prompt_secret(msg: str) -> str:
    import getpass

    if TTY is None:
        return ""
    return getpass.getpass(f"{msg}: ", stream=TTY)


def confirm(msg: str, default: bool = True) -> bool:
    if TTY is None:
        return default
    opts = "Y/n" if default else "y/N"
    ans = _read(f"{msg} [{opts}]: ").lower()
    if not ans:
        return default
    return ans.startswith("y")

# ----- Config dataclass -----

@dataclass
class Config:
    instance_name: str = os.environ.get("INSTANCE_NAME", "paperless")
    stack_dir: str = os.environ.get("STACK_DIR", "/home/docker/paperless-setup")
    data_root: str = os.environ.get("DATA_ROOT", "/home/docker/paperless")

    tz: str = os.environ.get("TZ", open('/etc/timezone').read().strip() if Path('/etc/timezone').exists() else 'Etc/UTC')
    puid: str = os.environ.get("PUID", "1001")
    pgid: str = os.environ.get("PGID", "1001")

    enable_traefik: str = os.environ.get("ENABLE_TRAEFIK", "no")
    enable_cloudflared: str = os.environ.get("ENABLE_CLOUDFLARED", "no")
    enable_tailscale: str = os.environ.get("ENABLE_TAILSCALE", "no")
    http_port: str = os.environ.get("HTTP_PORT", "8000")
    domain: str = os.environ.get("DOMAIN", "paperless.example.com")
    letsencrypt_email: str = os.environ.get("LETSENCRYPT_EMAIL", "admin@example.com")

    postgres_version: str = os.environ.get("POSTGRES_VERSION", "15")
    postgres_db: str = os.environ.get("POSTGRES_DB", "paperless")
    postgres_user: str = os.environ.get("POSTGRES_USER", "paperless")
    postgres_password: str = os.environ.get("POSTGRES_PASSWORD", randpass())

    paperless_admin_user: str = os.environ.get("PAPERLESS_ADMIN_USER", "admin")
    paperless_admin_password: str = os.environ.get("PAPERLESS_ADMIN_PASSWORD", randpass())

    rclone_remote_name: str = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    rclone_remote_path: str = os.environ.get("RCLONE_REMOTE_PATH", "backups/paperless/paperless")
    
    # Backup schedule (incremental 6h, full weekly, archive monthly)
    cron_incr_time: str = os.environ.get("CRON_INCR_TIME", "0 */6 * * *")
    cron_full_time: str = os.environ.get("CRON_FULL_TIME", "30 3 * * 0")
    cron_archive_time: str = os.environ.get("CRON_ARCHIVE_TIME", "0 4 1 * *")
    
    # Retention policy (keep all for 30 days, monthly archives for 6 months)
    retention_days: str = os.environ.get("RETENTION_DAYS", "30")
    retention_monthly_days: str = os.environ.get("RETENTION_MONTHLY_DAYS", "180")

    env_backup_mode: str = os.environ.get("ENV_BACKUP_MODE", "openssl")
    env_backup_passphrase_file: str = os.environ.get("ENV_BACKUP_PASSPHRASE_FILE", "/root/.paperless_env_pass")
    include_compose_in_backup: str = os.environ.get("INCLUDE_COMPOSE_IN_BACKUP", "yes")

    def refresh_paths(self) -> None:
        self.dir_export = os.path.join(self.data_root, "export")
        self.dir_media = os.path.join(self.data_root, "media")
        self.dir_data = os.path.join(self.data_root, "data")
        self.dir_consume = os.path.join(self.data_root, "consume")
        self.dir_db = os.path.join(self.data_root, "db")
        self.dir_tika_cache = os.path.join(self.data_root, "tika-cache")
        self.compose_file = os.path.join(self.stack_dir, "docker-compose.yml")
        self.env_file = os.path.join(self.stack_dir, ".env")


def ensure_dir_tree(cfg: Config) -> None:
    for d in [
        cfg.stack_dir,
        cfg.data_root,
        cfg.dir_export,
        cfg.dir_media,
        cfg.dir_data,
        cfg.dir_consume,
        cfg.dir_db,
        cfg.dir_tika_cache,
    ]:
        Path(d).mkdir(parents=True, exist_ok=True)


cfg = Config()
cfg.refresh_paths()


def prompt_core_values() -> None:
    print()
    print("Press Enter to accept the [default] value, or type a custom value.")
    cfg.tz = prompt("Timezone (IANA, e.g., Pacific/Auckland; Enter=default)", cfg.tz)
    cfg.instance_name = prompt("Instance name (Enter=default)", cfg.instance_name)
    
    # Update default paths based on instance name BEFORE prompting
    if cfg.instance_name != "paperless":
        cfg.data_root = f"/home/docker/{cfg.instance_name}"
        cfg.stack_dir = f"/home/docker/{cfg.instance_name}-setup"
        # Ensure backups go under the instance name
        cfg.rclone_remote_path = f"backups/paperless/{cfg.instance_name}"
    else:
        cfg.data_root = "/home/docker/paperless"
        cfg.stack_dir = "/home/docker/paperless-setup"
        cfg.rclone_remote_path = "backups/paperless/paperless"
    
    # Show computed defaults
    print()
    say(f"Instance '{cfg.instance_name}' will use:")
    print(f"  Data root: {cfg.data_root}")
    print(f"  Stack dir: {cfg.stack_dir}")
    print()
    
    # Now prompt with the updated defaults
    cfg.data_root = prompt("Data root (persistent storage; Enter=default)", cfg.data_root)
    cfg.stack_dir = prompt("Stack dir (where docker-compose.yml lives; Enter=default)", cfg.stack_dir)
    
    # Warn if paths already exist
    if Path(cfg.stack_dir).exists():
        warn(f"Stack directory {cfg.stack_dir} already exists - may conflict with existing instance")
    if Path(cfg.data_root).exists():
        warn(f"Data directory {cfg.data_root} already exists - may conflict with existing instance")

    cfg.paperless_admin_user = prompt("Paperless admin username (Enter=default)", cfg.paperless_admin_user)
    cfg.paperless_admin_password = prompt("Paperless admin password (Enter=default)", cfg.paperless_admin_password)
    cfg.postgres_password = prompt("Postgres password (Enter=default)", cfg.postgres_password)

    cfg.refresh_paths()


def get_next_available_port(start_port: int = 8000) -> str:
    """Find the next available port starting from start_port."""
    import socket
    port = start_port
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return str(port)
            except OSError:
                port += 1
    return str(start_port)  # Fallback


def prompt_networking() -> None:
    """Ask how the instance should be accessed."""
    print()
    say("How do you want to access this instance publicly?")
    print("  1) Direct HTTP only - bind to a port (e.g., localhost:8000)")
    print("  2) HTTPS via Traefik - automatic SSL with Let's Encrypt")
    print("  3) Cloudflare Tunnel - secure access via Cloudflare (no open ports)")
    
    choice = _read("Choose [1-3] [1]: ") or "1"
    
    if choice == "2":
        cfg.enable_traefik = "yes"
        cfg.enable_cloudflared = "no"
        cfg.domain = prompt_domain("Domain for Paperless (DNS A/AAAA must point here; Enter=default)", cfg.domain)
    elif choice == "3":
        cfg.enable_traefik = "no"
        cfg.enable_cloudflared = "yes"
        cfg.domain = prompt_domain("Domain for Paperless (configured in Cloudflare)", cfg.domain)
    else:
        cfg.enable_traefik = "no"
        cfg.enable_cloudflared = "no"
    
    # Find next available port if default is in use
    suggested_port = get_next_available_port(int(cfg.http_port))
    if suggested_port != cfg.http_port:
        warn(f"Port {cfg.http_port} is in use, suggesting {suggested_port}")
        cfg.http_port = suggested_port
    
    # Always set an http_port (needed for direct access or as backend)
    cfg.http_port = prompt_port("Bind Paperless on host port (Enter=default)", cfg.http_port)
    
    # Tailscale is additive - can be used alongside any other method
    print()
    say("Tailscale provides private VPN access and can be used alongside any method above.")
    if _read("Also enable Tailscale for private network access? [y/N]: ").lower().startswith("y"):
        cfg.enable_tailscale = "yes"
        say("Instance will also be accessible via Tailscale network")
    else:
        cfg.enable_tailscale = "no"


def prompt_backup_plan() -> None:
    print()
    say("Configure backup schedule")
    print("Full backups capture everything; incremental backups save changes since the last full.")

    freq_full = prompt(
        "Full backup frequency (daily/weekly/monthly/cron)", "weekly"
    ).lower()
    if " " in freq_full:
        cfg.cron_full_time = freq_full
    elif freq_full.startswith("d"):
        t = prompt("Time (HH:MM)", "03:30")
        h, m = t.split(":", 1)
        cfg.cron_full_time = f"{int(m)} {int(h)} * * *"
    elif freq_full.startswith("w"):
        dow = prompt("Day of week (0=Sun..6=Sat)", "0")
        t = prompt("Time (HH:MM)", "03:30")
        h, m = t.split(":", 1)
        cfg.cron_full_time = f"{int(m)} {int(h)} * * {dow}"
    elif freq_full.startswith("m"):
        dom = prompt("Day of month (1-31)", "1")
        t = prompt("Time (HH:MM)", "03:30")
        h, m = t.split(":", 1)
        cfg.cron_full_time = f"{int(m)} {int(h)} {dom} * *"
    elif freq_full.startswith("c"):
        cfg.cron_full_time = prompt("Cron expression", cfg.cron_full_time)

    freq_incr = prompt(
        "Incremental backup frequency (hourly/daily/weekly/cron)", "daily"
    ).lower()
    if " " in freq_incr:
        cfg.cron_incr_time = freq_incr
    elif freq_incr.startswith("h"):
        n = prompt("Every how many hours?", "1")
        cfg.cron_incr_time = f"0 */{int(n)} * * *"
    elif freq_incr.startswith("d"):
        t = prompt("Time (HH:MM)", "00:00")
        h, m = t.split(":", 1)
        cfg.cron_incr_time = f"{int(m)} {int(h)} * * *"
    elif freq_incr.startswith("w"):
        dow = prompt("Day of week (0=Sun..6=Sat)", "0")
        t = prompt("Time (HH:MM)", "00:00")
        h, m = t.split(":", 1)
        cfg.cron_incr_time = f"{int(m)} {int(h)} * * {dow}"
    elif freq_incr.startswith("c"):
        cfg.cron_incr_time = prompt("Cron expression", cfg.cron_incr_time)

    if confirm("Enable monthly archive backup?", False):
        dom = prompt("Day of month for archive", "1")
        t = prompt("Time for archive (HH:MM)", "04:00")
        h, m = t.split(":", 1)
        cfg.cron_archive_time = f"{int(m)} {int(h)} {dom} * *"
    else:
        cfg.cron_archive_time = ""


def pick_and_merge_preset(base: str) -> None:
    print()
    say("Select a preset (optional):")
    options = {"1": ("traefik", "Traefik + HTTPS"), "2": ("direct", "Direct HTTP"), "3": (None, "Skip")}
    for key, (name, desc) in options.items():
        label = f"{name} - {desc}" if name else desc
        print(f"  {key}) {label}")
    choice = _read("Choose [1-3] [3]: ") or "3"
    name = options.get(choice, (None,))[0]
    if not name:
        return
    content = ""
    if base.startswith("http://") or base.startswith("https://"):
        url = f"{base}/presets/{name}.env"
        try:
            with urlopen(url) as resp:
                content = resp.read().decode()
        except Exception:
            warn(f"Failed to download preset from {url}")
            return
    else:
        path = Path(base) / "presets" / f"{name}.env"
        if not path.exists():
            warn(f"Preset file not found at {path}")
            return
        content = path.read_text()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)
        attr = k.lower()
        if hasattr(cfg, attr):
            setattr(cfg, attr, v)
    cfg.refresh_paths()
    ok(f"Loaded {name} preset.")
