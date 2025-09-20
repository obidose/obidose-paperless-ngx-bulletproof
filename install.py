#!/usr/bin/env python3
"""Lightweight installer for Paperless-ngx Bulletproof CLI.

This installer only:
1. Installs basic prerequisites (Docker, rclone) 
2. Downloads and installs the bulletproof CLI
3. Launches bulletproof for all actual functionality

All pCloud setup, instance management, etc. is handled by bulletproof itself.
"""

from pathlib import Path
import os
import argparse
import sys
import shutil
import subprocess
import tempfile
import urllib.request
import tarfile
import io


def _parse_branch() -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--branch")
    args, unknown = parser.parse_known_args()
    sys.argv[1:] = unknown
    return args.branch or os.environ.get("BP_BRANCH", "main")


BRANCH = _parse_branch()

# Basic output functions
COLOR_BLUE = "\033[1;34m"
COLOR_GREEN = "\033[1;32m"
COLOR_YELLOW = "\033[1;33m"
COLOR_RED = "\033[1;31m"
COLOR_OFF = "\033[0m"

def say(msg: str) -> None:
    print(f"{COLOR_BLUE}[*]{COLOR_OFF} {msg}")

def ok(msg: str) -> None:
    print(f"{COLOR_GREEN}[✓]{COLOR_OFF} {msg}")

def warn(msg: str) -> None:
    print(f"{COLOR_YELLOW}[!]{COLOR_OFF} {msg}")

def die(msg: str, code: int = 1) -> None:
    print(f"{COLOR_RED}[✗]{COLOR_OFF} {msg}")
    sys.exit(code)


def run_command(cmd: list[str], description: str | None = None) -> bool:
    """Run a command and return success/failure."""
    try:
        if description:
            say(description)
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False


def install_prerequisites() -> None:
    """Install basic prerequisites."""
    say("Installing prerequisites...")
    
    # Update package lists
    run_command(["apt", "update"], "Updating package lists")
    
    # Install basic tools
    basic_packages = [
        "ca-certificates", "curl", "gnupg", "lsb-release", "tar", 
        "cron", "software-properties-common", "jq", "dos2unix", "unzip"
    ]
    
    cmd = ["apt", "install", "-y"] + basic_packages
    if not run_command(cmd, "Installing basic packages"):
        die("Failed to install basic packages")


def install_docker() -> None:
    """Install Docker if not present."""
    if run_command(["docker", "--version"], None):
        ok("Docker already installed")
        return
        
    say("Installing Docker...")
    
    # Add Docker GPG key
    run_command(["curl", "-fsSL", "https://download.docker.com/linux/ubuntu/gpg", 
                "-o", "/usr/share/keyrings/docker-archive-keyring.asc"])
    run_command(["gpg", "--dearmor", "/usr/share/keyrings/docker-archive-keyring.asc"])
    run_command(["mv", "/usr/share/keyrings/docker-archive-keyring.asc.gpg", 
                "/usr/share/keyrings/docker-archive-keyring.gpg"])
    
    # Add Docker repository
    lsb_release = subprocess.check_output(["lsb_release", "-cs"], text=True).strip()
    repo_line = f"deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu {lsb_release} stable"
    
    with open("/etc/apt/sources.list.d/docker.list", "w") as f:
        f.write(repo_line + "\n")
    
    # Install Docker
    run_command(["apt", "update"])
    docker_packages = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"]
    cmd = ["apt", "install", "-y"] + docker_packages
    
    if not run_command(cmd):
        die("Failed to install Docker")
    
    # Start Docker service
    run_command(["systemctl", "enable", "docker"])
    run_command(["systemctl", "start", "docker"])
    
    ok("Docker installed successfully")


def install_rclone() -> None:
    """Install rclone if not present."""
    if run_command(["rclone", "version"], None):
        ok("rclone already installed")
        return
        
    say("Installing rclone...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "rclone.zip")
        extract_dir = os.path.join(tmpdir, "extract")
        
        # Download rclone
        url = "https://downloads.rclone.org/rclone-current-linux-amd64.zip"
        urllib.request.urlretrieve(url, zip_path)
        
        # Extract
        import zipfile
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Find the rclone binary and install it
        for root, dirs, files in os.walk(extract_dir):
            if "rclone" in files:
                rclone_path = os.path.join(root, "rclone")
                shutil.copy2(rclone_path, "/usr/local/bin/rclone")
                os.chmod("/usr/local/bin/rclone", 0o755)
                break
        else:
            die("Could not find rclone binary in download")
    
    if not run_command(["rclone", "version"], None):
        die("Failed to install rclone")
    
    ok("rclone installed successfully")


def ensure_docker_user() -> None:
    """Create docker user if it doesn't exist."""
    try:
        subprocess.run(["id", "docker"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok("User 'docker' already exists")
    except subprocess.CalledProcessError:
        say("Creating 'docker' user...")
        subprocess.run(["useradd", "-r", "-s", "/bin/false", "-c", "Docker user", "-u", "1001", "docker"], check=True)
        ok("Created user 'docker'")


def download_and_install_bulletproof() -> None:
    """Download the latest bulletproof CLI and install it."""
    say("Installing bulletproof CLI...")
    
    # Download bulletproof.py
    url = f"https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}/tools/bulletproof.py"
    
    try:
        with urllib.request.urlopen(url) as response:
            content = response.read().decode('utf-8')
        
        # Install to /usr/local/bin/bulletproof
        with open("/usr/local/bin/bulletproof", "w") as f:
            f.write(content)
        
        os.chmod("/usr/local/bin/bulletproof", 0o755)
        ok("bulletproof CLI installed to /usr/local/bin/bulletproof")
        
    except Exception as e:
        die(f"Failed to download bulletproof CLI: {e}")


def main() -> None:
    """Main installer function."""
    if os.getuid() != 0:
        die("This installer must be run as root. Use 'sudo' or run as root user.")
    
    say(f"Installing Paperless-ngx Bulletproof CLI (branch: {BRANCH})")
    
    try:
        install_prerequisites()
        ensure_docker_user()
        install_docker()
        install_rclone()
        download_and_install_bulletproof()
        
        ok("Installation complete!")
        print()
        say("Next steps:")
        print("  1. Run 'bulletproof' to set up pCloud and manage instances")
        print("  2. Use 'bulletproof --help' to see all available options")
        print()
        say("Launching bulletproof now...")
        
        # Launch bulletproof
        os.execv("/usr/local/bin/bulletproof", ["bulletproof"])
        
    except KeyboardInterrupt:
        warn("Installation cancelled")
        sys.exit(1)
    except Exception as e:
        die(f"Installation failed: {e}")


if __name__ == "__main__":
    main()

from pathlib import Path
import os
import argparse
import sys
import shutil
import subprocess


def _parse_branch() -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--branch")
    args, unknown = parser.parse_known_args()
    sys.argv[1:] = unknown
    return args.branch or os.environ.get("BP_BRANCH", "main")


BRANCH = _parse_branch()


def _bootstrap() -> None:
    """Download repository sources into a temporary directory and load them."""
    import io
    import sys
    import tarfile
    import tempfile
    import urllib.request

    url = (
        "https://codeload.github.com/obidose/obidose-paperless-ngx-bulletproof/"
        f"tar.gz/refs/heads/{BRANCH}"
    )
    tmpdir = tempfile.mkdtemp(prefix="paperless-inst-")
    with urllib.request.urlopen(url) as resp:
        with tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz") as tf:
            root = tf.getmembers()[0].name.split("/", 1)[0]
            tf.extractall(tmpdir)
    repo = os.path.join(tmpdir, root)
    os.chdir(repo)
    sys.path.insert(0, repo)


try:  # first attempt to import locally present modules
    from installer import common, deps, files, pcloud
    from utils.selftest import run_stack_tests
except ModuleNotFoundError:
    _bootstrap()
    from installer import common, deps, files, pcloud
    from utils.selftest import run_stack_tests

cfg = common.cfg
say = common.say
need_root = common.need_root
ensure_dir_tree = common.ensure_dir_tree
preflight_ubuntu = common.preflight_ubuntu
prompt_core_values = common.prompt_core_values
pick_and_merge_preset = common.pick_and_merge_preset
ok = common.ok
warn = common.warn
# ``prompt_backup_plan`` was added in newer releases; fall back to a no-op if
# running against an older checkout that lacks it.
prompt_backup_plan = getattr(common, "prompt_backup_plan", lambda: None)


def offer_initial_actions() -> bool:
    """Return True if the script should exit early."""
    from tools import bulletproof as bp

    rem = bp.list_remote_instances()
    
    if rem:
        say(f"Found remote backups for {len(rem)} instance(s): {', '.join(rem)}")
        opts: list[tuple[str, str]] = [
            ("Restore all backups (full restore)", "restore"),
            ("Install new instance", "install"),
            ("Launch Bulletproof CLI (advanced)", "cli"),
            ("Quit", "quit")
        ]
    else:
        say("No remote backups found.")
        opts: list[tuple[str, str]] = [
            ("Install new instance", "install"),
            ("Launch Bulletproof CLI (advanced)", "cli"),
            ("Quit", "quit")
        ]

    while True:
        print()
        say("Select action:")
        for idx, (label, _) in enumerate(opts, 1):
            say(f" {idx}) {label}")
        choice = common.prompt("Select", "1")
        try:
            action = opts[int(choice) - 1][1]
            break
        except Exception:
            say("Invalid choice")

    if action == "restore":
        say("Restoring all instances from backups...")
        for name in rem:
            cfg.instance_name = name
            cfg.stack_dir = str(bp.BASE_DIR / f"{name}{bp.INSTANCE_SUFFIX}")
            cfg.data_root = str(bp.BASE_DIR / name)
            cfg.refresh_paths()
            ensure_dir_tree(cfg)
            inst = bp.Instance(name, Path(cfg.stack_dir), Path(cfg.data_root), {})
            snaps = bp.fetch_snapshots_for(name)
            if snaps:
                say(f"Restoring '{name}' from latest backup...")
                bp.restore_instance(inst, snaps[-1][0], name)
            if Path(cfg.env_file).exists():
                for line in Path(cfg.env_file).read_text().splitlines():
                    if line.startswith("CRON_FULL_TIME="):
                        cfg.cron_full_time = line.split("=", 1)[1].strip()
                    elif line.startswith("CRON_INCR_TIME="):
                        cfg.cron_incr_time = line.split("=", 1)[1].strip()
                    elif line.startswith("CRON_ARCHIVE_TIME="):
                        cfg.cron_archive_time = line.split("=", 1)[1].strip()
            files.copy_helper_scripts()
            files.install_cron_backup()
        ok("All instances restored! Launching Bulletproof CLI...")
        bp.multi_main()
        return True

    if action == "cli":
        say("Launching Bulletproof CLI...")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
        try:
            with open("/dev/tty", "r+") as tty:
                subprocess.run(
                    [sys.executable, str(Path(__file__).resolve().parent / "tools" / "bulletproof.py")],
                    stdin=tty,
                    stdout=tty,
                    stderr=tty,
                    check=False,
                    env=env,
                )
        except OSError:
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "tools" / "bulletproof.py")],
                check=False,
                env=env,
            )
        return True

    if action == "quit":
        return True

    # action == "install" - continue with normal installation flow
    return False


def main() -> None:
    need_root()
    say(f"Fetching assets from branch '{BRANCH}'")

    say("Starting Paperless-ngx setup wizard...")
    preflight_ubuntu()

    # If the Bulletproof CLI is already installed this one-liner acts as a
    # convenience wrapper.  Skip the heavy installation routine and hand off to
    # the multi-instance manager instead of re-running the wizard.
    # But if BP_FORCE_INSTALL is set, proceed with normal installation
    if (shutil.which("bulletproof") and Path("/usr/local/bin/bulletproof").exists() 
        and not os.environ.get("BP_FORCE_INSTALL")):
        say("Bulletproof CLI detected; launching manager...")
        files.install_global_cli()
        
        # Ensure pCloud is configured even when CLI is already installed
        pcloud.ensure_pcloud_remote_or_menu()
        
        # Always offer initial actions when CLI is already installed
        if offer_initial_actions():
            return
        
        # If they chose to continue with CLI, launch it
        from tools import bulletproof as bp
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
        cli_path = Path(__file__).resolve().parent / "tools" / "bulletproof.py"
        tty_path = os.environ.get("SUDO_TTY") or "/dev/tty"
        try:
            with open(tty_path, "r+") as tty:
                subprocess.run(
                    [sys.executable, str(cli_path)],
                    check=False,
                    env=env,
                    stdin=tty,
                    stdout=tty,
                    stderr=tty,
                )
        except OSError:
            subprocess.run([sys.executable, str(cli_path)], check=False, env=env)
        return

    try:
        deps.install_prereqs()
        deps.ensure_user()
        deps.install_docker()
        deps.install_rclone()

        # pCloud
        pcloud.ensure_pcloud_remote_or_menu()

        # Offer to restore existing backups or jump straight into the CLI
        if offer_initial_actions():
            return

        # Presets and prompts
        pick_and_merge_preset(
            f"https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}"
        )
        prompt_core_values()
        prompt_backup_plan()

        # Directories and files
        ensure_dir_tree(cfg)
        files.write_env_file()
        files.write_compose_file()
        files.copy_helper_scripts()
        files.bring_up_stack()

        if run_stack_tests(Path(cfg.compose_file), Path(cfg.env_file)):
            ok("Self-test passed")
        else:
            warn("Self-test failed; check container logs")

        files.install_cron_backup()
        files.show_status()
    except KeyboardInterrupt:
        warn("Installation cancelled; cleaning up")
        files.cleanup_stack_dir()
        raise
    except Exception as e:
        warn(f"Installation failed: {e}")
        files.cleanup_stack_dir()
        raise


if __name__ == "__main__":
    main()
