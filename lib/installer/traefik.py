"""
Traefik management - system-wide reverse proxy for all instances.
"""
import subprocess
import re
from pathlib import Path
from .common import say, ok, warn, die


def validate_email(email: str) -> bool:
    """Validate email format for Let's Encrypt."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def is_traefik_running() -> bool:
    """Check if system Traefik is running."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=traefik-system", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        return "traefik-system" in result.stdout
    except subprocess.CalledProcessError:
        return False


def get_traefik_email() -> str | None:
    """Get configured Let's Encrypt email from Traefik config."""
    config_file = Path("/opt/traefik/traefik.yml")
    if not config_file.exists():
        return None
    try:
        content = config_file.read_text()
        for line in content.splitlines():
            if "email:" in line:
                return line.split("email:")[1].strip()
    except Exception:
        pass
    return None


def ensure_traefik_network() -> None:
    """Ensure the shared traefik network exists."""
    try:
        # Check if network exists
        result = subprocess.run(
            ["docker", "network", "ls", "--filter", "name=traefik", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True
        )
        if "traefik" not in result.stdout:
            say("Creating shared Traefik network...")
            subprocess.run(["docker", "network", "create", "traefik"], check=True)
            ok("Traefik network created")
    except subprocess.CalledProcessError as e:
        warn(f"Failed to create Traefik network: {e}")


def setup_system_traefik(email: str = "admin@example.com") -> bool:
    """
    Set up system-wide Traefik container.
    This runs once and serves all Paperless instances.
    """
    if is_traefik_running():
        ok("System Traefik already running")
        return True
    
    say("Setting up system-wide Traefik...")
    
    # Ensure network exists
    ensure_traefik_network()
    
    # Create Traefik data directory
    traefik_dir = Path("/opt/traefik")
    traefik_dir.mkdir(parents=True, exist_ok=True)
    
    # Create empty acme.json for Let's Encrypt certificates
    acme_file = traefik_dir / "acme.json"
    if not acme_file.exists():
        acme_file.touch()
        acme_file.chmod(0o600)
    
    # Create Traefik static configuration file
    config_file = traefik_dir / "traefik.yml"
    config_content = f"""
api:
  insecure: false
  dashboard: false

providers:
  docker:
    endpoint: "unix:///var/run/docker.sock"
    exposedByDefault: false
    network: traefik
    watch: true

entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: ":443"

certificatesResolvers:
  letsencrypt:
    acme:
      email: {email}
      storage: /acme.json
      httpChallenge:
        entryPoint: web
"""
    config_file.write_text(config_content.strip())
    
    # Start Traefik container with latest version
    try:
        subprocess.run([
            "docker", "run", "-d",
            "--name", "traefik-system",
            "--network", "traefik",
            "--restart", "unless-stopped",
            "-p", "80:80",
            "-p", "443:443",
            "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
            "-v", f"{acme_file}:/acme.json",
            "-v", f"{config_file}:/traefik.yml:ro",
            "traefik:latest",
        ], check=True)
        
        ok("System Traefik started successfully")
        say("All Paperless instances will share this Traefik")
        return True
        
    except subprocess.CalledProcessError as e:
        warn(f"Failed to start Traefik: {e}")
        return False


def stop_system_traefik() -> None:
    """Stop and remove system Traefik container."""
    if not is_traefik_running():
        return
    
    try:
        subprocess.run(["docker", "stop", "traefik-system"], check=True)
        subprocess.run(["docker", "rm", "traefik-system"], check=True)
        ok("System Traefik stopped")
    except subprocess.CalledProcessError as e:
        warn(f"Failed to stop Traefik: {e}")
