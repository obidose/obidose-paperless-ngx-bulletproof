"""
Traefik management - system-wide reverse proxy for all instances.
"""
import subprocess
from pathlib import Path
from .common import say, ok, warn, die


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
    
    # Start Traefik container
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
            "-e", "DOCKER_API_VERSION=1.44",
            "traefik:v3.0",
            "--providers.docker=true",
            "--providers.docker.exposedbydefault=false",
            "--providers.docker.network=traefik",
            "--entrypoints.web.address=:80",
            "--entrypoints.websecure.address=:443",
            "--entrypoints.web.http.redirections.entrypoint.to=websecure",
            "--entrypoints.web.http.redirections.entrypoint.scheme=https",
            f"--certificatesresolvers.letsencrypt.acme.email={email}",
            "--certificatesresolvers.letsencrypt.acme.storage=/acme.json",
            "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web",
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
