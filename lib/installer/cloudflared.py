"""
Cloudflare Tunnel management - secure tunnels without exposing ports.

Uses containerized cloudflared for running tunnels.
Config and credentials stored per-instance in {data_root}/{instance}/cloudflared/
The cloudflared binary is only needed for initial setup/management.
"""
import subprocess
import json
import shutil
from pathlib import Path
from .common import say, ok, warn, die, cfg


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
    """Extract base domain from existing tunnel configs in instance directories."""
    # Check existing tunnel configs in per-instance directories
    data_root = Path(cfg.data_root)
    if data_root.exists():
        for cf_dir in data_root.glob("*/cloudflared"):
            config_file = cf_dir / "config.yml"
            if config_file.exists():
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


def create_tunnel(instance_name: str, domain: str, port: int = 8000, data_root: str | None = None) -> bool:
    """
    Create a Cloudflare tunnel for an instance.
    
    Creates the tunnel, stores config and credentials in the instance's
    cloudflared/ directory for self-contained backup/restore.
    Returns True on success, False on failure.
    """
    tunnel_name = f"paperless-{instance_name}"
    
    # Use provided data_root or fall back to cfg
    root = data_root or cfg.data_root
    instance_cf_dir = Path(root) / instance_name / "cloudflared"
    
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
                return False
            tunnel = get_tunnel_for_instance(instance_name)
            if not tunnel:
                warn("Tunnel not found after creation")
                return False
        
        tunnel_id = tunnel.get('id')
        
        # Create per-instance cloudflared directory
        instance_cf_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy credentials file from ~/.cloudflared/ to instance dir
        src_creds = Path.home() / ".cloudflared" / f"{tunnel_id}.json"
        dst_creds = instance_cf_dir / f"{tunnel_id}.json"
        if src_creds.exists():
            shutil.copy2(src_creds, dst_creds)
        else:
            warn(f"Credentials file not found: {src_creds}")
            return False
        
        # Write config file with ingress rules
        # Note: paths are as seen inside the container (/etc/cloudflared)
        config_content = f"""tunnel: {tunnel_id}
credentials-file: /etc/cloudflared/{tunnel_id}.json

ingress:
  - hostname: {domain}
    service: http://paperless:{port}
  - service: http_status:404
"""
        config_file = instance_cf_dir / "config.yml"
        config_file.write_text(config_content)
        
        # Create/update DNS record
        say(f"Configuring DNS for {domain}")
        result = subprocess.run(
            ["cloudflared", "tunnel", "route", "dns", "-f", tunnel_name, domain],
            capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            warn(f"DNS routing may need manual setup: {result.stderr.strip()}")
        
        ok(f"Cloudflare tunnel ready for {domain}")
        return True
        
    except Exception as e:
        warn(f"Failed to set up tunnel: {e}")
        return False


def delete_tunnel(instance_name: str, data_root: str | None = None) -> bool:
    """Delete a Cloudflare tunnel and its local config."""
    tunnel_name = f"paperless-{instance_name}"
    
    # Use provided data_root or fall back to cfg
    root = data_root or cfg.data_root
    instance_cf_dir = Path(root) / instance_name / "cloudflared"
    
    try:
        # Force delete tunnel (removes connections too)
        subprocess.run(
            ["cloudflared", "tunnel", "delete", "-f", tunnel_name],
            check=False, capture_output=True
        )
        
        # Remove local config directory
        if instance_cf_dir.exists():
            shutil.rmtree(instance_cf_dir)
        
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
