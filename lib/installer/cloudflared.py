"""
Cloudflare Tunnel management - secure tunnels without exposing ports.

Uses containerized cloudflared for running tunnels (token-based auth).
The cloudflared binary is only needed for initial setup/management.
"""
import subprocess
import json
from pathlib import Path
from .common import say, ok, warn, die


def is_cloudflared_installed() -> bool:
    """Check if cloudflared CLI is installed (needed for tunnel management)."""
    try:
        result = subprocess.run(
            ["which", "cloudflared"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except Exception:
        return False


def install_cloudflared() -> bool:
    """Install cloudflared binary (for management commands only)."""
    say("Installing cloudflared CLI...")
    try:
        subprocess.run([
            "curl", "-L", "--output", "/tmp/cloudflared.deb",
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb"
        ], check=True)
        
        subprocess.run(["dpkg", "-i", "/tmp/cloudflared.deb"], check=True)
        subprocess.run(["rm", "/tmp/cloudflared.deb"], check=False)
        
        ok("Cloudflared CLI installed")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"Failed to install cloudflared: {e}")
        return False


def is_authenticated() -> bool:
    """Check if cloudflared is authenticated with Cloudflare."""
    cert_file = Path.home() / ".cloudflared" / "cert.pem"
    return cert_file.exists()


def authenticate() -> bool:
    """Authenticate with Cloudflare (opens browser)."""
    say("Opening browser for Cloudflare authentication...")
    say("You'll need to log in to your Cloudflare account")
    try:
        subprocess.run(["cloudflared", "tunnel", "login"], check=True)
        ok("Successfully authenticated with Cloudflare")
        return True
    except subprocess.CalledProcessError:
        warn("Authentication failed or was cancelled")
        return False


def list_tunnels() -> list[dict]:
    """List all Cloudflare tunnels."""
    try:
        result = subprocess.run(
            ["cloudflared", "tunnel", "list", "--output", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
        return []
    except Exception:
        return []


def get_base_domain() -> str | None:
    """Extract base domain from existing tunnel configs or .env files."""
    # Check existing tunnel configs
    config_dir = Path("/etc/cloudflared")
    if config_dir.exists():
        for config_file in config_dir.glob("*.yml"):
            try:
                content = config_file.read_text()
                for line in content.splitlines():
                    if "hostname:" in line:
                        domain = line.split("hostname:")[1].strip()
                        if domain and "." in domain:
                            parts = domain.split(".")
                            if len(parts) >= 2:
                                if len(parts) >= 3 and len(parts[-2]) <= 3:
                                    return ".".join(parts[-3:])
                                return ".".join(parts[-2:])
            except Exception:
                continue
    return None


def get_tunnel_for_instance(instance_name: str) -> dict | None:
    """Get tunnel info for a specific instance."""
    tunnels = list_tunnels()
    for tunnel in tunnels:
        if tunnel.get("name") == f"paperless-{instance_name}":
            return tunnel
    return None


def get_tunnel_token(tunnel_name: str) -> str | None:
    """Get the connector token for a tunnel (used by container)."""
    try:
        result = subprocess.run(
            ["cloudflared", "tunnel", "token", tunnel_name],
            capture_output=True,
            text=True,
            check=True
        )
        token = result.stdout.strip()
        return token if token else None
    except subprocess.CalledProcessError:
        return None


def create_tunnel(instance_name: str, domain: str, port: int = 8000) -> str | None:
    """
    Create a Cloudflare tunnel for an instance.
    
    Returns the tunnel token on success, None on failure.
    The token should be stored in .env and used by the container.
    """
    tunnel_name = f"paperless-{instance_name}"
    
    say(f"Setting up Cloudflare tunnel: {tunnel_name}")
    
    try:
        # Check/create tunnel
        tunnel = get_tunnel_for_instance(instance_name)
        if not tunnel:
            result = subprocess.run(
                ["cloudflared", "tunnel", "create", tunnel_name],
                capture_output=True, text=True, check=False
            )
            if result.returncode != 0 and "already exists" not in result.stderr:
                warn(f"Failed to create tunnel: {result.stderr}")
                return None
            tunnel = get_tunnel_for_instance(instance_name)
            if not tunnel:
                warn("Tunnel not found after creation")
                return None
        
        # Create/update DNS record
        say(f"Configuring DNS for {domain}")
        result = subprocess.run(
            ["cloudflared", "tunnel", "route", "dns", "-f", tunnel_name, domain],
            capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            warn(f"DNS routing may need manual setup: {result.stderr.strip()}")
        
        # Get the token for container use
        token = get_tunnel_token(tunnel_name)
        if not token:
            warn("Could not get tunnel token")
            return None
        
        ok(f"Cloudflare tunnel ready for {domain}")
        return token
        
    except Exception as e:
        warn(f"Failed to set up tunnel: {e}")
        return None


def delete_tunnel(instance_name: str) -> bool:
    """Delete a Cloudflare tunnel."""
    tunnel_name = f"paperless-{instance_name}"
    
    try:
        # Force delete tunnel (removes connections too)
        subprocess.run(
            ["cloudflared", "tunnel", "delete", "-f", tunnel_name],
            check=False, capture_output=True
        )
        
        ok(f"Cloudflare tunnel {tunnel_name} deleted")
        return True
    except Exception as e:
        warn(f"Failed to delete tunnel: {e}")
        return False


def is_tunnel_running(instance_name: str) -> bool:
    """Check if the tunnel container is running."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "-f", f"name=paperless-{instance_name}-cloudflared"],
            capture_output=True, text=True, check=False
        )
        return bool(result.stdout.strip())
    except Exception:
        return False
