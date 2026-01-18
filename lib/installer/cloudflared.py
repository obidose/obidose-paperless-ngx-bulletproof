"""
Cloudflare Tunnel management - secure tunnels without exposing ports.
"""
import subprocess
import json
from pathlib import Path
from .common import say, ok, warn, die


def is_cloudflared_installed() -> bool:
    """Check if cloudflared is installed."""
    try:
        result = subprocess.run(
            ["which", "cloudflared"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except:
        return False


def install_cloudflared() -> bool:
    """Install cloudflared binary."""
    say("Installing cloudflared...")
    try:
        # Download and install cloudflared
        subprocess.run([
            "curl", "-L", "--output", "/tmp/cloudflared.deb",
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb"
        ], check=True)
        
        subprocess.run(["dpkg", "-i", "/tmp/cloudflared.deb"], check=True)
        subprocess.run(["rm", "/tmp/cloudflared.deb"], check=False)
        
        ok("Cloudflared installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"Failed to install cloudflared: {e}")
        return False


def is_authenticated() -> bool:
    """Check if cloudflared is authenticated."""
    cert_file = Path.home() / ".cloudflared" / "cert.pem"
    return cert_file.exists()


def authenticate() -> bool:
    """Authenticate with Cloudflare."""
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
    except:
        return []


def get_tunnel_for_instance(instance_name: str) -> dict | None:
    """Get tunnel info for a specific instance."""
    tunnels = list_tunnels()
    for tunnel in tunnels:
        if tunnel.get("name") == f"paperless-{instance_name}":
            return tunnel
    return None


def create_tunnel(instance_name: str, domain: str) -> bool:
    """Create a Cloudflare tunnel for an instance."""
    tunnel_name = f"paperless-{instance_name}"
    
    say(f"Creating Cloudflare tunnel: {tunnel_name}")
    
    try:
        # Create tunnel
        subprocess.run([
            "cloudflared", "tunnel", "create", tunnel_name
        ], check=True)
        
        # Get tunnel info
        tunnels = list_tunnels()
        tunnel = None
        for t in tunnels:
            if t.get("name") == tunnel_name:
                tunnel = t
                break
        
        if not tunnel:
            warn("Tunnel created but couldn't find it in list")
            return False
        
        tunnel_id = tunnel.get("id")
        
        # Create config directory
        config_dir = Path("/etc/cloudflared")
        config_dir.mkdir(parents=True, exist_ok=True)
        
        # Create config file
        config_file = config_dir / f"{instance_name}.yml"
        config_content = f"""tunnel: {tunnel_id}
credentials-file: /root/.cloudflared/{tunnel_id}.json

ingress:
  - hostname: {domain}
    service: http://localhost:8000
  - service: http_status:404
"""
        config_file.write_text(config_content)
        
        # Create DNS record
        say(f"Creating DNS record for {domain}")
        subprocess.run([
            "cloudflared", "tunnel", "route", "dns", tunnel_name, domain
        ], check=True)
        
        ok(f"Cloudflare tunnel created for {domain}")
        say(f"To start: cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run")
        return True
        
    except subprocess.CalledProcessError as e:
        warn(f"Failed to create tunnel: {e}")
        return False


def delete_tunnel(instance_name: str) -> bool:
    """Delete a Cloudflare tunnel."""
    tunnel_name = f"paperless-{instance_name}"
    
    try:
        # Delete tunnel
        subprocess.run([
            "cloudflared", "tunnel", "delete", "-f", tunnel_name
        ], check=True)
        
        # Remove config file
        config_file = Path(f"/etc/cloudflared/{instance_name}.yml")
        if config_file.exists():
            config_file.unlink()
        
        ok(f"Cloudflare tunnel {tunnel_name} deleted")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"Failed to delete tunnel: {e}")
        return False
