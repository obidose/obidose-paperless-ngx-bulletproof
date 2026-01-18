"""
Tailscale management - private network access.
"""
import subprocess
from pathlib import Path
from .common import say, ok, warn, die


def is_tailscale_installed() -> bool:
    """Check if Tailscale is installed."""
    try:
        result = subprocess.run(
            ["which", "tailscale"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except:
        return False


def install_tailscale() -> bool:
    """Install Tailscale."""
    say("Installing Tailscale...")
    try:
        # Download and run Tailscale installer
        subprocess.run([
            "curl", "-fsSL", "https://tailscale.com/install.sh"
        ], capture_output=True, text=True, check=True)
        
        subprocess.run([
            "sh", "-c",
            "curl -fsSL https://tailscale.com/install.sh | sh"
        ], check=True)
        
        ok("Tailscale installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"Failed to install Tailscale: {e}")
        return False


def is_connected() -> bool:
    """Check if Tailscale is connected."""
    try:
        result = subprocess.run(
            ["tailscale", "status"],
            capture_output=True,
            text=True,
            check=False
        )
        # If status command succeeds and doesn't show "Logged out", we're connected
        return result.returncode == 0 and "Logged out" not in result.stdout
    except:
        return False


def get_status() -> str:
    """Get Tailscale status."""
    try:
        result = subprocess.run(
            ["tailscale", "status"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except:
        return "Unable to get Tailscale status"


def get_ip() -> str | None:
    """Get Tailscale IP address."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except:
        return None


def connect() -> bool:
    """Connect to Tailscale (authenticate)."""
    say("Starting Tailscale authentication...")
    say("A browser window will open for you to log in")
    try:
        subprocess.run(["tailscale", "up"], check=True)
        ok("Successfully connected to Tailscale")
        
        ip = get_ip()
        if ip:
            say(f"Your Tailscale IP: {ip}")
        
        return True
    except subprocess.CalledProcessError:
        warn("Failed to connect to Tailscale")
        return False


def disconnect() -> bool:
    """Disconnect from Tailscale."""
    try:
        subprocess.run(["tailscale", "down"], check=True)
        ok("Disconnected from Tailscale")
        return True
    except subprocess.CalledProcessError:
        warn("Failed to disconnect from Tailscale")
        return False


def enable_https() -> bool:
    """Enable Tailscale HTTPS certificates."""
    say("Enabling Tailscale HTTPS certificates...")
    try:
        subprocess.run(["tailscale", "cert", "--help"], check=True)
        ok("Tailscale HTTPS is available")
        say("Use 'tailscale cert <hostname>' to get certificates for your instances")
        return True
    except subprocess.CalledProcessError:
        warn("Tailscale HTTPS not available")
        return False
