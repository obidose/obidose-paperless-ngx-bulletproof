"""
Cloud storage setup and management for the Paperless-ngx bulletproof tool.

This module handles all rclone configuration and cloud storage interactions,
including pCloud OAuth, WebDAV setup, and remote validation.
"""

import os
import subprocess
import time
from ui import say, ok, warn, error, _read


def _get_tty_path() -> str:
    """Get the path to the controlling TTY."""
    try:
        result = subprocess.run(["tty"], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    # Fall back to direct TTY access
    try:
        with open("/dev/tty", "r"):
            return "/dev/tty"
    except (OSError, IOError):
        # Last resort - return empty string if no TTY available
        return ""


def _pcloud_prompt(text: str) -> str:
    """Enhanced prompt function that works with TTY detection."""
    tty_path = _get_tty_path()
    if tty_path:
        try:
            with open(tty_path, "r+") as tty:
                tty.write(f"{text}: ")
                tty.flush()
                return tty.readline().strip()
        except (OSError, IOError):
            pass
    
    # Fall back to regular input
    return _read(f"{text}: ")


def _sanitize_oneline(text: str) -> str:
    """Sanitize input to single line."""
    return text.replace('\n', ' ').replace('\r', ' ').strip()


def _timeout(seconds: int, cmd: list[str]) -> bool:
    """Run command with timeout."""
    try:
        subprocess.run(cmd, timeout=seconds, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False


def _pcloud_remote_exists() -> bool:
    """Check if the pCloud remote exists in rclone config."""
    try:
        res = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, check=True)
        remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
        return f"{remote_name}:" in res.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _pcloud_remote_ok() -> bool:
    """Check if the pCloud remote is properly configured and accessible."""
    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    return _timeout(10, ["rclone", "lsd", f"{remote_name}:"])


def _pcloud_create_oauth_remote(token_json: str, host: str) -> bool:
    """Create OAuth-based pCloud remote configuration."""
    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    
    # Clean up existing remote thoroughly
    subprocess.run(["rclone", "config", "delete", remote_name], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    
    # Create new remote with full non-interactive environment
    env = os.environ.copy()
    env.update({
        "RCLONE_CONFIG_PCLOUD_TYPE": "pcloud",
        "RCLONE_CONFIG_PCLOUD_TOKEN": token_json,
        "RCLONE_CONFIG_PCLOUD_HOSTNAME": host,
        "RCLONE_NON_INTERACTIVE": "1",
        "RCLONE_INTERACTIVE": "false"
    })
    
    result = subprocess.run(
        ["rclone", "config", "create", remote_name, "pcloud", 
         f"token={token_json}", f"hostname={host}"],
        env=env,
        capture_output=True,
        text=True,
        check=False
    )
    
    if result.returncode == 0:
        say(f"Created {remote_name} remote with {host}")
        return True
    else:
        warn(f"Failed to create {remote_name} remote: {result.stderr}")
        return False


def _pcloud_set_oauth_token_autoregion(token_json: str) -> bool:
    """Automatically detect the correct pCloud region and set up OAuth token."""
    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    
    # Try Europe first as it's more common for OAuth issues
    regions = [
        ("eapi.pcloud.com", "Europe"),
        ("api.pcloud.com", "Global")
    ]
    
    for host, region_name in regions:
        say(f"Testing {region_name} region ({host})...")
        
        # Create remote and check if successful
        if not _pcloud_create_oauth_remote(token_json, host):
            continue
        
        # Give rclone a moment to process the config
        time.sleep(2)
        
        # Check if remote was created and exists
        if not _pcloud_remote_exists():
            warn(f"{region_name} region: Remote creation failed")
            continue
        
        # Test connection with more detailed output
        env = os.environ.copy()
        env.update({
            "RCLONE_NON_INTERACTIVE": "1",
            "RCLONE_INTERACTIVE": "false"
        })
        
        result = subprocess.run(
            ["rclone", "lsd", f"{remote_name}:", "--timeout", "30s"],
            env=env,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            ok(f"Successfully connected to pCloud {region_name} region!")
            say(f"Remote '{remote_name}' configured with {host}")
            return True
        else:
            if result.stderr:
                # Check for region-specific errors
                stderr_lower = result.stderr.lower()
                if "401" in stderr_lower or "unauthorized" in stderr_lower:
                    warn(f"{region_name} region: Authentication failed - may be wrong region")
                elif "timeout" in stderr_lower:
                    warn(f"{region_name} region: Connection timeout")
                else:
                    warn(f"{region_name} region: {result.stderr.strip()}")
            else:
                warn(f"{region_name} region: Connection test failed (no error details)")
    
    # If both regions failed, provide helpful guidance
    error("Both pCloud regions failed. Please check:")
    say("1. Token is valid and not expired")
    say("2. pCloud account is accessible")
    say("3. Network connectivity is working")
    return False


def _pcloud_webdav_create(email: str, password: str, host: str) -> None:
    """Create WebDAV-based pCloud remote configuration."""
    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    
    subprocess.run(["rclone", "config", "delete", remote_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        [
            "rclone", "config", "create", remote_name, "webdav",
            f"url=https://{host}",
            f"user={email}",
            f"pass={subprocess.run(['rclone', 'obscure', password], capture_output=True, text=True).stdout.strip()}",
            "vendor=other"
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _pcloud_webdav_try_both(email: str, password: str) -> bool:
    """Try WebDAV setup with both pCloud endpoints."""
    hosts = ["webdav.pcloud.com", "ewebdav.pcloud.com"]
    for host in hosts:
        say(f"Trying WebDAV with {host}...")
        _pcloud_webdav_create(email, password, host)
        if _pcloud_remote_ok():
            ok(f"WebDAV configured successfully with {host}")
            return True
    return False


def setup_pcloud_remote() -> bool:
    """Interactive setup for pCloud remote configuration."""
    remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
    
    # Ensure remote name is properly initialized for multi-main mode
    if not remote_name or remote_name == "":
        remote_name = "pcloud"
        os.environ["RCLONE_REMOTE_NAME"] = remote_name
    
    say(f"Setting up cloud storage remote: {remote_name}")
    say("This will configure rclone to access your cloud storage.")
    print()
    say("Choose setup method:")
    print("  1) OAuth (Recommended) - Browser-based authentication")
    print("  2) WebDAV - Username/password authentication")
    print("  3) Try legacy WebDAV")
    print()
    
    choice = _read("Enter choice [1-3]: ").strip()
    
    if choice == "1":
        say("Setting up OAuth authentication...")
        say("1. Open: https://my.pcloud.com/oauth2/authorize?client_id=DnONSzyJXpm&response_type=code&redirect_uri=https://oauth.pcloud.com/oauth_redirect.html")
        say("2. Click 'Allow' to authorize the application")
        say("3. Copy the authorization code from the URL (after 'code=')")
        print()
        
        code = _pcloud_prompt("Authorization code")
        if not code:
            warn("No authorization code provided")
            return False
        
        # Convert to token JSON format expected by rclone
        token_json = f'{{"access_token":"{_sanitize_oneline(code)}","token_type":"bearer"}}'
        
        if _pcloud_set_oauth_token_autoregion(token_json):
            ok("OAuth setup completed successfully!")
            return True
        else:
            warn("OAuth setup failed")
            return False
    
    elif choice == "2":
        say("Setting up WebDAV authentication...")
        print()
        email = _pcloud_prompt("pCloud email")
        if not email:
            warn("Email is required")
            return False
        
        # Try to use TTY for password input if available
        tty_path = _get_tty_path()
        if tty_path:
            try:
                import getpass
                password = getpass.getpass("pCloud password: ")
            except (ImportError, Exception):
                password = _pcloud_prompt("pCloud password")
        else:
            password = _pcloud_prompt("pCloud password")
        
        if not password:
            warn("Password is required")
            return False
        
        if _pcloud_webdav_try_both(email, password):
            ok("WebDAV setup completed successfully!")
            return True
        else:
            warn("WebDAV setup failed with both endpoints")
            return False
    
    elif choice == "3":
        warn("Legacy WebDAV option not implemented")
        return False
    
    else:
        warn("Invalid choice")
        return False


def cmd_setup_pcloud(args) -> None:
    """Command handler for cloud storage setup."""
    if setup_pcloud_remote():
        ok("Cloud storage setup completed!")
    else:
        error("Cloud storage setup failed!")