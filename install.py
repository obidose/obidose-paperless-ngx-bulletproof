#!/usr/bin/env python3
"""Lightweight installer for Paperless-ngx Bulletproof CLI.

This installer:
1. Installs basic prerequisites (Docker, rclone) 
2. Downloads and installs the bulletproof CLI with all modules
3. Launches bulletproof for configuration
"""

import os
import sys
import shutil
import subprocess
import urllib.request
from pathlib import Path


def _parse_branch() -> str:
    """Parse branch from command line or environment."""
    import argparse
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


def run_command(cmd: list, capture_output=False) -> bool:
    """Run a command and return success status."""
    try:
        if capture_output:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.returncode == 0
        else:
            result = subprocess.run(cmd, check=True)
            return result.returncode == 0
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False


def install_prerequisites() -> None:
    """Install basic system packages."""
    say("Installing prerequisites...")
    
    say("Updating package lists")
    if not run_command(["apt", "update"]):
        die("Failed to update package lists")
    
    say("Installing basic packages")
    basic_packages = ["curl", "wget", "unzip", "cron", "lsb-release", "ca-certificates", "gnupg"]
    if not run_command(["apt", "install", "-y"] + basic_packages):
        die("Failed to install basic packages")


def ensure_docker_user() -> None:
    """Ensure docker user exists."""
    if run_command(["id", "docker"], capture_output=True):
        ok("User 'docker' already exists")
    else:
        say("Creating 'docker' user...")
        if not run_command(["useradd", "-r", "-s", "/bin/false", "docker"]):
            die("Failed to create docker user")
        ok("Created 'docker' user")


def install_docker() -> None:
    """Install Docker if not present."""
    if run_command(["docker", "--version"], capture_output=True):
        ok("Docker already installed")
        return
    
    say("Installing Docker...")
    
    # Add Docker GPG key
    run_command(["mkdir", "-p", "/usr/share/keyrings"])
    cmd = ["curl", "-fsSL", "https://download.docker.com/linux/ubuntu/gpg"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as p1:
        subprocess.run(["gpg", "--dearmor", "-o", "/usr/share/keyrings/docker-archive-keyring.gpg"], 
                      stdin=p1.stdout, check=True)
    
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
    if run_command(["rclone", "version"], capture_output=True):
        ok("rclone already installed")
        return
        
    say("Installing rclone...")
    
    # Use rclone's official install script
    cmd = ["curl", "https://rclone.org/install.sh"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as p1:
        subprocess.run(["bash"], stdin=p1.stdout, check=True)
    
    ok("rclone installed successfully")


def download_and_install_bulletproof() -> None:
    """Download the latest bulletproof CLI and install it."""
    say("Installing bulletproof CLI...")
    
    # Create bulletproof directory for modules
    bulletproof_dir = "/usr/local/lib/bulletproof"
    os.makedirs(bulletproof_dir, exist_ok=True)
    
    # List of files to download
    files_to_download = [
        "tools/bulletproof.py",
        "tools/ui.py", 
        "tools/cloud_storage.py",
        "tools/instance.py",
        "tools/backup_restore.py"
    ]
    
    try:
        # Download all required files
        for file_path in files_to_download:
            url = f"https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}/{file_path}"
            
            with urllib.request.urlopen(url) as response:
                content = response.read().decode('utf-8')
            
            # Determine destination
            if file_path == "tools/bulletproof.py":
                # Main script goes to /usr/local/bin/bulletproof
                dest_path = "/usr/local/bin/bulletproof"
                chmod_mode = 0o755
            else:
                # Modules go to /usr/local/lib/bulletproof/
                filename = os.path.basename(file_path)
                dest_path = os.path.join(bulletproof_dir, filename)
                chmod_mode = 0o644
            
            with open(dest_path, "w") as f:
                f.write(content)
            
            os.chmod(dest_path, chmod_mode)
        
        # Update the main bulletproof script to add the module directory to Python path
        say("Configuring module imports...")
        with open("/usr/local/bin/bulletproof", "r") as f:
            content = f.read()
        
        # Find where 'import sys' is located
        lines = content.split('\n')
        sys_import_line = -1
        for i, line in enumerate(lines):
            if line.strip() == 'import sys':
                sys_import_line = i
                break
        
        if sys_import_line >= 0:
            # Insert after the existing 'import sys' line
            insert_index = sys_import_line + 1
            lines.insert(insert_index, "sys.path.insert(0, '/usr/local/lib/bulletproof')")
            lines.insert(insert_index + 1, '')  # Empty line for spacing
            
            with open("/usr/local/bin/bulletproof", "w") as f:
                f.write('\n'.join(lines))
            
            # Verify the modification worked
            with open("/usr/local/bin/bulletproof", "r") as f:
                final_content = f.read()
            
            if "sys.path.insert(0, '/usr/local/lib/bulletproof')" in final_content:
                ok("Module path configured successfully")
            else:
                die("Failed to configure module path")
                
            if "_safely_delete_instance" in final_content:
                ok("Enhanced instance management functions available")
            else:
                die("Enhanced functions missing - installation incomplete")
        else:
            die("Could not find 'import sys' line in bulletproof script")
        
        ok("bulletproof CLI installed to /usr/local/bin/bulletproof")
        ok(f"Supporting modules installed to {bulletproof_dir}")
        
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
        print("  1. Run 'bulletproof' to set up cloud storage and manage instances")
        print("  2. Use 'bulletproof --help' to see all available options")
        print()
        
        # Check if we're running in a pipe (like curl | python)
        if not sys.stdin.isatty():
            say("Installation completed via pipe - run 'bulletproof' manually to start")
            return
        
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
