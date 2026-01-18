#!/usr/bin/env python3
"""
Paperless-NGX Bulletproof - Unified Entry Point

Single command for everything: install, manage, backup, restore, health check.
Can be run on fresh machine or existing installation.

Usage:
  # Fresh machine (downloads and runs) - TWO step process for interactive prompts:
  wget https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py
  sudo python3 paperless.py

  # Or one-liner (downloads to temp and executes):
  curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py > /tmp/paperless_install.py && sudo python3 /tmp/paperless_install.py

  # Installed system
  paperless
"""

import argparse
import os
import sys
from pathlib import Path


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


def _bootstrap() -> None:
    """Download repository if not already present."""
    import io
    import tarfile
    import tempfile
    import urllib.request

    url = (
        "https://codeload.github.com/obidose/obidose-paperless-ngx-bulletproof/"
        f"tar.gz/refs/heads/{BRANCH}"
    )
    tmpdir = tempfile.mkdtemp(prefix="paperless-")
    with urllib.request.urlopen(url) as resp:
        with tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz") as tf:
            root = tf.getmembers()[0].name.split("/", 1)[0]
            tf.extractall(tmpdir)
    repo = os.path.join(tmpdir, root)
    os.chdir(repo)
    sys.path.insert(0, repo)


# Try to import locally, bootstrap if needed
try:
    from lib.manager import PaperlessManager
    from lib.installer import common, deps, files, pcloud
except ModuleNotFoundError:
    _bootstrap()
    from lib.manager import PaperlessManager
    from lib.installer import common, deps, files, pcloud


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
        default_env = Path("/home/docker/paperless-setup/.env")
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
