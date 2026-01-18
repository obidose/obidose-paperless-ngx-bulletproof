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


def main():
    """Main entry point."""
    from pathlib import Path
    
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
        
        # Set up pCloud connection
        print("\n" + "="*70)
        print("  Backup Configuration")
        print("="*70)
        print()
        pcloud.ensure_pcloud_remote_or_menu()
        
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
