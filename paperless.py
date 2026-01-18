#!/usr/bin/env python3
"""
Paperless-NGX Bulletproof - Unified Entry Point

Single command for everything: install, manage, backup, restore, health check.
Can be run on fresh machine or existing installation.

Usage:
  # Fresh machine (downloads and runs)
  curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/paperless.py | sudo python3 -

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
    from paperless_manager import PaperlessManager
    from installer import common, deps, files, pcloud
except ModuleNotFoundError:
    _bootstrap()
    from paperless_manager import PaperlessManager
    from installer import common, deps, files, pcloud


def main():
    """Main entry point."""
    # If stdin is not a terminal (piped execution), reopen it to the controlling terminal
    if not sys.stdin.isatty():
        try:
            sys.stdin = open('/dev/tty', 'r')
        except:
            print("ERROR: Cannot access terminal for interactive input.")
            print("Please download and run the script directly:")
            print(f"  wget https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}/paperless.py")
            print(f"  sudo python3 paperless.py")
            sys.exit(1)
    
    # Check if we're on a fresh machine (no instances configured)
    from pathlib import Path
    
    config_file = Path("/etc/paperless-bulletproof/instances.json")
    is_fresh = not config_file.exists()
    
    # Check for existing default installation
    default_env = Path("/home/docker/paperless-setup/.env")
    has_default = default_env.exists()
    
    if is_fresh and not has_default:
        # Completely fresh machine - offer quick setup
        print("\n" + "="*70)
        print("  Welcome to Paperless-NGX Bulletproof!")
        print("="*70)
        print("\nThis appears to be a fresh installation.")
        print("\nOptions:")
        print("  1) Quick setup (guided installation)")
        print("  2) Advanced options (manual configuration)")
        print("  3) Restore from existing backup")
        print()
        
        choice = input("Choose [1-3] [1]: ").strip() or "1"
        
        if choice == "1":
            # Run full installer
            print("\n Starting guided installation...\n")
            if os.geteuid() != 0:
                print("ERROR: Installation requires root privileges. Please run with sudo.")
                sys.exit(1)
            
            common.say(f"Fetching assets from branch '{BRANCH}'")
            common.preflight_ubuntu()
            deps.install_prereqs()
            deps.ensure_user()
            deps.install_docker()
            deps.install_rclone()
            pcloud.ensure_pcloud_remote_or_menu()
            common.ensure_dir_tree(common.cfg)
            
            if files.restore_existing_backup_if_present():
                files.copy_helper_scripts()
                files.install_cron_backup()
                files.show_status()
                return
            
            common.pick_and_merge_preset(
                f"https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}"
            )
            common.prompt_core_values()
            common.prompt_backup_plan()
            common.ensure_dir_tree(common.cfg)
            files.write_env_file()
            files.write_compose_file()
            files.copy_helper_scripts()
            files.bring_up_stack()
            
            from utils.selftest import run_stack_tests
            if run_stack_tests(Path(common.cfg.compose_file), Path(common.cfg.env_file)):
                common.ok("Self-test passed")
            else:
                common.warn("Self-test failed; check container logs")
            
            files.install_cron_backup()
            files.show_status()
            
            print("\n" + "="*70)
            print("  Installation complete! You can now run: paperless")
            print("="*70 + "\n")
            return
            
        elif choice == "3":
            # Restore from backup
            print("\n Starting restore wizard...\n")
            if os.geteuid() != 0:
                print("ERROR: Restore requires root privileges. Please run with sudo.")
                sys.exit(1)
            
            common.say("Checking for backups...")
            deps.install_rclone()
            pcloud.ensure_pcloud_remote_or_menu()
            
            if files.restore_existing_backup_if_present():
                files.copy_helper_scripts()
                files.install_cron_backup()
                files.show_status()
                return
            else:
                print("\nNo backups found. Would you like to run a fresh installation instead?")
                if input("Continue with installation? [Y/n]: ").strip().lower() != 'n':
                    # Fall through to normal manager
                    pass
                else:
                    return
    
    # Launch the manager (fresh or existing installation)
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
