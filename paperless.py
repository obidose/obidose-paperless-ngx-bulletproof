#!/usr/bin/env python3
"""
Paperless-NGX Bulletproof - Unified Entry Point

Single command for everything: install, manage, backup, restore, health check.
Can be run on fresh machine or existing installation.

Usage:
  # One-liner for any system (fresh or existing):
  curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py | sudo python3

  # Use dev branch:
  curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/dev/paperless.py | sudo python3 - --branch dev

  # Installed system (after first install):
  paperless
"""

import argparse
import os
import sys
import shutil
from pathlib import Path

# Try to import config, bootstrap if needed
try:
    from lib import config
except ModuleNotFoundError:
    config = None  # Will bootstrap and import later


def _parse_branch() -> str:
    """Parse branch from args or environment."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--branch")
    args, unknown = parser.parse_known_args()
    sys.argv[1:] = unknown
    return args.branch or os.environ.get("BP_BRANCH", "main")


BRANCH = _parse_branch()

# Set environment variable for manager to use
os.environ["BP_BRANCH"] = BRANCH

# Installation paths
LIB_INSTALL_DIR = Path("/usr/local/lib/paperless-bulletproof")
CONFIG_DIR = Path("/etc/paperless-bulletproof")


def _bootstrap() -> str:
    """Download repository and return path to extracted repo."""
    import io
    import tarfile
    import tempfile
    import urllib.request

    url = f"https://codeload.github.com/obidose/obidose-paperless-ngx-bulletproof/tar.gz/refs/heads/{BRANCH}"
    tmpdir = tempfile.mkdtemp(prefix="paperless-")
    with urllib.request.urlopen(url) as resp:
        with tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz") as tf:
            root = tf.getmembers()[0].name.split("/", 1)[0]
            tf.extractall(tmpdir)
    repo = os.path.join(tmpdir, root)
    return repo


def _update_installed_library(repo_path: str) -> None:
    """Update the installed library from downloaded repo."""
    repo = Path(repo_path)
    
    # Update library
    lib_src = repo / "lib"
    lib_dst = LIB_INSTALL_DIR / "lib"
    if lib_src.exists():
        if lib_dst.exists():
            shutil.rmtree(lib_dst)
        shutil.copytree(lib_src, lib_dst)
    
    # Update main entry point
    main_src = repo / "paperless.py"
    main_dst = LIB_INSTALL_DIR / "paperless.py"
    if main_src.exists():
        main_dst.write_text(main_src.read_text())
        main_dst.chmod(0o755)
    
    # Update backup/restore scripts for all existing instances
    if CONFIG_DIR.exists():
        instances_file = CONFIG_DIR / "instances.json"
        if instances_file.exists():
            import json
            try:
                instances = json.loads(instances_file.read_text())
                for name, cfg in instances.items():
                    stack_dir = Path(cfg.get("stack_dir", ""))
                    if stack_dir.exists():
                        for script in ("backup.py", "restore.py"):
                            src = repo / "lib" / "modules" / script
                            dst = stack_dir / script
                            if src.exists():
                                dst.write_text(src.read_text())
                                dst.chmod(0o755)
            except Exception:
                pass  # Continue even if instance update fails


def _setup_imports(repo_path: str = None):
    """Set up imports from repo path or installed location."""
    if repo_path:
        sys.path.insert(0, repo_path)
        os.chdir(repo_path)
    
    global PaperlessManager, common, deps, files, pcloud, config
    from lib.manager import PaperlessManager
    from lib.installer import common, deps, files, pcloud
    from lib import config


# Determine if we need to bootstrap
_repo_path = None
_is_installed = LIB_INSTALL_DIR.exists() and (LIB_INSTALL_DIR / "lib").exists()
_is_running_from_install = str(Path(__file__).resolve()).startswith(str(LIB_INSTALL_DIR))

# Always bootstrap from GitHub if not running from installed location
# This ensures one-liner always gets latest code
if not _is_running_from_install:
    print("[*] Fetching latest version...")
    _repo_path = _bootstrap()
    
    # If already installed, update the library and instance scripts
    if _is_installed:
        print("[*] Updating installed files...")
        _update_installed_library(_repo_path)
    
    _setup_imports(_repo_path)
else:
    # Running from installed location - use installed libs
    _setup_imports()


def _get_input(prompt: str, default: str = "") -> str:
    """Get input from user with optional default."""
    if default:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"{prompt}: ").strip()


def _confirm(prompt: str, default: bool = True) -> bool:
    """Get yes/no confirmation from user."""
    yn = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{yn}]: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _initial_backup_server_setup() -> None:
    """Advanced backup server configuration during initial setup.
    
    Uses the same logic as the main app's backup server menu, supporting
    multiple cloud providers (pCloud, Google Drive, Dropbox, etc.)
    """
    import subprocess
    import json
    
    print("Backups are stored in the cloud using rclone, which supports")
    print("70+ cloud storage providers including:")
    print()
    print("   • pCloud      - Great value, EU/US servers (recommended)")
    print("   • Google Drive - 15GB free")
    print("   • Dropbox      - 2GB free")
    print("   • OneDrive     - 5GB free")
    print("   • Backblaze B2 - 10GB free, cheap storage")
    print("   • Amazon S3    - Enterprise scalable")
    print("   • SFTP/WebDAV  - Self-hosted options")
    print()
    
    while True:
        print("Select backup provider:")
        print("  1) pCloud (recommended)")
        print("  2) Google Drive")
        print("  3) Dropbox")
        print("  4) Other provider (advanced)")
        print("  5) Skip for now")
        print()
        
        choice = _get_input("Choose", "1")
        
        if choice == "1":
            # pCloud setup (same as PaperlessManager._setup_pcloud)
            print()
            print("=" * 60)
            print(" pCloud Setup")
            print("=" * 60)
            print()
            print(" pCloud offers excellent value with lifetime plans and")
            print(" servers in both EU and US regions.")
            print()
            print(" Step 1: On any computer with a browser, run:")
            print()
            print('    rclone authorize "pcloud"')
            print()
            print(" Step 2: Log in to pCloud in the browser")
            print()
            print(" Step 3: Copy the token JSON that appears")
            print()
            
            token = _get_input("Paste token JSON (or 'skip' to go back)", "")
            
            if token.lower() == "skip" or not token:
                continue
            
            # Validate JSON
            try:
                json.loads(token)
            except:
                common.error("Invalid JSON format. Make sure you copy the entire token.")
                continue
            
            common.say("Configuring pCloud remote...")
            
            # Try EU region first, then US
            for host, region in [("eapi.pcloud.com", "EU"), ("api.pcloud.com", "US")]:
                subprocess.run(["rclone", "config", "delete", "pcloud"], 
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run([
                    "rclone", "config", "create", "pcloud", "pcloud",
                    "token", token, "hostname", host, "--non-interactive"
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Test connection
                result = subprocess.run(
                    ["rclone", "about", "pcloud:", "--json"],
                    capture_output=True,
                    timeout=15,
                    check=False
                )
                if result.returncode == 0:
                    common.ok(f"pCloud configured successfully ({region} region)")
                    return
            
            common.error("Failed to connect with provided token. Please try again.")
            
        elif choice == "2":
            # Google Drive setup
            print()
            print("=" * 60)
            print(" Google Drive Setup")
            print("=" * 60)
            print()
            print(" Google Drive offers 15GB free storage.")
            print()
            print(' Step 1: On any computer with a browser, run:')
            print()
            print('    rclone authorize "drive"')
            print()
            print(" Step 2: Log in to Google in the browser")
            print()
            print(" Step 3: Copy the token JSON that appears")
            print()
            
            token = _get_input("Paste token JSON (or 'skip' to go back)", "")
            
            if token.lower() == "skip" or not token:
                continue
            
            try:
                json.loads(token)
            except:
                common.error("Invalid JSON format.")
                continue
            
            common.say("Configuring Google Drive remote...")
            
            subprocess.run(["rclone", "config", "delete", "pcloud"], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([
                "rclone", "config", "create", "pcloud", "drive",
                "token", token, "--non-interactive"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            result = subprocess.run(
                ["rclone", "about", "pcloud:", "--json"],
                capture_output=True,
                timeout=15,
                check=False
            )
            if result.returncode == 0:
                common.ok("Google Drive configured successfully")
                return
            else:
                common.error("Failed to configure Google Drive.")
                
        elif choice == "3":
            # Dropbox setup
            print()
            print("=" * 60)
            print(" Dropbox Setup")
            print("=" * 60)
            print()
            print(" Dropbox offers 2GB free storage.")
            print()
            print(' Step 1: On any computer with a browser, run:')
            print()
            print('    rclone authorize "dropbox"')
            print()
            print(" Step 2: Log in to Dropbox in the browser")
            print()
            print(" Step 3: Copy the token JSON that appears")
            print()
            
            token = _get_input("Paste token JSON (or 'skip' to go back)", "")
            
            if token.lower() == "skip" or not token:
                continue
            
            try:
                json.loads(token)
            except:
                common.error("Invalid JSON format.")
                continue
            
            common.say("Configuring Dropbox remote...")
            
            subprocess.run(["rclone", "config", "delete", "pcloud"], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([
                "rclone", "config", "create", "pcloud", "dropbox",
                "token", token, "--non-interactive"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            result = subprocess.run(
                ["rclone", "about", "pcloud:", "--json"],
                capture_output=True,
                timeout=15,
                check=False
            )
            if result.returncode == 0:
                common.ok("Dropbox configured successfully")
                return
            else:
                common.error("Failed to configure Dropbox.")
                
        elif choice == "4":
            # Other provider - use rclone config interactive mode
            print()
            print("=" * 60)
            print(" Other Provider Setup")
            print("=" * 60)
            print()
            print(" For other providers, you'll use rclone's interactive setup.")
            print()
            print(" Important: When prompted for remote name, enter: pcloud")
            print(" (This is required for the backup system to work)")
            print()
            
            if _confirm("Run 'rclone config' now?", True):
                subprocess.run(["rclone", "config"])
                
                # Check if configured
                result = subprocess.run(
                    ["rclone", "listremotes"],
                    capture_output=True, text=True, check=False
                )
                if "pcloud:" in result.stdout:
                    common.ok("Backup provider configured")
                    return
                else:
                    common.warn("Remote 'pcloud:' not found. Please configure again.")
        
        elif choice == "5":
            common.warn("Skipping backup configuration. You can set this up later via:")
            print("  paperless → Configure Backup Server")
            print()
            return
        
        else:
            common.warn("Invalid choice")


def _initial_network_setup() -> None:
    """Offer to set up network services during initial installation.
    
    Uses the same methods as the main app's Tailscale and Traefik menus.
    """
    import subprocess
    from lib.installer import tailscale, traefik
    
    print("Network services allow secure remote access to your instances:")
    print()
    print("   • Tailscale - Zero-config VPN for private remote access")
    print("   • Traefik   - HTTPS proxy with auto SSL certificates")
    print()
    print("These can be set up now or later from the main menu.")
    print()
    
    # Tailscale setup
    if not tailscale.is_tailscale_installed():
        if _confirm("Set up Tailscale (private remote access)?", False):
            common.say("Installing Tailscale...")
            if tailscale.install():
                common.ok("Tailscale installed")
                print()
                if _confirm("Connect Tailscale now?", True):
                    tailscale.connect()
            else:
                common.warn("Tailscale installation failed")
    else:
        common.ok("Tailscale already installed")
        if not tailscale.is_connected():
            if _confirm("Connect Tailscale now?", True):
                tailscale.connect()
    
    print()
    
    # Traefik setup
    if not traefik.is_traefik_running():
        if _confirm("Set up Traefik (HTTPS with auto SSL)?", False):
            print()
            print("Traefik requires a valid email for Let's Encrypt SSL certificates.")
            print()
            email = _get_input("Email address for SSL certificates", "")
            
            if email and "@" in email:
                common.say("Setting up Traefik...")
                if traefik.setup_system_traefik(email):
                    common.ok("Traefik installed and running")
                    print()
                    print("Note: Instances can be configured to use Traefik for HTTPS.")
                    print("DNS records must point to this server's IP address.")
                    print()
                else:
                    common.warn("Traefik setup failed")
            else:
                common.warn("Invalid or empty email - skipping Traefik setup")
    else:
        common.ok("Traefik already running")
    
    print()


def main():
    """Main entry point."""
    from pathlib import Path
    
    # Reconnect stdin to TTY if we're being piped (curl | python3)
    # This allows interactive prompts to work
    if not sys.stdin.isatty():
        try:
            sys.stdin = open('/dev/tty', 'r')
        except OSError:
            # No TTY available (e.g., running in CI)
            print("\n[!] No interactive terminal available.")
            print("    Run 'paperless' to launch the manager.\n")
            sys.exit(0)
    
    # Check if this is first run (base system not installed)
    rclone_installed = Path("/usr/bin/rclone").exists()
    docker_installed = Path("/usr/bin/docker").exists()
    
    if not (rclone_installed and docker_installed):
        # First time setup - install base system
        print("\n" + "="*70)
        print("  Welcome to Paperless-NGX Bulletproof!")
        print("="*70)
        print("\nFirst-time setup: Installing base system...")
        print()
        
        if os.geteuid() != 0:
            print("ERROR: Installation requires root privileges. Please run with sudo.")
            sys.exit(1)
        
        common.say(f"Fetching assets from branch '{BRANCH}'")
        common.preflight_ubuntu()
        deps.install_prereqs()
        deps.ensure_user()
        deps.install_docker()
        deps.install_rclone()
        
        # Set up backup server connection using advanced menu (same as main app)
        print("\n" + "="*70)
        print("  Backup Server Configuration")
        print("="*70)
        print()
        _initial_backup_server_setup()
        
        # Offer Tailscale setup
        print("\n" + "="*70)
        print("  Network Configuration (Optional)")
        print("="*70)
        print()
        _initial_network_setup()
        
        # Install the manager
        files.copy_helper_scripts()
        
        print("\n" + "="*70)
        print("  Base system installed!")
        print("="*70)
        print("\nYou can now:")
        print("  - Run 'paperless' to manage instances")
        print("  - Add instances from backups or create new ones")
        print()
        
        # Auto-detect and import legacy instance
        default_env = Path(config.DEFAULT_STACK_DIR) / ".env"
        if default_env.exists():
            print("Detected existing instance. Importing...")
            # This will be handled by the manager on first launch
    
    # Launch the manager
    try:
        app = PaperlessManager()
        app.run()
    except KeyboardInterrupt:
        print("\n\nExiting...\n")
        sys.exit(0)
    except Exception as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
