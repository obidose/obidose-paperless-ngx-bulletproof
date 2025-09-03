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

    enable_traefik: str = os.environ.get("ENABLE_TRAEFIK", "yes")
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
    retention_days: str = os.environ.get("RETENTION_DAYS", "30")
    cron_time: str = os.environ.get("CRON_TIME", "30 3 * * *")

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
    cfg.data_root = prompt("Data root (persistent storage; Enter=default)", cfg.data_root)
    cfg.stack_dir = prompt("Stack dir (where docker-compose.yml lives; Enter=default)", cfg.stack_dir)

    cfg.paperless_admin_user = prompt("Paperless admin username (Enter=default)", cfg.paperless_admin_user)
    cfg.paperless_admin_password = prompt("Paperless admin password (Enter=default)", cfg.paperless_admin_password)
    cfg.postgres_password = prompt("Postgres password (Enter=default)", cfg.postgres_password)

    if prompt("Enable Traefik with HTTPS? (yes/no; Enter=default)", cfg.enable_traefik).lower() in ["y", "yes", "true", "1"]:
        cfg.enable_traefik = "yes"
        cfg.domain = prompt("Domain for Paperless (DNS A/AAAA must point here; Enter=default)", cfg.domain)
        cfg.letsencrypt_email = prompt("Let's Encrypt email (Enter=default)", cfg.letsencrypt_email)
    else:
        cfg.enable_traefik = "no"
        cfg.http_port = prompt("Bind Paperless on host port (Enter=default)", cfg.http_port)

    cfg.refresh_paths()


def prompt_backup_plan() -> None:
    print()
    say("Configure backup schedule")
    cfg.cron_time = prompt("Daily backup cron time", cfg.cron_time)


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
