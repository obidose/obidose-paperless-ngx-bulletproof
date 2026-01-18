"""
Tailscale management - private network access with Tailscale Serve.

Tailscale Serve exposes local services to your tailnet with automatic HTTPS.
Example: https://myserver.tail12345.ts.net/paperless
"""
import subprocess
import json
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


def get_hostname() -> str | None:
    """Get the Tailscale hostname (e.g., myserver.tail12345.ts.net)."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(result.stdout)
        dns_name = data.get("Self", {}).get("DNSName", "")
        # Remove trailing dot if present
        return dns_name.rstrip(".") if dns_name else None
    except:
        return None


def get_serve_config() -> dict | None:
    """Get current Tailscale Serve configuration."""
    try:
        result = subprocess.run(
            ["tailscale", "serve", "status", "--json"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return {}
    except:
        return None


def list_serve_paths() -> list[tuple[str, str, int]]:
    """List all Tailscale Serve paths. Returns [(path, target, port), ...]"""
    config = get_serve_config()
    if not config:
        return []
    
    paths = []
    # Parse the serve config structure
    tcp_config = config.get("TCP", {})
    web_config = config.get("Web", {})
    
    # Check web handlers (HTTPS paths)
    for host_port, handlers in web_config.items():
        for path, handler in handlers.get("Handlers", {}).items():
            proxy = handler.get("Proxy", "")
            if proxy:
                # Extract port from proxy URL like "http://127.0.0.1:8000"
                try:
                    port = int(proxy.split(":")[-1])
                    paths.append((path, proxy, port))
                except:
                    paths.append((path, proxy, 0))
    
    return paths


def add_serve(path: str, port: int, https: bool = True) -> bool:
    """
    Add a Tailscale Serve path.
    
    Example: add_serve("/paperless", 8000)
    Creates: https://myserver.tail12345.ts.net/paperless -> localhost:8000
    """
    say(f"Adding Tailscale Serve: {path} -> localhost:{port}")
    try:
        cmd = ["tailscale", "serve"]
        if https:
            cmd.append("--https=443")
        cmd.extend([f"--set-path={path}", f"http://127.0.0.1:{port}"])
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            # Try simpler syntax for older versions
            cmd = ["tailscale", "serve", "--bg"]
            if path != "/":
                cmd.extend([f"--set-path={path}"])
            cmd.append(f"http://127.0.0.1:{port}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            # Even simpler - just the port
            cmd = ["tailscale", "serve", "--bg", str(port)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if result.returncode == 0:
            hostname = get_hostname()
            if hostname:
                ok(f"Tailscale Serve configured: https://{hostname}{path}")
            else:
                ok(f"Tailscale Serve configured for path {path}")
            return True
        else:
            warn(f"Failed to configure serve: {result.stderr}")
            return False
    except Exception as e:
        warn(f"Failed to add Tailscale Serve: {e}")
        return False


def remove_serve(path: str) -> bool:
    """Remove a Tailscale Serve path."""
    say(f"Removing Tailscale Serve path: {path}")
    try:
        # Try to remove specific path
        result = subprocess.run(
            ["tailscale", "serve", "--remove", f"--set-path={path}"],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            # Try reset all
            result = subprocess.run(
                ["tailscale", "serve", "reset"],
                capture_output=True,
                text=True,
                check=False
            )
        
        if result.returncode == 0:
            ok(f"Removed Tailscale Serve path: {path}")
            return True
        else:
            warn(f"Failed to remove serve path: {result.stderr}")
            return False
    except Exception as e:
        warn(f"Failed to remove Tailscale Serve: {e}")
        return False


def reset_serve() -> bool:
    """Reset all Tailscale Serve configuration."""
    try:
        result = subprocess.run(
            ["tailscale", "serve", "reset"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            ok("Tailscale Serve configuration reset")
            return True
        return False
    except:
        return False


def get_serve_url(path: str = "/") -> str | None:
    """Get the full Tailscale Serve URL for a path."""
    hostname = get_hostname()
    if hostname:
        return f"https://{hostname}{path}"
    return None


def connect() -> bool:
    """Connect to Tailscale (authenticate)."""
    say("Starting Tailscale authentication...")
    say("A browser window will open for you to log in")
    try:
        subprocess.run(["tailscale", "up"], check=True)
        ok("Successfully connected to Tailscale")
        
        ip = get_ip()
        hostname = get_hostname()
        if hostname:
            say(f"Your Tailscale hostname: {hostname}")
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


def is_serve_available() -> bool:
    """Check if Tailscale Serve is available (requires paid plan or beta)."""
    try:
        result = subprocess.run(
            ["tailscale", "serve", "--help"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except:
        return False


def is_funnel_available() -> bool:
    """Check if Tailscale Funnel is available (public internet exposure)."""
    try:
        result = subprocess.run(
            ["tailscale", "funnel", "--help"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except:
        return False
