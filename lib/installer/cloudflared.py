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


def get_base_domain() -> str | None:
    """Extract base domain from existing Cloudflare tunnel configs."""
    config_dir = Path("/etc/cloudflared")
    if not config_dir.exists():
        return None
    
    for config_file in config_dir.glob("*.yml"):
        try:
            content = config_file.read_text()
            for line in content.splitlines():
                if "hostname:" in line:
                    domain = line.split("hostname:")[1].strip()
                    if domain and "." in domain:
                        # Extract base domain (e.g., "cft.dromey.co.uk" -> "dromey.co.uk")
                        parts = domain.split(".")
                        if len(parts) >= 2:
                            # Handle TLDs like .co.uk, .com.au
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


def create_tunnel(instance_name: str, domain: str) -> bool:
    """Create a Cloudflare tunnel for an instance."""
    tunnel_name = f"paperless-{instance_name}"
    
    say(f"Setting up Cloudflare tunnel: {tunnel_name}")
    
    try:
        # Check existing tunnel
        tunnel = get_tunnel_for_instance(instance_name)
        if not tunnel:
            # Create tunnel
            try:
                subprocess.run(["cloudflared", "tunnel", "create", tunnel_name], check=True)
            except subprocess.CalledProcessError as e:
                # If already exists, continue
                warn(f"Create failed or already exists: {e}")
            tunnel = get_tunnel_for_instance(instance_name)
            if not tunnel:
                warn("Tunnel not found after creation attempt")
                return False
        
        tunnel_id = tunnel.get("id")
        
        # Create config file pointing to the tunnel
        config_dir = Path("/etc/cloudflared")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / f"{instance_name}.yml"
        config_content = f"""tunnel: {tunnel_id}
credentials-file: /root/.cloudflared/{tunnel_id}.json

ingress:
  - hostname: {domain}
    service: http://localhost:8000
  - service: http_status:404
"""
        config_file.write_text(config_content)
        
        # Create or ensure DNS record
        say(f"Ensuring DNS record for {domain}")
        result = subprocess.run(
            ["cloudflared", "tunnel", "route", "dns", tunnel_name, domain],
            capture_output=True,
            text=True,
            check=False
        )
        
        # If failed because record exists, use --force to overwrite
        if result.returncode != 0 and "already exists" in result.stderr:
            say(f"DNS record exists, forcing update to new tunnel...")
            result = subprocess.run(
                ["cloudflared", "tunnel", "route", "dns", "-f", tunnel_name, domain],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                warn(f"Could not update DNS automatically. Please delete the existing CNAME record for {domain} in Cloudflare dashboard, then re-run setup.")
                warn(f"Error: {result.stderr.strip()}")
        
        ok(f"Cloudflare tunnel ready for {domain}")
        say(f"To start: cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run")
        return True
        
    except Exception as e:
        warn(f"Failed to set up tunnel: {e}")
        return False


def delete_tunnel(instance_name: str) -> bool:
    """Delete a Cloudflare tunnel."""
    tunnel_name = f"paperless-{instance_name}"
    
    try:
        # Delete tunnel (ignore errors if it was already removed)
        subprocess.run(["cloudflared", "tunnel", "delete", "-f", tunnel_name], check=False)
        
        # Remove config file
        config_file = Path(f"/etc/cloudflared/{instance_name}.yml")
        if config_file.exists():
            config_file.unlink()
        
        ok(f"Cloudflare tunnel {tunnel_name} deleted")
        return True
    except subprocess.CalledProcessError as e:
        warn(f"Failed to delete tunnel: {e}")
        return False


def update_tunnel_dns(instance_name: str, domain: str) -> bool:
    """Update DNS record to point to the tunnel (fixes stale DNS)."""
    tunnel_name = f"paperless-{instance_name}"
    
    tunnel = get_tunnel_for_instance(instance_name)
    if not tunnel:
        warn(f"No tunnel found for instance {instance_name}")
        return False
    
    say(f"Updating DNS for {domain} to point to tunnel {tunnel_name}...")
    
    # Use -f (force) flag to overwrite existing DNS record
    result = subprocess.run(
        ["cloudflared", "tunnel", "route", "dns", "-f", tunnel_name, domain],
        capture_output=True,
        text=True,
        check=False
    )
    
    if result.returncode == 0:
        ok(f"DNS record updated for {domain}")
        return True
    else:
        warn(f"Failed to update DNS: {result.stderr}")
        warn("You may need to manually delete the CNAME record in Cloudflare dashboard")
        return False


def is_tunnel_service_running(instance_name: str) -> bool:
    """Check if the tunnel systemd service is running."""
    result = subprocess.run(
        ["systemctl", "is-active", f"cloudflared-{instance_name}"],
        capture_output=True,
        text=True,
        check=False
    )
    return result.stdout.strip() == "active"


def restart_tunnel_service(instance_name: str) -> bool:
    """Restart the tunnel systemd service."""
    try:
        subprocess.run(["systemctl", "restart", f"cloudflared-{instance_name}"], check=True)
        ok(f"Tunnel service cloudflared-{instance_name} restarted")
        return True
    except subprocess.CalledProcessError:
        warn(f"Failed to restart tunnel service")
        return False