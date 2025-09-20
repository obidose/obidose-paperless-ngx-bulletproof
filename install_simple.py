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


def run_command(cmd: list[str], description: str = None) -> bool:
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