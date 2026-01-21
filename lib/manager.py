#!/usr/bin/env python3
"""
Paperless-NGX Bulletproof Manager
A comprehensive TUI for managing Paperless-NGX instances with backup/restore capabilities.
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Determine branch (set by paperless.py or default to main)
BRANCH = os.environ.get("BP_BRANCH", "main")


# ─── Import from refactored modules ───────────────────────────────────────────

from lib.ui import (
    Colors, colorize, say, ok, warn, error, die,
    create_box_helper, draw_box_top, draw_box_bottom, draw_box_divider,
    draw_section_header, print_header, print_menu
)
from lib.validation import (
    get_input, confirm, is_valid_domain, get_domain_input,
    is_valid_email, get_email_input, is_valid_port, get_port_input,
    is_valid_instance_name, get_instance_name_input
)
from lib.instance import (
    Instance, InstanceManager, load_instance_config, load_backup_env_config,
    is_port_available, is_port_in_use, find_available_port, get_local_ip
)
from lib.health import HealthChecker
from lib.backup_ops import (
    BackupManager, run_restore_with_env, get_backup_size, count_snapshots, delete_snapshot
)


# ─── Shared Instance Setup Helpers ────────────────────────────────────────────

def setup_instance_config(instance_name: str, existing_instances: list[str] = None) -> tuple[bool, str]:
    """
    Set up instance configuration with validation.
    Returns (success, error_message).
    """
    if existing_instances is None:
        existing_instances = []
    
    # Validate instance name
    if instance_name in existing_instances:
        return False, f"Instance '{instance_name}' already exists"
    
    if not instance_name or not instance_name.replace("-", "").replace("_", "").isalnum():
        return False, "Instance name must be alphanumeric (hyphens and underscores allowed)"
    
    # Import and configure
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import common
    
    # Set instance name and compute paths
    common.cfg.instance_name = instance_name
    common.cfg.data_root = f"/home/docker/{instance_name}"
    common.cfg.stack_dir = f"/home/docker/{instance_name}-setup"
    common.cfg.rclone_remote_path = f"backups/paperless/{instance_name}"
    common.cfg.refresh_paths()
    
    return True, ""


def check_networking_dependencies() -> dict[str, bool]:
    """Check availability of networking services. Returns dict of service -> available."""
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import traefik, cloudflared, tailscale
    
    return {
        "traefik_running": traefik.is_traefik_running(),
        "cloudflared_installed": cloudflared.is_cloudflared_installed(),
        "cloudflared_authenticated": cloudflared.is_authenticated(),
        "tailscale_installed": tailscale.is_tailscale_installed(),
        "tailscale_connected": tailscale.is_connected(),
    }


def setup_cloudflare_tunnel(instance_name: str, domain: str, port: int = 8000) -> bool:
    """Set up Cloudflare tunnel for an instance. Returns success status."""
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import cloudflared, common
    
    if not cloudflared.is_authenticated():
        return False
    
    print()
    common.say("Setting up Cloudflare Tunnel...")
    
    if not cloudflared.create_tunnel(instance_name, domain, port):
        common.warn("Failed to create Cloudflare tunnel")
        return False
    
    common.ok(f"Cloudflare tunnel ready for {domain}")
    common.say(f"To start: cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run")
    
    # Start tunnel as systemd service automatically
    try:
        service_content = f"""[Unit]
Description=Cloudflare Tunnel for {instance_name}
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""
        service_file = Path(f"/etc/systemd/system/cloudflared-{instance_name}.service")
        service_file.write_text(service_content)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", f"cloudflared-{instance_name}"], check=True)
        subprocess.run(["systemctl", "start", f"cloudflared-{instance_name}"], check=True)
        common.ok("Tunnel service started")
        return True
    except Exception as e:
        common.warn(f"Failed to create service: {e}")
        return False


def finalize_instance_setup(instance_manager: 'InstanceManager', instance_name: str, 
                           stack_dir: Path, data_root: Path, enable_cloudflared: str, 
                           domain: str, port: int = 8000) -> None:
    """Finalize instance setup - register and set up optional services."""
    sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
    from lib.installer import common, files
    
    # Install backup cron
    files.install_cron_backup()
    
    # Set up Cloudflare tunnel if enabled
    if enable_cloudflared == "yes":
        setup_cloudflare_tunnel(instance_name, domain, port)
    
    # Register instance
    instance_manager.add_instance(instance_name, stack_dir, data_root)


# ─── Main Application ─────────────────────────────────────────────────────────

# ─── Main Application ─────────────────────────────────────────────────────────

class PaperlessManager:
    """Main application controller."""
    
    # Standard library paths for restore operations
    LIB_PATH_INSTALLED = Path("/usr/local/lib/paperless-bulletproof/lib")
    LIB_PATH_DEV = Path(__file__).parent  # For development
    
    def __init__(self):
        self.instance_manager = InstanceManager()
        self.rclone_configured = self._check_rclone_connection()
        # Determine correct lib path (installed or development)
        self.lib_path = self.LIB_PATH_INSTALLED if self.LIB_PATH_INSTALLED.exists() else self.LIB_PATH_DEV
    
    def _check_rclone_connection(self) -> bool:
        """Check if pCloud/rclone is configured."""
        try:
            result = subprocess.run(
                ["rclone", "listremotes"],
                capture_output=True,
                text=True,
                check=False
            )
            return "pcloud:" in result.stdout
        except Exception:
            return False
    
    def run(self) -> None:
        """Run the main menu loop."""
        while True:
            self._scan_system()
            self.show_main_menu()
            choice = get_input("Select option", "")
            
            if choice == "0":
                print("\nGoodbye! 👋\n")
                break
            
            self.handle_main_choice(choice)
    
    def _scan_system(self) -> None:
        """Scan for instances and check backup connection."""
        # Reload instances to pick up any changes
        self.instance_manager.load_instances()
        
        # Check backup connection
        self.rclone_configured = self._check_rclone_connection()
    
    def show_main_menu(self) -> None:
        """Display the main menu."""
        print_header("Paperless-NGX Bulletproof Manager")
        
        # Show backup connection status
        instances = self.instance_manager.list_instances()
        running_count = sum(1 for i in instances if i.is_running)
        stopped_count = len(instances) - running_count
        
        # System overview box - use centralized helper
        box_line, box_width = create_box_helper(80)
        
        print(draw_box_top(box_width))
        
        if self.rclone_configured:
            # Get backup info (count only instance folders with at least one snapshot)
            try:
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                instance_dirs = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
                backed_up_count = 0
                for inst_dir in instance_dirs:
                    check = subprocess.run(
                        ["rclone", "lsd", f"pcloud:backups/paperless/{inst_dir}"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5
                    )
                    if check.stdout.strip():
                        backed_up_count += 1
                
                # Get most recent backup date across all instances
                latest_backup = "none"
                for inst in instances:
                    backup_mgr = BackupManager(inst)
                    snaps = backup_mgr.fetch_snapshots()
                    if snaps:
                        latest_backup = snaps[0][0][:16]  # First is newest after sorting
                        break
                
                backup_status = colorize("✓ Connected", Colors.GREEN)
                backup_detail = f"{backed_up_count} backed up"
                if latest_backup != "none":
                    backup_detail += f" • {latest_backup}"
            except:
                backup_status = colorize("✓ Connected", Colors.GREEN)
                backup_detail = "Ready"
        else:
            backup_status = colorize("⚠ Not connected", Colors.YELLOW)
            backup_detail = "Configure to enable backups"
        
        print(box_line(f" Backup Server:  {backup_status} {backup_detail}"))
        
        # Instances status
        if instances:
            instance_status = f"{running_count} running, {stopped_count} stopped"
            print(box_line(f" Instances:      {len(instances)} total • {instance_status}"))
        else:
            print(box_line(f" Instances:      {colorize('No instances configured', Colors.YELLOW)}"))
        
        # Networking services status
        # Traefik
        from lib.installer.traefik import is_traefik_running, get_traefik_email
        traefik_running = is_traefik_running()
        if traefik_running:
            email = get_traefik_email()
            if email:
                traefik_status = f"{colorize('✓', Colors.GREEN)} Running • {email}"
            else:
                traefik_status = f"{colorize('✓', Colors.GREEN)} Running"
        else:
            traefik_status = colorize("○ Not installed", Colors.CYAN)
        print(box_line(f" Traefik:        {traefik_status}"))
        
        # Cloudflare Tunnel
        from lib.installer.cloudflared import is_cloudflared_installed
        if is_cloudflared_installed():
            # Count tunnels
            try:
                from lib.installer.cloudflared import list_tunnels
                tunnels = list_tunnels()
                tunnel_count = len([t for t in tunnels if t.get('name', '').startswith('paperless-')])
                cloudflared_status = f"{colorize('✓', Colors.GREEN)} Installed • {tunnel_count} tunnel{'s' if tunnel_count != 1 else ''}"
            except:
                cloudflared_status = f"{colorize('✓', Colors.GREEN)} Installed"
        else:
            cloudflared_status = colorize("○ Not installed", Colors.CYAN)
        print(box_line(f" Cloudflare:     {cloudflared_status}"))
        
        # Tailscale
        from lib.installer.tailscale import is_tailscale_installed, is_connected, get_ip
        if is_tailscale_installed():
            if is_connected():
                try:
                    ip = get_ip()
                    tailscale_status = f"{colorize('✓', Colors.GREEN)} Connected • {ip}"
                except:
                    tailscale_status = f"{colorize('✓', Colors.GREEN)} Connected"
            else:
                tailscale_status = f"{colorize('○', Colors.YELLOW)} Installed • Disconnected"
        else:
            tailscale_status = colorize("○ Not installed", Colors.CYAN)
        print(box_line(f" Tailscale:      {tailscale_status}"))
        
        print(draw_box_bottom(box_width))
        print()
        
        # Quick instance list with rich access info
        if instances:
            print(colorize("Active Instances:", Colors.BOLD))
            for instance in instances[:5]:  # Show max 5
                status_icon = colorize("●", Colors.GREEN) if instance.is_running else colorize("○", Colors.YELLOW)
                access_display = instance.get_access_url_display()
                # Format: status icon, name (fixed 20 chars), then access method with URL
                name_padded = f"{instance.name:<20}"
                print(f"  {status_icon} {colorize(name_padded, Colors.BOLD)} {access_display}")
            
            if len(instances) > 5:
                print(f"  {colorize(f'... and {len(instances) - 5} more', Colors.CYAN)}")
            print()
        
        # Main menu options
        options = [
            ("1", colorize("▸", Colors.GREEN) + " Manage Instances" + (f" ({len(instances)})" if instances else "")),
            ("2", colorize("▸", Colors.BLUE) + " Browse Backups" + (" ✓" if self.rclone_configured else " ⚠")),
            ("3", colorize("▸", Colors.MAGENTA) + " System Backup/Restore"),
            ("4", colorize("▸", Colors.CYAN) + " Manage Traefik (HTTPS)"),
            ("5", colorize("▸", Colors.CYAN) + " Manage Cloudflare Tunnel"),
            ("6", colorize("▸", Colors.CYAN) + " Manage Tailscale"),
            ("7", colorize("▸", Colors.YELLOW) + " Configure Backup Server"),
            ("8", colorize("▸", Colors.RED) + " Nuke Setup (Clean Start)"),
            ("0", colorize("◀", Colors.RED) + " Quit")
        ]
        print_menu(options)
    
    def handle_main_choice(self, choice: str) -> None:
        """Handle main menu selection."""
        if choice == "1":
            self.instances_menu()
        elif choice == "2":
            if not self.rclone_configured:
                warn("Backup server not configured!")
                if confirm("Configure now?", True):
                    self.configure_backup_connection()
            else:
                self.backups_menu()
        elif choice == "3":
            self.system_backup_menu()
        elif choice == "4":
            self.traefik_menu()
        elif choice == "5":
            self.cloudflared_menu()
        elif choice == "6":
            self.tailscale_menu()
        elif choice == "7":
            self.configure_backup_connection()
        elif choice == "8":
            self.nuke_setup()
        else:
            warn("Invalid option")
    
    def configure_backup_connection(self) -> None:
        """Configure rclone cloud backup connection with guided setup."""
        while True:
            print_header("Backup Server Configuration")
            
            box_line, box_width = create_box_helper(80)
            
            # Check current status
            current_remote = None
            remote_type = None
            remote_ok = False
            
            try:
                result = subprocess.run(
                    ["rclone", "listremotes"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0:
                    remotes = [r.strip().rstrip(':') for r in result.stdout.splitlines() if r.strip()]
                    if remotes:
                        current_remote = remotes[0]
                        # Get remote type
                        result = subprocess.run(
                            ["rclone", "config", "show", current_remote],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if result.returncode == 0:
                            for line in result.stdout.splitlines():
                                if line.startswith("type = "):
                                    remote_type = line.split("=")[1].strip()
                                    break
                        # Check if working
                        result = subprocess.run(
                            ["rclone", "about", f"{current_remote}:", "--json"],
                            capture_output=True,
                            text=True,
                            timeout=15,
                            check=False
                        )
                        remote_ok = result.returncode == 0
            except:
                pass
            
            # Display info box
            print(draw_box_top(box_width))
            print(box_line(" Backups are stored in the cloud using rclone, which"))
            print(box_line(" supports 70+ cloud storage providers including:"))
            print(box_line(""))
            print(box_line(f"   • {colorize('pCloud', Colors.CYAN)} - Great value, EU/US servers"))
            print(box_line(f"   • {colorize('Google Drive', Colors.CYAN)} - 15GB free"))
            print(box_line(f"   • {colorize('Dropbox', Colors.CYAN)} - 2GB free"))
            print(box_line(f"   • {colorize('OneDrive', Colors.CYAN)} - 5GB free"))
            print(box_line(f"   • {colorize('Backblaze B2', Colors.CYAN)} - 10GB free, cheap storage"))
            print(box_line(f"   • {colorize('Amazon S3', Colors.CYAN)} - Enterprise scalable"))
            print(box_line(f"   • {colorize('SFTP/WebDAV', Colors.CYAN)} - Self-hosted options"))
            print(box_line(""))
            print(draw_section_header("Current Status", box_width))
            
            if current_remote and remote_ok:
                status_icon = colorize("● Connected", Colors.GREEN)
                print(box_line(f" Status:  {status_icon}"))
                print(box_line(f" Remote:  {colorize(current_remote, Colors.CYAN)} ({remote_type or 'unknown'})"))
                
                # Try to get usage info
                try:
                    result = subprocess.run(
                        ["rclone", "about", f"{current_remote}:", "--json"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False
                    )
                    if result.returncode == 0:
                        import json as json_module
                        about = json_module.loads(result.stdout)
                        if "used" in about and "total" in about:
                            used_gb = about["used"] / (1024**3)
                            total_gb = about["total"] / (1024**3)
                            pct = (about["used"] / about["total"]) * 100 if about["total"] > 0 else 0
                            print(box_line(f" Storage: {used_gb:.1f} GB / {total_gb:.1f} GB ({pct:.0f}% used)"))
                except:
                    pass
            elif current_remote:
                status_icon = colorize("● Configured but not responding", Colors.YELLOW)
                print(box_line(f" Status:  {status_icon}"))
                print(box_line(f" Remote:  {colorize(current_remote, Colors.CYAN)} ({remote_type or 'unknown'})"))
            else:
                status_icon = colorize("○ Not configured", Colors.RED)
                print(box_line(f" Status:  {status_icon}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # Menu options
            if current_remote and remote_ok:
                print(f"  {colorize('1)', Colors.BOLD)} Test connection")
                print(f"  {colorize('2)', Colors.BOLD)} View storage usage")
                print(f"  {colorize('3)', Colors.BOLD)} Change backup provider")
                print(f"  {colorize('4)', Colors.BOLD)} Remove configuration")
            else:
                print(f"  {colorize('1)', Colors.BOLD)} {colorize('Set up pCloud', Colors.CYAN)} {colorize('(recommended)', Colors.GREEN)}")
                print(f"  {colorize('2)', Colors.BOLD)} Set up Google Drive")
                print(f"  {colorize('3)', Colors.BOLD)} Set up Dropbox")
                print(f"  {colorize('4)', Colors.BOLD)} Set up other provider (advanced)")
            
            print()
            print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            
            if current_remote and remote_ok:
                # Already configured menu
                if choice == "1":
                    self._test_backup_connection(current_remote)
                elif choice == "2":
                    self._show_storage_usage(current_remote)
                elif choice == "3":
                    if confirm("Replace current backup configuration?", False):
                        self._setup_backup_provider_menu()
                elif choice == "4":
                    if confirm(f"Remove '{current_remote}' configuration? Backups will stop working.", False):
                        subprocess.run(["rclone", "config", "delete", current_remote], check=False)
                        ok("Configuration removed")
                        self.rclone_configured = False
                        input("\nPress Enter to continue...")
            else:
                # Not configured menu
                if choice == "1":
                    self._setup_pcloud()
                elif choice == "2":
                    self._setup_google_drive()
                elif choice == "3":
                    self._setup_dropbox()
                elif choice == "4":
                    self._setup_other_provider()
            
            # Refresh connection status
            self.rclone_configured = self._check_rclone_connection()
    
    def _test_backup_connection(self, remote: str) -> None:
        """Test the backup connection."""
        print()
        say("Testing connection...")
        
        try:
            result = subprocess.run(
                ["rclone", "lsd", f"{remote}:"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            if result.returncode == 0:
                ok("Connection successful!")
                dirs = [line.split()[-1] for line in result.stdout.splitlines() if line.strip()]
                if dirs:
                    say(f"Found {len(dirs)} top-level folders")
            else:
                error(f"Connection failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            error("Connection timed out")
        except Exception as e:
            error(f"Test failed: {e}")
        
        input("\nPress Enter to continue...")
    
    def _show_storage_usage(self, remote: str) -> None:
        """Show storage usage details."""
        print()
        say("Fetching storage information...")
        
        try:
            result = subprocess.run(
                ["rclone", "about", f"{remote}:"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            if result.returncode == 0:
                print()
                print(result.stdout)
            else:
                warn("Could not fetch storage info")
        except Exception as e:
            error(f"Failed: {e}")
        
        input("\nPress Enter to continue...")
    
    def _setup_backup_provider_menu(self) -> None:
        """Show provider selection menu."""
        print()
        print(f"  {colorize('1)', Colors.BOLD)} {colorize('pCloud', Colors.CYAN)} {colorize('(recommended)', Colors.GREEN)}")
        print(f"  {colorize('2)', Colors.BOLD)} Google Drive")
        print(f"  {colorize('3)', Colors.BOLD)} Dropbox")
        print(f"  {colorize('4)', Colors.BOLD)} Other provider")
        print()
        
        choice = get_input("Select provider", "")
        
        if choice == "1":
            self._setup_pcloud()
        elif choice == "2":
            self._setup_google_drive()
        elif choice == "3":
            self._setup_dropbox()
        elif choice == "4":
            self._setup_other_provider()
    
    def _setup_pcloud(self) -> None:
        """Guided pCloud setup."""
        print()
        box_line, box_width = create_box_helper(80)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('pCloud Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" pCloud offers excellent value with lifetime plans and"))
        print(box_line(" servers in both EU and US regions."))
        print(box_line(""))
        print(box_line(f" {colorize('Step 1:', Colors.CYAN)} On any computer with a browser, run:"))
        print(box_line(""))
        print(box_line(f"   {colorize('rclone authorize \"pcloud\"', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 2:', Colors.CYAN)} Log in to pCloud in the browser"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 3:', Colors.CYAN)} Copy the token JSON that appears"))
        print(draw_box_bottom(box_width))
        print()
        
        token = get_input("Paste token JSON (or 'cancel' to go back)", "")
        
        if token.lower() == "cancel" or not token:
            return
        
        # Validate JSON
        try:
            import json as json_module
            json_module.loads(token)
        except:
            error("Invalid JSON format. Make sure you copy the entire token.")
            input("\nPress Enter to continue...")
            return
        
        say("Configuring pCloud remote...")
        
        # Try EU region first, then US
        for host, region in [("eapi.pcloud.com", "EU"), ("api.pcloud.com", "US")]:
            subprocess.run(["rclone", "config", "delete", "pcloud"], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([
                "rclone", "config", "create", "pcloud", "pcloud",
                "token", token, "hostname", host, "--non-interactive"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Test connection
            result = subprocess.run(
                ["rclone", "about", "pcloud:", "--json"],
                capture_output=True,
                timeout=15,
                check=False
            )
            if result.returncode == 0:
                ok(f"pCloud configured successfully ({region} region)")
                self.rclone_configured = True
                input("\nPress Enter to continue...")
                return
        
        error("Failed to connect with provided token. Please try again.")
        input("\nPress Enter to continue...")
    
    def _setup_google_drive(self) -> None:
        """Guided Google Drive setup."""
        print()
        box_line, box_width = create_box_helper(80)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Google Drive Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" Google Drive offers 15GB free storage."))
        print(box_line(""))
        print(box_line(f" {colorize('Step 1:', Colors.CYAN)} On any computer with a browser, run:"))
        print(box_line(""))
        print(box_line(f"   {colorize('rclone authorize \"drive\"', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 2:', Colors.CYAN)} Log in to Google in the browser"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 3:', Colors.CYAN)} Copy the token JSON that appears"))
        print(draw_box_bottom(box_width))
        print()
        
        token = get_input("Paste token JSON (or 'cancel' to go back)", "")
        
        if token.lower() == "cancel" or not token:
            return
        
        try:
            import json as json_module
            json_module.loads(token)
        except:
            error("Invalid JSON format.")
            input("\nPress Enter to continue...")
            return
        
        say("Configuring Google Drive remote...")
        
        subprocess.run(["rclone", "config", "delete", "pcloud"], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([
            "rclone", "config", "create", "pcloud", "drive",
            "token", token, "--non-interactive"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        result = subprocess.run(
            ["rclone", "about", "pcloud:", "--json"],
            capture_output=True,
            timeout=15,
            check=False
        )
        if result.returncode == 0:
            ok("Google Drive configured successfully")
            self.rclone_configured = True
        else:
            error("Failed to connect. Please try again.")
        
        input("\nPress Enter to continue...")
    
    def _setup_dropbox(self) -> None:
        """Guided Dropbox setup."""
        print()
        box_line, box_width = create_box_helper(80)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Dropbox Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" Dropbox offers 2GB free storage."))
        print(box_line(""))
        print(box_line(f" {colorize('Step 1:', Colors.CYAN)} On any computer with a browser, run:"))
        print(box_line(""))
        print(box_line(f"   {colorize('rclone authorize \"dropbox\"', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 2:', Colors.CYAN)} Log in to Dropbox in the browser"))
        print(box_line(""))
        print(box_line(f" {colorize('Step 3:', Colors.CYAN)} Copy the token JSON that appears"))
        print(draw_box_bottom(box_width))
        print()
        
        token = get_input("Paste token JSON (or 'cancel' to go back)", "")
        
        if token.lower() == "cancel" or not token:
            return
        
        try:
            import json as json_module
            json_module.loads(token)
        except:
            error("Invalid JSON format.")
            input("\nPress Enter to continue...")
            return
        
        say("Configuring Dropbox remote...")
        
        subprocess.run(["rclone", "config", "delete", "pcloud"], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([
            "rclone", "config", "create", "pcloud", "dropbox",
            "token", token, "--non-interactive"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        result = subprocess.run(
            ["rclone", "about", "pcloud:", "--json"],
            capture_output=True,
            timeout=15,
            check=False
        )
        if result.returncode == 0:
            ok("Dropbox configured successfully")
            self.rclone_configured = True
        else:
            error("Failed to connect. Please try again.")
        
        input("\nPress Enter to continue...")
    
    def _setup_other_provider(self) -> None:
        """Advanced setup for other rclone providers."""
        print()
        box_line, box_width = create_box_helper(80)
        
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Advanced Provider Setup', Colors.BOLD)}"))
        print(box_line(""))
        print(box_line(" rclone supports 70+ cloud providers. For full list:"))
        print(box_line(f"   {colorize('https://rclone.org/overview/', Colors.CYAN)}"))
        print(box_line(""))
        print(box_line(" Common options:"))
        print(box_line("   • Backblaze B2  - Cheap object storage"))
        print(box_line("   • Amazon S3     - Enterprise storage"))
        print(box_line("   • SFTP          - Any SSH server"))
        print(box_line("   • WebDAV        - Nextcloud, ownCloud, etc."))
        print(box_line("   • FTP           - Legacy servers"))
        print(box_line(""))
        print(box_line(" To configure manually, run:"))
        print(box_line(f"   {colorize('rclone config', Colors.YELLOW)}"))
        print(box_line(""))
        print(box_line(f" {colorize('Important:', Colors.RED)} Name your remote 'pcloud' for"))
        print(box_line(" compatibility with this system."))
        print(draw_box_bottom(box_width))
        print()
        
        if confirm("Launch rclone interactive config?", True):
            print()
            say("Starting rclone config... Create a remote named 'pcloud'")
            print()
            subprocess.run(["rclone", "config"], check=False)
            
            # Check if it worked
            self.rclone_configured = self._check_rclone_connection()
            if self.rclone_configured:
                ok("Remote configured successfully!")
            else:
                warn("Remote not detected. Make sure it's named 'pcloud'.")
        
        input("\nPress Enter to continue...")
    
    def instances_menu(self) -> None:
        """Instances management menu."""
        while True:
            instances = self.instance_manager.list_instances()
            
            print_header("Instances")
            
            if instances:
                for idx, instance in enumerate(instances, 1):
                    status = colorize("Running", Colors.GREEN) if instance.is_running else colorize("Stopped", Colors.YELLOW)
                    access_urls = instance.get_access_urls_formatted()
                    print(f"  {idx}) {instance.name} [{status}]")
                    if len(access_urls) == 1:
                        print(f"      Access: {access_urls[0][0]}: {colorize(access_urls[0][1], Colors.CYAN)}")
                    else:
                        print(f"      Access:")
                        for mode_label, url in access_urls:
                            print(f"        {mode_label}: {colorize(url, Colors.CYAN)}")
                    print(f"      Stack:  {instance.stack_dir}")
                    print(f"      Data:   {instance.data_root}")
                print()
            else:
                print("  No instances configured\n")
            
            # Build options with numbers for instances first, then operations
            options = []
            for idx in range(1, len(instances) + 1):
                options.append((str(idx), f"Manage '{instances[idx-1].name}'"))
            
            next_num = len(instances) + 1
            options.append((str(next_num), "Add new instance"))
            options.append((str(next_num + 1), "Delete all instances"))
            options.append(("0", "Back to main menu"))
            
            print_menu(options)
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == str(next_num):
                self.add_instance_menu()
            elif choice == str(next_num + 1):
                print()
                warn(f"This will DELETE all {len(instances)} instances completely!")
                print("  • All instance directories")
                print("  • All Docker containers")
                print("  • All data and configurations")
                print("  • All Cloudflared services")
                print()
                
                if confirm("Delete ALL instances and their files?", False):
                    confirmation = get_input("Type 'DELETE ALL' to confirm", "")
                    if confirmation == "DELETE ALL":
                        for inst in instances:
                            self.instance_manager.remove_instance(inst.name, delete_files=True)
                        ok(f"All {len(instances)} instances completely deleted")
                        input("\nPress Enter to continue...")
                    else:
                        warn("Deletion cancelled")
            elif choice.isdigit() and 1 <= int(choice) <= len(instances):
                self.instance_detail_menu(instances[int(choice) - 1])
            else:
                warn("Invalid option")
    
    def traefik_menu(self) -> None:
        """Traefik management menu."""
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import traefik
        
        while True:
            print_header("Manage Traefik (HTTPS)")
            
            # Check Traefik status
            is_running = traefik.is_traefik_running()
            configured_email = traefik.get_traefik_email()
            
            if is_running:
                say(colorize("✓ System Traefik is running", Colors.GREEN))
                if configured_email:
                    print(f"Let's Encrypt Email: {colorize(configured_email, Colors.CYAN)}")
                print()
                print("Traefik provides HTTPS routing for all instances.")
                print("Each instance with Traefik enabled will automatically")
                print("get SSL certificates and HTTPS access via its domain.")
                print()
            else:
                say(colorize("⚠ System Traefik is not running", Colors.YELLOW))
                print()
                print("Install Traefik to enable HTTPS for instances.")
                print()
            
            options = []
            if is_running:
                options.extend([
                    ("1", "View Traefik status"),
                    ("2", "Update Let's Encrypt email"),
                    ("3", "Restart Traefik"),
                    ("4", "Stop and remove Traefik"),
                ])
            else:
                options.append(("1", "Install and start Traefik"))
            
            options.append(("0", "Back to main menu"))
            
            print_menu(options)
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if is_running:
                    # Show status
                    import subprocess
                    try:
                        subprocess.run(["docker", "ps", "--filter", "name=traefik-system"], check=True)
                        subprocess.run(["docker", "logs", "--tail", "50", "traefik-system"], check=True)
                    except:
                        warn("Failed to get Traefik status")
                    input("\nPress Enter to continue...")
                else:
                    # Install
                    email = get_email_input("Let's Encrypt email for SSL certificates", "admin@example.com")
                    
                    if traefik.setup_system_traefik(email):
                        ok("Traefik installed and started successfully!")
                        say("You can now create instances with HTTPS enabled")
                    else:
                        error("Failed to install Traefik")
                    input("\nPress Enter to continue...")
            elif choice == "2" and is_running:
                # Update email
                current = configured_email or "admin@example.com"
                say(f"Current email: {current}")
                email = get_email_input("New Let's Encrypt email", current)
                
                if confirm("Restart Traefik with new email?", True):
                    traefik.stop_system_traefik()
                    if traefik.setup_system_traefik(email):
                        ok("Traefik restarted with new email")
                    else:
                        error("Failed to restart Traefik")
                input("\nPress Enter to continue...")
            elif choice == "3" and is_running:
                # Restart
                import subprocess
                try:
                    subprocess.run(["docker", "restart", "traefik-system"], check=True)
                    ok("Traefik restarted")
                except:
                    warn("Failed to restart Traefik")
                input("\nPress Enter to continue...")
            elif choice == "4" and is_running:
                # Stop and remove
                if confirm("Stop and remove Traefik? All HTTPS instances will become unavailable.", False):
                    traefik.stop_system_traefik()
                    ok("Traefik stopped and removed")
                    warn("Existing instances with HTTPS will not be accessible until Traefik is reinstalled")
                input("\nPress Enter to continue...")
    
    def add_instance_menu(self) -> None:
        """Add new instance submenu with modern styling."""
        print_header("Add New Instance")
        
        box_line, box_width = create_box_helper(80)
        
        print(draw_box_top(box_width))
        print(box_line(" Choose how to create your new instance:"))
        print(box_line(""))
        print(box_line(f"   {colorize('1)', Colors.BOLD)} {colorize('Create fresh instance', Colors.CYAN)}"))
        print(box_line("      Start with a clean Paperless installation"))
        print(box_line(""))
        print(box_line(f"   {colorize('2)', Colors.BOLD)} {colorize('Restore from cloud backup', Colors.CYAN)}"))
        print(box_line("      Restore documents and settings from cloud backup"))
        print(draw_box_bottom(box_width))
        print()
        print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
        print()
        
        choice = get_input("Select option", "")
        
        if choice == "1":
            self.create_fresh_instance()
        elif choice == "2":
            self.restore_instance_from_backup()
        # else back (0 or any other)
    
    def restore_instance_from_backup(self, backup_instance: str = None, snapshot: str = None) -> None:
        """Restore an instance from cloud backup with guided setup.
        
        Flow:
        1. Select backup source and snapshot
        2. Download and parse backup's .env to get original settings
        3. Detect conflicts (ports, names, paths)
        4. Walk through settings, allowing changes and forcing where conflicts exist
        5. Restore data with merged config
        
        Args:
            backup_instance: Name of the backup instance to restore from (prompts if None)
            snapshot: Snapshot name to restore (prompts if None)
        """
        if not self.rclone_configured:
            error("Backup server not configured!")
            say("Configure from main menu: Configure Backup Server")
            input("\nPress Enter to continue...")
            return
        
        print_header("Restore from Backup")
        
        # Get existing instances for validation
        existing_instances = [i.name for i in self.instance_manager.list_instances()]
        
        # Check networking availability
        net_status = check_networking_dependencies()
        
        # Get rclone remote settings
        remote_name = "pcloud"  # TODO: make configurable
        remote_base = f"{remote_name}:backups/paperless"
        
        box_line, box_width = create_box_helper(80)
        
        try:
            # ─── Step 1: Select Backup Source ─────────────────────────────────
            print(colorize("Step 1 of 4: Select Backup", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Select backup instance if not provided
            if not backup_instance:
                say("Scanning backup server...")
                
                result = subprocess.run(
                    ["rclone", "lsd", remote_base],
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn("No backups found on server")
                    input("\nPress Enter to continue...")
                    return
                
                # Parse instance names
                backup_instances = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if parts:
                        backup_instances.append(parts[-1])
                
                if not backup_instances:
                    warn("No backup instances found")
                    input("\nPress Enter to continue...")
                    return
                
                # Show available instances in a nice format
                print(draw_box_top(box_width))
                print(box_line(f" {colorize('Available Backups', Colors.BOLD)}"))
                print(box_line(""))
                for idx, inst_name in enumerate(backup_instances, 1):
                    print(box_line(f"   {colorize(str(idx) + ')', Colors.BOLD)} {inst_name}"))
                print(draw_box_bottom(box_width))
                print()
                
                selected = get_input(f"Select backup [1-{len(backup_instances)}] or 'cancel'", "cancel")
                
                if selected.lower() == "cancel" or not selected.isdigit():
                    return
                
                idx = int(selected)
                if not (1 <= idx <= len(backup_instances)):
                    warn("Invalid selection")
                    return
                
                backup_instance = backup_instances[idx - 1]
            
            # Select snapshot
            if not snapshot:
                say(f"Loading snapshots for '{backup_instance}'...")
                
                result = subprocess.run(
                    ["rclone", "lsd", f"{remote_base}/{backup_instance}"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn(f"No snapshots found for {backup_instance}")
                    input("\nPress Enter to continue...")
                    return
                
                snapshots = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if parts:
                        snapshots.append(parts[-1])
                
                # Sort newest first (date-based names)
                snapshots = sorted(snapshots, reverse=True)
                
                print()
                print(draw_box_top(box_width))
                print(box_line(f" {colorize('Available Snapshots', Colors.BOLD)} ({backup_instance})"))
                print(box_line(""))
                for idx, snap in enumerate(snapshots, 1):
                    # Parse date from snapshot name (format: YYYY-MM-DD_HH-MM-SS)
                    display = snap
                    try:
                        date_part = snap.split("_")[0]
                        time_part = snap.split("_")[1].replace("-", ":")
                        display = f"{date_part} {time_part}"
                    except:
                        pass
                    latest_marker = colorize(" (latest)", Colors.GREEN) if idx == 1 else ""
                    print(box_line(f"   {colorize(str(idx) + ')', Colors.BOLD)} {display}{latest_marker}"))
                print(draw_box_bottom(box_width))
                print()
                
                say("Tip: Enter 'L' for latest snapshot")
                snap_choice = get_input(f"Select snapshot [1-{len(snapshots)}, L=latest]", "L")
                
                if snap_choice.lower() == "l":
                    snapshot = snapshots[0]  # Latest is now first
                elif snap_choice.isdigit() and 1 <= int(snap_choice) <= len(snapshots):
                    snapshot = snapshots[int(snap_choice) - 1]
                else:
                    warn("Invalid selection")
                    return
            
            ok(f"Selected: {backup_instance}/{snapshot}")
            print()
            
            # ─── Step 2: Load Backup Configuration ────────────────────────────
            print(colorize("Step 2 of 4: Review Backup Settings", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            say("Downloading backup configuration...")
            
            # Download the .env from the backup to see original settings
            result = subprocess.run(
                ["rclone", "cat", f"{remote_base}/{backup_instance}/{snapshot}/.env"],
                capture_output=True,
                text=True,
                check=False
            )
            
            backup_env = {}
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        backup_env[k.strip()] = v.strip()
                ok("Loaded backup configuration")
            else:
                warn("Could not load backup .env - will use defaults")
            
            # Import installer modules
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import common, files, traefik, cloudflared, tailscale
            from lib.installer.common import get_next_available_port
            from lib.instance import is_port_available, find_available_port
            
            # ─── Detect Conflicts ─────────────────────────────────────────────
            # Check what ports are in use (checks both OS and existing instances)
            original_port = backup_env.get("HTTP_PORT", "8000")
            port_conflict = not is_port_available(int(original_port), check_existing_instances=True)
            
            # Check if instance name conflicts with REGISTERED instances
            original_name = backup_env.get("INSTANCE_NAME", backup_instance)
            name_conflict = original_name in existing_instances
            
            # Check if paths exist (separate from name conflict - paths may exist without registration)
            original_data_root = backup_env.get("DATA_ROOT", f"/home/docker/{original_name}")
            original_stack_dir = backup_env.get("STACK_DIR", f"/home/docker/{original_name}-setup")
            path_conflict = Path(original_data_root).exists() or Path(original_stack_dir).exists()
            
            # Check for "ghost" registry entries - registered but files don't exist
            ghost_entry = name_conflict and not path_conflict
            if ghost_entry:
                # Instance is registered but files don't exist - offer to remove
                warn(f"Instance '{original_name}' is registered but has no files on disk")
                if confirm(f"Remove orphaned registry entry for '{original_name}'?", True):
                    self.instance_manager.remove_instance(original_name)
                    self.instance_manager.load_instances()
                    existing_instances = [i.name for i in self.instance_manager.list_instances()]
                    name_conflict = False
                    ok(f"Removed orphaned entry for '{original_name}'")
            
            # Show backup configuration with conflict warnings
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Backup Configuration', Colors.BOLD)}"))
            print(draw_box_divider(box_width))
            
            # Instance name
            name_status = colorize(" ⚠ CONFLICT", Colors.RED) if name_conflict else ""
            print(box_line(f" Instance:  {original_name}{name_status}"))
            
            # Credentials (from backup)
            admin_user = backup_env.get("PAPERLESS_ADMIN_USER", "admin")
            print(box_line(f" Admin:     {admin_user}"))
            print(box_line(f" Timezone:  {backup_env.get('TZ', 'UTC')}"))
            
            # Database
            print(box_line(f" Database:  {backup_env.get('POSTGRES_DB', 'paperless')}"))
            
            print(draw_box_divider(box_width))
            
            # Network settings
            print(box_line(f" {colorize('Network Settings:', Colors.BOLD)}"))
            
            port_status = colorize(" ⚠ IN USE", Colors.RED) if port_conflict else ""
            print(box_line(f" Port:      {original_port}{port_status}"))
            
            original_traefik = backup_env.get("ENABLE_TRAEFIK", "no")
            original_cloudflared = backup_env.get("ENABLE_CLOUDFLARED", "no")
            original_domain = backup_env.get("DOMAIN", "")
            
            if original_traefik == "yes":
                print(box_line(f" Access:    HTTPS via Traefik"))
                print(box_line(f" Domain:    {original_domain}"))
            elif original_cloudflared == "yes":
                print(box_line(f" Access:    Cloudflare Tunnel"))
                print(box_line(f" Domain:    {original_domain}"))
            else:
                print(box_line(f" Access:    Direct HTTP"))
            
            if path_conflict:
                print(draw_box_divider(box_width))
                print(box_line(f" {colorize('⚠ Paths already exist - will use new paths', Colors.YELLOW)}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # ─── Step 3: Configure Instance ───────────────────────────────────
            print(colorize("Step 3 of 4: Configure Instance", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Instance name - force change if conflict
            if name_conflict:
                warn(f"Instance name '{original_name}' conflicts with existing registered instance")
                suggested_name = f"{original_name}-restored"
            elif path_conflict:
                warn(f"Paths for '{original_name}' already exist - using new name")
                suggested_name = f"{original_name}-restored"
            else:
                suggested_name = original_name
            
            while True:
                new_name = get_instance_name_input("Instance name", suggested_name, existing_instances)
                
                # Check if new paths would conflict
                new_data_root = f"/home/docker/{new_name}"
                new_stack_dir = f"/home/docker/{new_name}-setup"
                if Path(new_data_root).exists() or Path(new_stack_dir).exists():
                    error(f"Paths for '{new_name}' already exist - choose another name")
                    suggested_name = f"{new_name}-2"
                    continue
                
                break
            
            # Set up paths
            common.cfg.instance_name = new_name
            common.cfg.data_root = f"/home/docker/{new_name}"
            common.cfg.stack_dir = f"/home/docker/{new_name}-setup"
            common.cfg.rclone_remote_name = remote_name
            common.cfg.rclone_remote_path = f"backups/paperless/{new_name}"
            common.cfg.refresh_paths()
            
            # Load settings from backup
            # Skip consume folders for clones - they need fresh setup with new device IDs
            load_backup_env_config(backup_env, skip_consume_folders=True)
            
            print()
            say(f"Instance '{colorize(new_name, Colors.BOLD)}' paths:")
            print(f"  Data:  {colorize(common.cfg.data_root, Colors.CYAN)}")
            print(f"  Stack: {colorize(common.cfg.stack_dir, Colors.CYAN)}")
            print()
            
            # Network access - show original and allow change
            say("Access method:")
            print()
            
            # Determine what was originally used
            original_access = "1"  # Direct HTTP
            if original_traefik == "yes":
                original_access = "2"
            elif original_cloudflared == "yes":
                original_access = "3"
            
            print(f"  {colorize('1)', Colors.BOLD)} {colorize('Direct HTTP', Colors.CYAN)} - Simple port binding" + 
                  (colorize(" (original)", Colors.GREEN) if original_access == "1" else ""))
            print(f"  {colorize('2)', Colors.BOLD)} {colorize('HTTPS via Traefik', Colors.CYAN)}" + 
                  ("" if net_status["traefik_running"] else colorize(" (not running)", Colors.YELLOW)) +
                  (colorize(" (original)", Colors.GREEN) if original_access == "2" else ""))
            print(f"  {colorize('3)', Colors.BOLD)} {colorize('Cloudflare Tunnel', Colors.CYAN)}" + 
                  ("" if net_status["cloudflared_authenticated"] else colorize(" (not configured)", Colors.YELLOW)) +
                  (colorize(" (original)", Colors.GREEN) if original_access == "3" else ""))
            print()
            
            access_choice = get_input("Choose access method [1-3]", original_access)
            
            # Domain - use original if available, suggest new based on new name
            default_domain = original_domain if original_domain else f"{new_name}.example.com"
            if original_name in default_domain and new_name != original_name:
                default_domain = default_domain.replace(original_name, new_name)
            
            if access_choice == "2":
                common.cfg.enable_traefik = "yes"
                common.cfg.enable_cloudflared = "no"
                common.cfg.domain = get_domain_input("Domain", default_domain)
                
                if not net_status["traefik_running"]:
                    warn("Traefik is not running - HTTPS won't work until configured")
                    if not confirm("Continue anyway?", False):
                        return
                        
            elif access_choice == "3":
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "yes"
                common.cfg.domain = get_domain_input("Domain", default_domain)
                
                if not net_status["cloudflared_authenticated"]:
                    warn("Cloudflare Tunnel not configured")
                    if not confirm("Continue anyway? (tunnel won't be created)", False):
                        return
            else:
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "no"
            
            # Port - force change if conflict
            print()
            if port_conflict:
                warn(f"Port {original_port} is already in use!")
                available_port = find_available_port(int(original_port) + 1, check_existing_instances=True)
                common.cfg.http_port = get_port_input("HTTP port (must change)", str(available_port))
                
                # Verify new port isn't also in use
                while not is_port_available(int(common.cfg.http_port), check_existing_instances=True):
                    warn(f"Port {common.cfg.http_port} is also in use!")
                    available_port = find_available_port(int(common.cfg.http_port) + 1, check_existing_instances=True)
                    common.cfg.http_port = get_port_input("HTTP port", str(available_port))
            else:
                common.cfg.http_port = get_port_input("HTTP port", original_port)
                # Also verify user-chosen port isn't in use
                while not is_port_available(int(common.cfg.http_port), check_existing_instances=True):
                    warn(f"Port {common.cfg.http_port} is in use!")
                    available_port = find_available_port(int(common.cfg.http_port) + 1, check_existing_instances=True)
                    common.cfg.http_port = get_port_input("HTTP port", str(available_port))
            
            # Tailscale option
            original_tailscale = backup_env.get("ENABLE_TAILSCALE", "no")
            if net_status["tailscale_connected"]:
                print()
                default_ts = original_tailscale == "yes"
                if confirm("Enable Tailscale access?", default_ts):
                    common.cfg.enable_tailscale = "yes"
                else:
                    common.cfg.enable_tailscale = "no"
            else:
                common.cfg.enable_tailscale = "no"
            
            # Backup schedule - use original or defaults
            common.cfg.cron_incr_time = backup_env.get("CRON_INCR_TIME", "0 */6 * * *")
            common.cfg.cron_full_time = backup_env.get("CRON_FULL_TIME", "30 3 * * 0")
            common.cfg.cron_archive_time = backup_env.get("CRON_ARCHIVE_TIME", "0 4 1 * *")
            common.cfg.refresh_paths()
            print()
            
            # ─── Step 4: Restore ──────────────────────────────────────────────
            print(colorize("Step 4 of 4: Restore Data", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Summary before proceeding
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Restore Summary', Colors.BOLD)}"))
            print(box_line(""))
            print(box_line(f" Source:  {backup_instance}/{snapshot}"))
            print(box_line(f" Target:  {colorize(new_name, Colors.CYAN)}"))
            print(box_line(f" Path:    {common.cfg.data_root}"))
            
            if common.cfg.enable_cloudflared == "yes":
                print(box_line(f" Access:  ☁️  https://{common.cfg.domain}"))
            elif common.cfg.enable_traefik == "yes":
                print(box_line(f" Access:  🛡️ https://{common.cfg.domain}"))
            else:
                print(box_line(f" Access:  🌐 http://localhost:{common.cfg.http_port}"))
            print(draw_box_bottom(box_width))
            print()
            
            if not confirm("Proceed with restore?", True):
                say("Restore cancelled")
                input("\nPress Enter to continue...")
                return
            
            print()
            
            # Check dependencies
            if common.cfg.enable_traefik == "yes" and not net_status["traefik_running"]:
                warn("Traefik not running - HTTPS won't work until configured")
            if common.cfg.enable_cloudflared == "yes" and not net_status["cloudflared_authenticated"]:
                warn("Cloudflare not configured - tunnel won't be created")
            
            # Create directories
            say("Creating directories...")
            common.ensure_dir_tree(common.cfg)
            ok("Directories created")
            
            # Write config files
            say("Writing configuration...")
            files.write_env_file()
            files.write_compose_file()
            files.copy_helper_scripts()
            ok("Configuration written")
            
            # Restore data
            say(f"Restoring data from backup...")
            
            # Determine if this is a clone (different name) or same-name restore
            is_clone = new_name != backup_instance
            
            success = run_restore_with_env(
                stack_dir=Path(common.cfg.stack_dir),
                data_root=Path(common.cfg.data_root),
                instance_name=new_name,
                remote_name=remote_name,
                remote_path=f"backups/paperless/{backup_instance}",
                snapshot=snapshot,
                fresh_config=True,  # Manager wrote config files
                restore_syncthing=not is_clone  # Restore Syncthing for same-name restore
            )
            
            if not success:
                raise Exception("Restore operation failed")
            
            ok("Data restored successfully")
            
            # Install backup cron
            files.install_cron_backup()
            ok("Backup schedule configured")
            
            # Set up Cloudflare tunnel if enabled
            if common.cfg.enable_cloudflared == "yes" and net_status["cloudflared_authenticated"]:
                port = int(common.cfg.http_port)
                setup_cloudflare_tunnel(new_name, common.cfg.domain, port)
            
            # Register instance
            self.instance_manager.add_instance(
                new_name,
                Path(common.cfg.stack_dir),
                Path(common.cfg.data_root)
            )
            
            # For same-name restores, ensure consume services are set up
            # For clones, user needs to configure consume services fresh
            restored_instance = self.instance_manager.get_instance(new_name)
            if not is_clone and restored_instance:
                self._ensure_consume_services(restored_instance)
            
            # Success message
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('✓ Restore Complete!', Colors.GREEN)}"))
            print(box_line(""))
            if common.cfg.enable_cloudflared == "yes":
                print(box_line(f" Access: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            elif common.cfg.enable_traefik == "yes":
                print(box_line(f" Access: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            else:
                print(box_line(f" Access: {colorize(f'http://localhost:{common.cfg.http_port}', Colors.CYAN)}"))
            print(box_line(""))
            print(box_line(" Your documents and settings have been restored."))
            
            # Different message for clone vs same-name restore
            if is_clone:
                print(box_line(""))
                print(box_line(colorize(" Note:", Colors.YELLOW) + " Consume folders (Syncthing/Samba/SFTP) need to"))
                print(box_line(" be set up fresh via Manage Instance → Consume Folders."))
            else:
                print(box_line(""))
                print(box_line(" Consume services have been restored from backup."))
            print(draw_box_bottom(box_width))
            
        except KeyboardInterrupt:
            print()
            say("Restore cancelled")
            # Offer cleanup if files were created
            if 'new_name' in dir() and new_name:
                self._cleanup_failed_restore(new_name, common.cfg if 'common' in dir() else None)
        except Exception as e:
            error(f"Restore failed: {e}")
            import traceback
            traceback.print_exc()
            # Offer cleanup if files were created
            if 'new_name' in dir() and new_name:
                self._cleanup_failed_restore(new_name, common.cfg if 'common' in dir() else None)
        
        input("\nPress Enter to continue...")
    
    def _cleanup_failed_restore(self, instance_name: str, cfg=None) -> None:
        """Offer to clean up a failed restore attempt."""
        print()
        
        # Check what was created
        data_root = Path(cfg.data_root) if cfg else Path(f"/home/docker/{instance_name}")
        stack_dir = Path(cfg.stack_dir) if cfg else Path(f"/home/docker/{instance_name}-setup")
        
        has_data = data_root.exists()
        has_stack = stack_dir.exists()
        
        if not has_data and not has_stack:
            return  # Nothing to clean up
        
        print(draw_box_top(78))
        box_line, _ = create_box_helper(78)
        print(box_line(colorize(" Cleanup Options", Colors.BOLD)))
        print(draw_box_divider(78))
        print(box_line(f" The restore failed but some files were created:"))
        if has_data:
            print(box_line(f"   • Data:  {data_root}"))
        if has_stack:
            print(box_line(f"   • Stack: {stack_dir}"))
        print(draw_box_bottom(78))
        print()
        
        print("  1) Delete partial installation (recommended)")
        print("  2) Keep files for manual inspection")
        print()
        
        choice = input("Select option [1]: ").strip() or "1"
        
        if choice == "1":
            say("Cleaning up failed restore...")
            
            # Stop any containers that might have been started
            try:
                import subprocess
                container_name = f"paperless-{instance_name}"
                subprocess.run(
                    ["docker", "compose", "-p", container_name, "down", "-v", "--remove-orphans"],
                    cwd=str(stack_dir) if has_stack else None,
                    capture_output=True,
                    timeout=60
                )
            except:
                pass
            
            # Remove directories
            import shutil
            if has_data:
                try:
                    shutil.rmtree(data_root)
                    ok(f"Removed {data_root}")
                except Exception as e:
                    warn(f"Could not remove {data_root}: {e}")
            
            if has_stack:
                try:
                    shutil.rmtree(stack_dir)
                    ok(f"Removed {stack_dir}")
                except Exception as e:
                    warn(f"Could not remove {stack_dir}: {e}")
            
            # Remove from instance registry if it got registered
            try:
                self.instance_manager.remove_instance(instance_name)
            except:
                pass
            
            ok("Cleanup complete")
        else:
            say("Files kept for inspection")
            say(f"You can manually delete them with:")
            print(f"  sudo rm -rf {data_root}")
            print(f"  sudo rm -rf {stack_dir}")

    def create_fresh_instance(self) -> None:
        """Create a new fresh instance with guided setup."""
        print_header("Create New Instance")
        
        if os.geteuid() != 0:
            error("Creating instances requires root privileges. Please run with sudo.")
            input("\nPress Enter to continue...")
            return
        
        # Get existing instances for validation
        instances = self.instance_manager.list_instances()
        existing_instances = [i.name for i in instances]
        
        # Check networking service availability upfront
        net_status = check_networking_dependencies()
        
        # Display welcome box with system status
        box_line, box_width = create_box_helper(80)
        print(draw_box_top(box_width))
        print(box_line(" Welcome to the Paperless-NGX instance creator!"))
        print(box_line(""))
        print(box_line(" This wizard will guide you through setting up a new"))
        print(box_line(" Paperless-NGX instance with your preferred options."))
        print(box_line(""))
        print(draw_section_header("System Status", box_width))
        
        # Show networking availability
        traefik_status = colorize("● Ready", Colors.GREEN) if net_status["traefik_running"] else colorize("○ Not running", Colors.YELLOW)
        cloudflare_status = colorize("● Ready", Colors.GREEN) if net_status["cloudflared_authenticated"] else (
            colorize("○ Not authenticated", Colors.YELLOW) if net_status["cloudflared_installed"] else colorize("○ Not installed", Colors.RED)
        )
        tailscale_status = colorize("● Connected", Colors.GREEN) if net_status["tailscale_connected"] else (
            colorize("○ Not connected", Colors.YELLOW) if net_status["tailscale_installed"] else colorize("○ Not installed", Colors.RED)
        )
        
        print(box_line(f" Traefik (HTTPS):     {traefik_status}"))
        print(box_line(f" Cloudflare Tunnel:   {cloudflare_status}"))
        print(box_line(f" Tailscale:           {tailscale_status}"))
        print(draw_box_bottom(box_width))
        print()
        
        try:
            # Import installer modules
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import common, files, traefik, cloudflared, tailscale
            
            # ─── Step 1: Instance Identity ────────────────────────────────────
            print(colorize("Step 1 of 5: Instance Identity", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Get instance name with validation
            instance_name = get_instance_name_input("Instance name", "paperless", existing_instances)
            
            # Set up paths
            common.cfg.instance_name = instance_name
            common.cfg.data_root = f"/home/docker/{instance_name}"
            common.cfg.stack_dir = f"/home/docker/{instance_name}-setup"
            common.cfg.rclone_remote_path = f"backups/paperless/{instance_name}"
            common.cfg.refresh_paths()
            
            # Show computed paths
            print()
            say(f"Instance '{colorize(instance_name, Colors.BOLD)}' will use:")
            print(f"  Data:  {colorize(common.cfg.data_root, Colors.CYAN)}")
            print(f"  Stack: {colorize(common.cfg.stack_dir, Colors.CYAN)}")
            print()
            
            # Timezone
            common.cfg.tz = get_input("Timezone", common.cfg.tz)
            
            # Admin credentials
            print()
            say("Set up admin credentials:")
            common.cfg.paperless_admin_user = get_input("Admin username", common.cfg.paperless_admin_user)
            common.cfg.paperless_admin_password = get_input("Admin password", common.cfg.paperless_admin_password)
            print()
            
            # ─── Step 2: Network Access ───────────────────────────────────────
            print(colorize("Step 2 of 5: Network Access", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            say("How should this instance be accessed?")
            print()
            
            options = []
            options.append(("1", "Direct HTTP", "Simple port binding (e.g., http://localhost:8000)", True))
            options.append(("2", "HTTPS via Traefik", "Automatic SSL certificates" + (
                "" if net_status["traefik_running"] else colorize(" (Traefik not running)", Colors.YELLOW)
            ), True))
            options.append(("3", "Cloudflare Tunnel", "Secure access via Cloudflare" + (
                "" if net_status["cloudflared_authenticated"] else colorize(" (Not configured)", Colors.YELLOW)
            ), True))
            
            for key, title, desc, _ in options:
                print(f"  {colorize(key + ')', Colors.BOLD)} {colorize(title, Colors.CYAN)}")
                print(f"     {desc}")
            print()
            
            access_choice = get_input("Choose access method [1-3]", "1")
            
            if access_choice == "2":
                common.cfg.enable_traefik = "yes"
                common.cfg.enable_cloudflared = "no"
                # Get base domain from Traefik config (from Let's Encrypt email)
                from lib.installer.traefik import get_base_domain as get_traefik_domain
                traefik_base = get_traefik_domain()
                default_domain = f"{instance_name}.{traefik_base}" if traefik_base else f"{instance_name}.example.com"
                common.cfg.domain = get_domain_input("Domain (DNS must point to this server)", default_domain)
                
                if not net_status["traefik_running"]:
                    # Only ask for email if Traefik isn't running yet (will need to be set up)
                    common.cfg.letsencrypt_email = get_email_input("Email for Let's Encrypt", common.cfg.letsencrypt_email)
                    warn("Traefik is not running!")
                    print()
                    print("  1) Set up Traefik now (recommended)")
                    print("  2) Continue anyway (configure Traefik later)")
                    print("  0) Cancel")
                    print()
                    traefik_choice = get_input("Choose option", "1")
                    
                    if traefik_choice == "0":
                        say("Setup cancelled")
                        input("\nPress Enter to continue...")
                        return
                    elif traefik_choice == "1":
                        # Set up Traefik inline
                        say("Setting up Traefik...")
                        from lib.installer.traefik import setup_system_traefik
                        if setup_system_traefik(common.cfg.letsencrypt_email):
                            ok("Traefik installed and running")
                            # Update net_status
                            net_status["traefik_running"] = True
                        else:
                            error("Failed to set up Traefik")
                            if not confirm("Continue anyway?", False):
                                say("Setup cancelled")
                                input("\nPress Enter to continue...")
                                return
                    # traefik_choice == "2" just continues
                        
            elif access_choice == "3":
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "yes"
                # Get base domain from existing Cloudflare tunnel configs
                from lib.installer.cloudflared import get_base_domain as get_cloudflare_domain
                cloudflare_base = get_cloudflare_domain()
                default_domain = f"{instance_name}.{cloudflare_base}" if cloudflare_base else f"{instance_name}.example.com"
                common.cfg.domain = get_domain_input("Domain (configured in Cloudflare)", default_domain)
                
                if not net_status["cloudflared_authenticated"]:
                    warn("Cloudflare Tunnel not configured!")
                    say("Set up from main menu: Manage Cloudflare Tunnel")
                    if not confirm("Continue anyway? (Tunnel won't be created automatically)", False):
                        say("Setup cancelled")
                        input("\nPress Enter to continue...")
                        return
            else:
                common.cfg.enable_traefik = "no"
                common.cfg.enable_cloudflared = "no"
            
            # Port selection - find available port BEFORE showing default
            print()
            from lib.installer.common import get_next_available_port
            available_port = get_next_available_port(8000)  # Always start checking from 8000
            common.cfg.http_port = get_port_input("HTTP port", available_port)
            
            # Tailscale add-on
            print()
            if net_status["tailscale_connected"]:
                say("Tailscale is available for private network access")
                if confirm("Enable Tailscale access?", False):
                    common.cfg.enable_tailscale = "yes"
                    ok("Tailscale access enabled")
                else:
                    common.cfg.enable_tailscale = "no"
            elif net_status["tailscale_installed"]:
                say("Tailscale is installed but not connected")
                common.cfg.enable_tailscale = "no"
            else:
                common.cfg.enable_tailscale = "no"
            print()
            
            # ─── Step 3: Backup Configuration ─────────────────────────────────
            print(colorize("Step 3 of 5: Backup Schedule", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            if self.rclone_configured:
                say("Backup server is configured. Set your backup schedule:")
                print()
                
                print(colorize("  Backup Frequency:", Colors.BOLD))
                print(f"  {colorize('1)', Colors.BOLD)} Recommended: 6-hour incremental, weekly full, monthly archive")
                print(f"  {colorize('2)', Colors.BOLD)} Conservative: Daily incremental, weekly full, monthly archive")
                print(f"  {colorize('3)', Colors.BOLD)} Minimal: Weekly full, monthly archive only")
                print(f"  {colorize('4)', Colors.BOLD)} Custom schedule")
                print()
                
                backup_choice = get_input("Choose backup plan [1-4]", "1")
                
                if backup_choice == "1":
                    # Recommended: comprehensive coverage
                    common.cfg.cron_incr_time = "0 */6 * * *"   # Every 6 hours
                    common.cfg.cron_full_time = "30 3 * * 0"    # Sunday 3:30 AM
                    common.cfg.cron_archive_time = "0 4 1 * *"  # 1st of month 4:00 AM
                elif backup_choice == "2":
                    # Conservative: less frequent
                    common.cfg.cron_incr_time = "0 0 * * *"     # Daily midnight
                    common.cfg.cron_full_time = "30 3 * * 0"    # Sunday 3:30 AM
                    common.cfg.cron_archive_time = "0 4 1 * *"  # 1st of month 4:00 AM
                elif backup_choice == "3":
                    # Minimal: just full and archive
                    common.cfg.cron_incr_time = ""              # Disabled
                    common.cfg.cron_full_time = "30 3 * * 0"    # Sunday 3:30 AM
                    common.cfg.cron_archive_time = "0 4 1 * *"  # 1st of month 4:00 AM
                else:
                    # Custom - use helper function
                    self._configure_custom_backup_schedule()
                
                print()
                print(colorize("  Retention Policy:", Colors.BOLD))
                print(f"  {colorize('1)', Colors.BOLD)} Standard: Keep all for 30 days, monthly archives for 6 months")
                print(f"  {colorize('2)', Colors.BOLD)} Extended: Keep all for 60 days, monthly archives for 12 months")
                print(f"  {colorize('3)', Colors.BOLD)} Compact: Keep all for 14 days, monthly archives for 3 months")
                print(f"  {colorize('4)', Colors.BOLD)} Custom retention")
                print()
                
                retention_choice = get_input("Choose retention policy [1-4]", "1")
                
                if retention_choice == "1":
                    common.cfg.retention_days = "30"
                    common.cfg.retention_monthly_days = "180"
                elif retention_choice == "2":
                    common.cfg.retention_days = "60"
                    common.cfg.retention_monthly_days = "365"
                elif retention_choice == "3":
                    common.cfg.retention_days = "14"
                    common.cfg.retention_monthly_days = "90"
                else:
                    # Custom retention
                    common.cfg.retention_days = get_input("Keep ALL backups for how many days?", "30")
                    common.cfg.retention_monthly_days = get_input("Keep monthly archives for how many days?", "180")
                
                ok("Backup schedule configured")
            else:
                warn("Backup server not configured - backups will be disabled")
                say("Configure from main menu: Configure Backup Server")
                common.cfg.cron_incr_time = ""
                common.cfg.cron_full_time = ""
                common.cfg.cron_archive_time = ""
            print()
            
            # ─── Step 4: Consume Input Methods (Optional) ─────────────────────
            print(colorize("Step 4 of 5: Consume Input Methods (Optional)", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            say("Configure how documents get into Paperless:")
            say("You can enable these later from the instance menu.")
            print()
            
            print(f"  {colorize('1)', Colors.BOLD)} {colorize('Syncthing', Colors.CYAN)} - Peer-to-peer sync from your devices")
            print(f"       Best for: Mobile phones, personal computers")
            print(f"  {colorize('2)', Colors.BOLD)} {colorize('Samba', Colors.CYAN)} - Network folder (Windows/Mac compatible)")
            print(f"       Best for: Scanners, shared family access")
            print(f"  {colorize('3)', Colors.BOLD)} {colorize('SFTP', Colors.CYAN)} - Secure file transfer protocol")
            print(f"       Best for: Automated scripts, advanced users")
            print(f"  {colorize('0)', Colors.BOLD)} Skip - Configure later from instance menu")
            print()
            
            # Initialize all consume config to disabled by default
            common.cfg.consume_syncthing_enabled = "false"
            common.cfg.consume_samba_enabled = "false"
            common.cfg.consume_sftp_enabled = "false"
            
            consume_choice = get_input("Enable any consume methods? [0-3, comma-separated]", "0")
            
            if consume_choice != "0" and consume_choice.strip():
                consume_methods = [x.strip() for x in consume_choice.split(",")]
                
                if "1" in consume_methods:
                    # Enable Syncthing config - container will be started on first use
                    common.cfg.consume_syncthing_enabled = "true"
                    common.cfg.consume_syncthing_sync_port = str(find_available_port(22000))
                    common.cfg.consume_syncthing_gui_port = str(find_available_port(8384))
                    common.cfg.consume_syncthing_folder_id = f"paperless-{instance_name}"
                    common.cfg.consume_syncthing_folder_label = f"Paperless {instance_name}"
                    ok("Syncthing will be enabled after instance creation")
                
                if "2" in consume_methods:
                    # Enable Samba config - per-instance container
                    from lib.installer.consume import generate_secure_password, get_next_available_samba_port
                    common.cfg.consume_samba_enabled = "true"
                    common.cfg.consume_samba_share_name = f"paperless-{instance_name}"
                    common.cfg.consume_samba_username = f"pl-{instance_name}"
                    common.cfg.consume_samba_password = generate_secure_password()
                    common.cfg.consume_samba_port = str(get_next_available_samba_port())
                    ok("Samba share will be enabled after instance creation")
                
                if "3" in consume_methods:
                    # Enable SFTP config
                    from lib.installer.consume import generate_secure_password
                    common.cfg.consume_sftp_enabled = "true"
                    common.cfg.consume_sftp_port = str(find_available_port(2222))
                    common.cfg.consume_sftp_username = f"pl-{instance_name}"
                    common.cfg.consume_sftp_password = generate_secure_password()
                    ok("SFTP access will be enabled after instance creation")
            
            print()
            
            # ─── Step 5: Review & Create ──────────────────────────────────────
            print(colorize("Step 5 of 5: Review & Create", Colors.BOLD))
            print(colorize("─" * 40, Colors.CYAN))
            print()
            
            # Summary box
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Instance Summary', Colors.BOLD)}"))
            print(box_line(""))
            print(box_line(f" Name:     {colorize(common.cfg.instance_name, Colors.CYAN)}"))
            print(box_line(f" Data:     {common.cfg.data_root}"))
            print(box_line(f" Stack:    {common.cfg.stack_dir}"))
            print(box_line(""))
            
            # Access method
            if common.cfg.enable_traefik == "yes":
                access_str = f"🛡️ HTTPS via Traefik → https://{common.cfg.domain}"
            elif common.cfg.enable_cloudflared == "yes":
                access_str = f"☁️  Cloudflare Tunnel → https://{common.cfg.domain}"
            else:
                access_str = f"🌐 Direct HTTP → http://localhost:{common.cfg.http_port}"
            print(box_line(f" Access:   {access_str}"))
            
            if common.cfg.enable_tailscale == "yes":
                print(box_line(f"           🔐 + Tailscale private access"))
            
            # Show backup schedule summary
            print(box_line(""))
            if common.cfg.cron_full_time:
                backup_summary = []
                if common.cfg.cron_incr_time:
                    backup_summary.append("incr")
                backup_summary.append("full")
                if common.cfg.cron_archive_time:
                    backup_summary.append("archive")
                print(box_line(f" Backups:  {' + '.join(backup_summary)}, {common.cfg.retention_days}d retention"))
            else:
                print(box_line(f" Backups:  {colorize('Disabled', Colors.YELLOW)}"))
            
            # Show consume methods if any enabled
            consume_methods_enabled = []
            if common.cfg.consume_syncthing_enabled == "true":
                consume_methods_enabled.append("Syncthing")
            if common.cfg.consume_samba_enabled == "true":
                consume_methods_enabled.append("Samba")
            if common.cfg.consume_sftp_enabled == "true":
                consume_methods_enabled.append("SFTP")
            
            if consume_methods_enabled:
                print(box_line(""))
                print(box_line(f" Consume:  {', '.join(consume_methods_enabled)}"))
            
            print(box_line(""))
            print(box_line(f" Admin:    {common.cfg.paperless_admin_user}"))
            print(box_line(f" Timezone: {common.cfg.tz}"))
            print(draw_box_bottom(box_width))
            print()
            
            if not confirm("Create this instance?", True):
                say("Setup cancelled")
                input("\nPress Enter to continue...")
                return
            
            # ─── Create Instance ──────────────────────────────────────────────
            print()
            say("Creating instance...")
            
            # Create directories
            common.ensure_dir_tree(common.cfg)
            ok("Directories created")
            
            # Write config files
            files.write_env_file()
            files.write_compose_file()
            files.copy_helper_scripts()
            ok("Configuration files written")
            
            # Start containers
            say("Starting containers (this may take a moment)...")
            files.bring_up_stack()
            
            # Run self-test
            from lib.utils.selftest import run_stack_tests
            if run_stack_tests(Path(common.cfg.compose_file), Path(common.cfg.env_file)):
                ok("Health check passed")
            else:
                warn("Health check had warnings - check container logs if issues persist")
            
            # Set up backup cron if configured
            if common.cfg.cron_full_time or common.cfg.cron_incr_time:
                files.install_cron_backup()
                ok("Backup schedule installed")
            
            # Set up Cloudflare tunnel if enabled
            if common.cfg.enable_cloudflared == "yes" and net_status["cloudflared_authenticated"]:
                port = int(common.cfg.http_port)
                setup_cloudflare_tunnel(common.cfg.instance_name, common.cfg.domain, port)
            
            # Register instance
            self.instance_manager.add_instance(
                common.cfg.instance_name,
                Path(common.cfg.stack_dir),
                Path(common.cfg.data_root)
            )
            
            # Start consume containers if enabled
            consume_services_started = []
            if common.cfg.consume_syncthing_enabled == "true":
                say("Starting Syncthing container...")
                try:
                    from lib.installer.consume import start_syncthing_container, SyncthingConfig
                    syncthing_config = SyncthingConfig(
                        enabled=True,
                        sync_port=int(common.cfg.consume_syncthing_sync_port),
                        gui_port=int(common.cfg.consume_syncthing_gui_port),
                        folder_id=common.cfg.consume_syncthing_folder_id,
                        folder_label=common.cfg.consume_syncthing_folder_label
                    )
                    start_syncthing_container(
                        instance_name=instance_name,
                        config=syncthing_config,
                        consume_path=Path(common.cfg.dir_consume),
                        config_dir=Path(common.cfg.stack_dir) / "syncthing-config"
                    )
                    consume_services_started.append("Syncthing")
                except Exception as e:
                    warn(f"Failed to start Syncthing: {e}")
            
            if common.cfg.consume_samba_enabled == "true":
                say("Setting up Samba share...")
                try:
                    from lib.installer.consume import (
                        start_samba, SambaConfig
                    )
                    # Get samba port from common.cfg
                    samba_port = int(getattr(common.cfg, "consume_samba_port", "445") or "445")
                    samba_config = SambaConfig(
                        enabled=True,
                        share_name=common.cfg.consume_samba_share_name,
                        username=common.cfg.consume_samba_username,
                        password=common.cfg.consume_samba_password,
                        port=samba_port
                    )
                    consume_path = Path(common.cfg.dir_consume)
                    puid = int(common.cfg.puid)
                    pgid = int(common.cfg.pgid)
                    if start_samba(instance_name, samba_config, consume_path, puid=puid, pgid=pgid):
                        consume_services_started.append(f"Samba (port {samba_port})")
                    else:
                        warn("Failed to start Samba container")
                except Exception as e:
                    warn(f"Failed to set up Samba: {e}")
            
            if common.cfg.consume_sftp_enabled == "true":
                say("Setting up SFTP access...")
                try:
                    from lib.installer.consume import (
                        start_sftp_container, ConsumeConfig, SFTPConfig
                    )
                    sftp_config = SFTPConfig(
                        enabled=True,
                        username=common.cfg.consume_sftp_username,
                        password=common.cfg.consume_sftp_password,
                        port=int(common.cfg.consume_sftp_port)
                    )
                    # Create config for this instance
                    consume_config = ConsumeConfig()
                    consume_config.sftp = sftp_config
                    instances_config = {instance_name: consume_config}
                    data_roots = {instance_name: Path(common.cfg.data_root)}
                    start_sftp_container(instances_config, data_roots, sftp_config.port)
                    consume_services_started.append(f"SFTP (port {common.cfg.consume_sftp_port})")
                except Exception as e:
                    warn(f"Failed to set up SFTP: {e}")
            
            # Success message
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('✓ Instance Created Successfully!', Colors.GREEN)}"))
            print(box_line(""))
            if common.cfg.enable_traefik == "yes":
                print(box_line(f" Access at: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            elif common.cfg.enable_cloudflared == "yes":
                print(box_line(f" Access at: {colorize(f'https://{common.cfg.domain}', Colors.CYAN)}"))
            else:
                print(box_line(f" Access at: {colorize(f'http://localhost:{common.cfg.http_port}', Colors.CYAN)}"))
            print(box_line(""))
            print(box_line(f" Username: {colorize(common.cfg.paperless_admin_user, Colors.BOLD)}"))
            print(box_line(f" Password: {colorize(common.cfg.paperless_admin_password, Colors.BOLD)}"))
            
            # Show consume services if any were started
            if consume_services_started:
                print(box_line(""))
                print(box_line(f" {colorize('Consume Methods:', Colors.BOLD)}"))
                for svc in consume_services_started:
                    print(box_line(f"   • {svc}"))
            
            print(draw_box_bottom(box_width))
            
        except KeyboardInterrupt:
            print()
            say("Setup cancelled")
        except Exception as e:
            error(f"Failed to create instance: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def instance_detail_menu(self, instance: Instance) -> None:
        """Detail menu for a specific instance."""
        while True:
            print_header(f"Instance: {instance.name}")
            
            status = colorize("● Running", Colors.GREEN) if instance.is_running else colorize("○ Stopped", Colors.YELLOW)
            domain = instance.get_env_value("DOMAIN", "localhost")
            
            box_line, box_width = create_box_helper(80)
            
            print(draw_box_top(box_width))
            print(box_line(f" Status: {status}"))
            print(box_line(f" Domain: {colorize(domain, Colors.BOLD)}"))
            
            # Show all access URLs with emojis
            access_urls = instance.get_access_urls_formatted()
            if access_urls:
                print(box_line(f" Access:"))
                for mode_label, url in access_urls:
                    print(box_line(f"   {mode_label}: {colorize(url, Colors.CYAN)}"))
            
            # Show consume input methods status
            consume_methods = self._get_consume_methods_status(instance)
            if consume_methods:
                print(box_line(f" Consume:"))
                for method_name, is_enabled in consume_methods.items():
                    icon = colorize("✓", Colors.GREEN) if is_enabled else colorize("○", Colors.YELLOW)
                    print(box_line(f"   {icon} {method_name}"))
            
            print(box_line(f" Stack:  {instance.stack_dir}"))
            print(draw_box_bottom(box_width))
            print()
            
            options = [
                ("", colorize("Information:", Colors.BOLD)),
                ("1", "  • View full details"),
                ("2", "  • Health check"),
                ("", ""),
                ("", colorize("Operations:", Colors.BOLD)),
                ("3", "  • Update instance " + colorize("(backup + upgrade)", Colors.YELLOW)),
                ("4", "  • Backup now"),
                ("5", "  • Restore snapshot"),
                ("6", "  • Container operations"),
                ("", ""),
                ("", colorize("Configuration:", Colors.BOLD)),
                ("7", "  • Edit settings"),
                ("8", "  • " + colorize("Consume input methods", Colors.CYAN) + " (Syncthing/Samba/SFTP)"),
                ("", ""),
                ("", colorize("Danger Zone:", Colors.RED)),
                ("9", "  • " + colorize("Delete instance", Colors.RED)),
                ("", ""),
                ("0", colorize("◀ Back", Colors.CYAN))
            ]
            
            for key, desc in options:
                if key:
                    print(f"  {colorize(key + ')', Colors.BOLD)} {desc}")
                else:
                    print(f"  {desc}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self.view_instance_details(instance)
            elif choice == "2":
                self.health_check(instance)
            elif choice == "3":
                self.update_instance(instance)
            elif choice == "4":
                self.backup_instance(instance)
            elif choice == "5":
                self.revert_instance(instance)
            elif choice == "6":
                self.container_operations(instance)
            elif choice == "7":
                self.edit_instance(instance)
            elif choice == "8":
                self.consume_input_menu(instance)
            elif choice == "9":
                print()
                warn(f"This will DELETE instance '{instance.name}' completely!")
                print(f"  • Stack directory: {instance.stack_dir}")
                print(f"  • Data directory:  {instance.data_root}")
                print(f"  • Docker containers")
                print(f"  • Cloudflared service (if exists)")
                print()
                
                if confirm("Delete ALL files and containers?", False):
                    if confirm("Are you ABSOLUTELY sure? This cannot be undone!", False):
                        self.instance_manager.remove_instance(instance.name, delete_files=True)
                        ok(f"Instance '{instance.name}' completely deleted")
                        input("\nPress Enter to continue...")
                        break
                else:
                    # Just remove from tracking
                    if confirm(f"Remove from tracking only (keep files)?", False):
                        self.instance_manager.remove_instance(instance.name, delete_files=False)
                        ok(f"Instance '{instance.name}' removed from tracking")
                        input("\nPress Enter to continue...")
                        break
            else:
                warn("Invalid option")
    
    def _get_consume_methods_status(self, instance: Instance) -> dict[str, bool]:
        """Get status of consume input methods for an instance."""
        return {
            "Syncthing": instance.get_env_value("CONSUME_SYNCTHING_ENABLED", "false").lower() == "true",
            "Samba": instance.get_env_value("CONSUME_SAMBA_ENABLED", "false").lower() == "true",
            "SFTP": instance.get_env_value("CONSUME_SFTP_ENABLED", "false").lower() == "true",
        }
    
    def view_instance_details(self, instance: Instance) -> None:
        """View detailed information about an instance."""
        print_header(f"Details: {instance.name}")
        
        box_line, box_width = create_box_helper(80)
        
        # ── Basic Info ──
        print(draw_box_top(box_width))
        print(box_line(colorize(" INSTANCE OVERVIEW", Colors.BOLD)))
        print(draw_box_divider(box_width))
        print(box_line(f" Name:           {instance.name}"))
        print(box_line(f" Status:         {'● Running' if instance.is_running else '○ Stopped'}"))
        print(box_line(f" Stack Dir:      {instance.stack_dir}"))
        print(box_line(f" Data Root:      {instance.data_root}"))
        print(draw_box_bottom(box_width))
        print()
        
        # ── Access Methods ──
        print(draw_box_top(box_width))
        print(box_line(colorize(" ACCESS METHODS", Colors.BOLD)))
        print(draw_box_divider(box_width))
        
        access_urls = instance.get_access_urls_formatted()
        if access_urls:
            for mode_label, url in access_urls:
                print(box_line(f" {mode_label}: {colorize(url, Colors.CYAN)}"))
        else:
            print(box_line(" No access methods configured"))
        
        # Tailscale status
        ts_enabled = instance.get_env_value("ENABLE_TAILSCALE", "no")
        if ts_enabled == "yes":
            from lib.installer.tailscale import get_ip as get_tailscale_ip
            ts_ip = get_tailscale_ip()
            port = instance.get_env_value("HTTP_PORT", "8000")
            if ts_ip:
                print(box_line(f" 🔐 Tailscale: {colorize(f'http://{ts_ip}:{port}', Colors.CYAN)}"))
        
        print(draw_box_bottom(box_width))
        print()
        
        # ── Consume Methods ──
        print(draw_box_top(box_width))
        print(box_line(colorize(" CONSUME INPUT METHODS", Colors.BOLD)))
        print(draw_box_divider(box_width))
        
        from lib.installer.consume import (
            load_consume_config, get_syncthing_status, get_syncthing_device_id,
            is_samba_running, is_sftp_available
        )
        consume_config = load_consume_config(instance.env_file)
        
        # Syncthing
        if consume_config.syncthing.enabled:
            status = get_syncthing_status(instance.name)
            if status["running"]:
                st_status = colorize("● Running", Colors.GREEN)
            elif status["status"] == "not found":
                st_status = colorize("⚠ Not Found", Colors.YELLOW)
            else:
                st_status = colorize("○ Stopped", Colors.YELLOW)
            print(box_line(f" Syncthing:      {st_status}"))
            # Get device ID from config or API
            device_id = consume_config.syncthing.device_id or get_syncthing_device_id(instance.name)
            if device_id:
                print(box_line(f"   Device ID:    {device_id}"))
            else:
                print(box_line(f"   Device ID:    (initializing)"))
            print(box_line(f"   GUI Port:     {consume_config.syncthing.gui_port}"))
            print(box_line(f"   Sync Port:    {consume_config.syncthing.sync_port}"))
        else:
            print(box_line(f" Syncthing:      {colorize('○ Disabled', Colors.CYAN)}"))
        
        # Samba - check per-instance container status
        if consume_config.samba.enabled:
            samba_running = is_samba_running(instance.name)
            if samba_running:
                print(box_line(f" Samba:          {colorize('● Running', Colors.GREEN)}"))
            else:
                print(box_line(f" Samba:          {colorize('⚠ Not Running', Colors.YELLOW)}"))
            print(box_line(f"   Share:        {consume_config.samba.share_name}"))
            print(box_line(f"   Port:         {consume_config.samba.port}"))
            print(box_line(f"   Username:     {consume_config.samba.username}"))
        else:
            print(box_line(f" Samba:          {colorize('○ Disabled', Colors.CYAN)}"))
        
        # SFTP - check actual container status
        if consume_config.sftp.enabled:
            sftp_running = is_sftp_available()
            if sftp_running:
                print(box_line(f" SFTP:           {colorize('● Running', Colors.GREEN)}"))
            else:
                print(box_line(f" SFTP:           {colorize('⚠ Not Running', Colors.YELLOW)}"))
            print(box_line(f"   Port:         {consume_config.sftp.port}"))
            print(box_line(f"   Username:     {consume_config.sftp.username}"))
        else:
            print(box_line(f" SFTP:           {colorize('○ Disabled', Colors.CYAN)}"))
        
        print(draw_box_bottom(box_width))
        print()
        
        # ── Backup Status ──
        print(draw_box_top(box_width))
        print(box_line(colorize(" BACKUP STATUS", Colors.BOLD)))
        print(draw_box_divider(box_width))
        
        rclone_remote = instance.get_env_value("RCLONE_REMOTE_NAME", "")
        rclone_path = instance.get_env_value("RCLONE_REMOTE_PATH", "")
        
        if rclone_remote and rclone_path:
            print(box_line(f" Remote:         {rclone_remote}:{rclone_path}"))
            
            # Get snapshot count, size, and last backup
            try:
                snap_count = count_snapshots(f"{rclone_remote}:{rclone_path}")
                backup_size = get_backup_size(f"{rclone_remote}:{rclone_path}")
                print(box_line(f" Snapshots:      {snap_count}"))
                print(box_line(f" Total Size:     {backup_size}"))
                
                # Get last backup date from latest snapshot name
                if snap_count > 0:
                    result = subprocess.run(
                        ["rclone", "lsd", f"{rclone_remote}:{rclone_path}"],
                        capture_output=True, text=True, check=False, timeout=10
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        snapshots = sorted([l.split()[-1] for l in result.stdout.splitlines() if l.strip()], reverse=True)
                        if snapshots:
                            # Parse snapshot name like "2026-01-20_08-14-32"
                            latest = snapshots[0]
                            try:
                                # Format nicely: "2026-01-20 08:14"
                                # Split into date and time parts
                                if "_" in latest:
                                    date_str, time_str = latest.split("_", 1)
                                    # time_str is like "08-14-32", convert to "08:14"
                                    time_parts = time_str.split("-")
                                    if len(time_parts) >= 2:
                                        formatted_time = f"{time_parts[0]}:{time_parts[1]}"
                                        print(box_line(f" Last Backup:    {date_str} {formatted_time}"))
                                    else:
                                        print(box_line(f" Last Backup:    {latest}"))
                                else:
                                    print(box_line(f" Last Backup:    {latest}"))
                            except:
                                print(box_line(f" Last Backup:    {latest}"))
            except:
                print(box_line(f" Snapshots:      (unable to fetch)"))
            
            # Backup schedule from env (actual var names are CRON_*_TIME)
            schedule_incr = instance.get_env_value("CRON_INCR_TIME", "")
            schedule_full = instance.get_env_value("CRON_FULL_TIME", "")
            schedule_archive = instance.get_env_value("CRON_ARCHIVE_TIME", "")
            
            if schedule_incr or schedule_full or schedule_archive:
                print(box_line(f""))
                print(box_line(colorize(" Schedule:", Colors.BOLD)))
                if schedule_incr:
                    # Parse cron: "0 */6 * * *" -> "Every 6 hours"
                    parts = schedule_incr.split()
                    if len(parts) >= 2 and parts[1].startswith("*/"):
                        hours = parts[1].replace("*/", "")
                        print(box_line(f"   Incremental:  Every {hours} hours"))
                    else:
                        print(box_line(f"   Incremental:  {schedule_incr}"))
                if schedule_full:
                    # Parse cron: "30 3 * * 0" -> "Weekly (Sun 03:30)"
                    parts = schedule_full.split()
                    if len(parts) >= 5:
                        minute, hour = parts[0], parts[1]
                        day_of_week = parts[4] if len(parts) > 4 else "*"
                        days = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}
                        day_name = days.get(day_of_week, day_of_week)
                        if day_of_week != "*":
                            print(box_line(f"   Full:         Weekly ({day_name} {hour.zfill(2)}:{minute.zfill(2)})"))
                        else:
                            print(box_line(f"   Full:         Daily ({hour.zfill(2)}:{minute.zfill(2)})"))
                    else:
                        print(box_line(f"   Full:         {schedule_full}"))
                if schedule_archive:
                    # Parse cron: "0 4 1 * *" -> "Monthly (1st at 04:00)"
                    parts = schedule_archive.split()
                    if len(parts) >= 4:
                        minute, hour, day_of_month = parts[0], parts[1], parts[2]
                        if day_of_month != "*":
                            suffix = "th" if 4 <= int(day_of_month) <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(int(day_of_month) % 10, "th")
                            print(box_line(f"   Archive:      Monthly ({day_of_month}{suffix} at {hour.zfill(2)}:{minute.zfill(2)})"))
                        else:
                            print(box_line(f"   Archive:      {schedule_archive}"))
                    else:
                        print(box_line(f"   Archive:      {schedule_archive}"))
                
                # Retention policy
                retention = instance.get_env_value("RETENTION_DAYS", "")
                archive_retention = instance.get_env_value("ARCHIVE_RETENTION_MONTHS", "")
                if retention or archive_retention:
                    retention_str = f"{retention}d" if retention else ""
                    archive_str = f", archives {archive_retention}mo" if archive_retention else ""
                    print(box_line(f"   Retention:    Keep {retention_str}{archive_str}"))
        else:
            print(box_line(f" {colorize('Not configured', Colors.YELLOW)}"))
        
        print(draw_box_bottom(box_width))
        print()
        
        # ── Key Settings ──
        print(draw_box_top(box_width))
        print(box_line(colorize(" KEY SETTINGS", Colors.BOLD)))
        print(draw_box_divider(box_width))
        
        settings = [
            ("PAPERLESS_URL", "Paperless URL"),
            ("DOMAIN", "Domain"),
            ("HTTP_PORT", "HTTP Port"),
            ("TZ", "Timezone"),
            ("PAPERLESS_ADMIN_USER", "Admin User"),
            ("POSTGRES_DB", "Database"),
        ]
        for key, label in settings:
            value = instance.get_env_value(key, "not set")
            # Mask sensitive values
            print(box_line(f" {label + ':':<16} {value}"))
        
        print(draw_box_divider(box_width))
        print(box_line(colorize(" Network Features:", Colors.BOLD)))
        
        features = [
            ("ENABLE_TRAEFIK", "Traefik (HTTPS)"),
            ("ENABLE_CLOUDFLARED", "Cloudflare Tunnel"),
            ("ENABLE_TAILSCALE", "Tailscale"),
        ]
        for key, label in features:
            value = instance.get_env_value(key, "no")
            if value == "yes":
                print(box_line(f" {label + ':':<20} {colorize('✓ Enabled', Colors.GREEN)}"))
            else:
                print(box_line(f" {label + ':':<20} {colorize('○ Disabled', Colors.CYAN)}"))
        
        print(draw_box_bottom(box_width))
        
        input("\nPress Enter to continue...")
    
    def health_check(self, instance: Instance) -> None:
        """Run health check on instance."""
        # Auto-repair missing CSRF settings
        self._repair_csrf_settings(instance)
        
        checker = HealthChecker(instance)
        checker.print_report()
        input("\nPress Enter to continue...")
    
    def _repair_csrf_settings(self, instance: Instance) -> None:
        """Auto-repair missing PAPERLESS_CSRF_TRUSTED_ORIGINS settings."""
        try:
            # Check if CSRF setting is missing
            csrf_value = instance.get_env_value("PAPERLESS_CSRF_TRUSTED_ORIGINS", "")
            
            if not csrf_value:
                # Determine the correct CSRF origins based on networking
                enable_traefik = instance.get_env_value("ENABLE_TRAEFIK", "no")
                enable_cloudflared = instance.get_env_value("ENABLE_CLOUDFLARED", "no")
                
                if enable_traefik == "yes" or enable_cloudflared == "yes":
                    domain = instance.get_env_value("DOMAIN", "localhost")
                    csrf_origins = f"https://{domain},http://localhost"
                else:
                    csrf_origins = "http://localhost"
                
                # Update the .env file
                say("Repairing missing CSRF settings...")
                self._update_instance_env(instance, "PAPERLESS_CSRF_TRUSTED_ORIGINS", csrf_origins)
                ok(f"CSRF settings repaired: {csrf_origins}")
                
                # Also ensure docker-compose.yml has the reference
                # Check if compose file already has CSRF reference
                if instance.compose_file.exists():
                    compose_content = instance.compose_file.read_text()
                    if "PAPERLESS_CSRF_TRUSTED_ORIGINS" not in compose_content:
                        warn("Docker-compose.yml needs regeneration")
                        if confirm("Regenerate docker-compose.yml now?", True):
                            self._offer_regenerate_compose(instance, skip_confirm=True)
                            return
        except Exception as e:
            # Don't fail health check on repair error
            pass
    
    def _ensure_consume_services(self, instance: Instance) -> None:
        """Ensure consume services (Samba/SFTP) are running for an instance.
        
        This is called after individual instance restore to ensure the
        per-instance Samba container and shared SFTP container are running.
        Syncthing is handled by restore.py directly.
        """
        try:
            from lib.installer.consume import (
                load_consume_config, save_consume_config, is_samba_running, is_sftp_available,
                start_samba, start_sftp_container, restart_sftp_with_config,
                get_used_samba_ports, get_next_available_samba_port, get_instance_puid_pgid
            )
            
            config = load_consume_config(instance.env_file)
            
            # Handle Samba - per-instance container
            if config.samba.enabled:
                consume_path = instance.data_root / "consume"
                if not is_samba_running(instance.name):
                    # Check for port conflicts - another instance might have taken this port
                    used_ports = get_used_samba_ports()
                    if config.samba.port in used_ports:
                        # Port conflict! Assign a new port
                        old_port = config.samba.port
                        config.samba.port = get_next_available_samba_port()
                        save_consume_config(config, instance.env_file)
                        self._update_instance_env(instance, "CONSUME_SAMBA_PORT", str(config.samba.port))
                        warn(f"Samba port {old_port} in use, reassigned to {config.samba.port}")
                    
                    # Get UID/GID that Paperless actually uses
                    puid, pgid = get_instance_puid_pgid(instance.name)
                    if start_samba(instance.name, config.samba, consume_path, puid=puid, pgid=pgid):
                        ok(f"Samba started for {instance.name} on port {config.samba.port}")
                    else:
                        warn(f"Failed to start Samba for {instance.name}")
                else:
                    ok(f"Samba already running for {instance.name}")
            
            # Handle SFTP - shared container
            if config.sftp.enabled:
                # Collect all instance configs for shared SFTP container
                instances_config = {}
                data_roots = {}
                for inst in self.instance_manager.list_instances():
                    try:
                        inst_config = load_consume_config(inst.env_file)
                        instances_config[inst.name] = inst_config
                        data_roots[inst.name] = inst.data_root
                    except:
                        pass
                
                # Start or restart SFTP container
                if not is_sftp_available():
                    start_sftp_container(instances_config, data_roots)
                else:
                    # Restart to ensure new user is included
                    restart_sftp_with_config(instances_config, data_roots)
                ok("SFTP configured")
                
        except Exception as e:
            warn(f"Could not ensure consume services: {e}")
    
    def update_instance(self, instance: Instance) -> None:
        """Update instance with automatic backup and Docker version tracking."""
        print_header(f"Update Instance: {instance.name}")
        
        if not self.rclone_configured:
            warn("Backup server not configured - updates without backup are risky!")
            if not confirm("Continue anyway?", False):
                return
        
        say("This will:")
        print("  1. Create a FULL backup (with current Docker versions)")
        print("  2. Pull latest container images")
        print("  3. Recreate containers with new images")
        print("  4. Test health")
        print("  5. If it fails, you can restore from the backup\n")
        
        if not confirm("Continue with update?", True):
            return
        
        # Step 1: Full backup with Docker versions
        if self.rclone_configured:
            say("Creating full backup before update...")
            backup_mgr = BackupManager(instance)
            if not backup_mgr.run_backup("full"):
                error("Backup failed! Update aborted for safety.")
                input("\nPress Enter to continue...")
                return
            ok("Backup completed with Docker version info")
            print()
        
        # Step 2: Get current image versions before upgrade
        say("Recording current Docker versions...")
        current_versions = {}
        try:
            result = subprocess.run(
                [
                    "docker", "compose",
                    "-f", str(instance.compose_file),
                    "images", "--format", "{{.Service}}: {{.Repository}}:{{.Tag}}"
                ],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if ":" in line:
                        current_versions[line.split(":")[0].strip()] = line
                print("Current versions:")
                for version in current_versions.values():
                    print(f"  {version}")
                print()
        except Exception as e:
            warn(f"Could not capture current versions: {e}")
        
        # Step 3: Pull latest images
        say("Pulling latest container images...")
        try:
            self._docker_command(instance, "pull")
            ok("Images pulled successfully")
            print()
        except subprocess.CalledProcessError:
            error("Failed to pull images")
            input("\nPress Enter to continue...")
            return
        
        # Step 4: Recreate containers
        say("Recreating containers with new images...")
        try:
            self._docker_command(instance, "up", "-d", "--force-recreate")
            ok("Containers recreated")
            print()
        except subprocess.CalledProcessError:
            error("Failed to recreate containers!")
            warn("You may need to restore from backup")
            input("\nPress Enter to continue...")
            return
        
        # Step 5: Wait a moment for containers to stabilize
        say("Waiting for containers to stabilize...")
        import time
        time.sleep(10)
        
        # Step 6: Health check
        say("Running health check...")
        checker = HealthChecker(instance)
        checks = checker.check_all()
        
        passed = sum(checks.values())
        total = len(checks)
        
        print()
        if passed == total:
            ok(f"✓ Update successful! All {total} health checks passed")
            say("Your instance is now running the latest container versions")
        else:
            warn(f"⚠ Update completed but {total - passed}/{total} health checks failed")
            error("Instance may not be fully functional")
            print()
            print("You can:")
            print(f"  1. Check logs: docker compose -f {instance.compose_file} logs")
            print("  2. Restore from backup (will restore previous working versions)")
            print()
        
        # Show new versions
        try:
            result = subprocess.run(
                [
                    "docker", "compose",
                    "-f", str(instance.compose_file),
                    "images", "--format", "{{.Service}}: {{.Repository}}:{{.Tag}}"
                ],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                print("\nNew versions:")
                for line in result.stdout.strip().splitlines():
                    print(f"  {line}")
        except Exception:
            pass
        
        input("\nPress Enter to continue...")
    
    def backup_instance(self, instance: Instance) -> None:
        """Backup an instance."""
        if not self.rclone_configured:
            error("Backup server not configured!")
            input("\nPress Enter to continue...")
            return
        
        print_header(f"Backup: {instance.name}")
        
        options = [
            ("1", "Incremental backup"),
            ("2", "Full backup"),
            ("3", "Archive backup"),
            ("0", "Cancel")
        ]
        print_menu(options)
        
        choice = get_input("Select backup type", "1")
        
        mode_map = {"1": "incr", "2": "full", "3": "archive"}
        
        if choice in mode_map:
            backup_mgr = BackupManager(instance)
            say(f"Starting {mode_map[choice]} backup...")
            if backup_mgr.run_backup(mode_map[choice]):
                ok("Backup completed successfully!")
            else:
                error("Backup failed!")
        
        input("\nPress Enter to continue...")
    
    def revert_instance(self, instance: Instance) -> None:
        """Revert instance from backup."""
        if not self.rclone_configured:
            error("Backup server not configured!")
            input("\nPress Enter to continue...")
            return
        
        print_header(f"Restore/Revert: {instance.name}")
        
        backup_mgr = BackupManager(instance)
        snapshots = backup_mgr.fetch_snapshots()
        
        if not snapshots:
            warn("No backups found for this instance")
            input("\nPress Enter to continue...")
            return
        
        print(f"{'#':<5} {'Name':<35} {'Mode':<10} {'Parent'}")
        print("─" * 80)
        
        for idx, (name, mode, parent) in enumerate(snapshots, 1):
            parent_display = parent if mode == "incr" else "-"
            mode_color = Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN
            latest_marker = " (latest)" if idx == 1 else ""
            print(f"{idx:<5} {name:<35} {colorize(mode, mode_color):<20} {parent_display}{latest_marker}")
        print()
        
        say("Tip: Enter 'L' for latest snapshot")
        choice = get_input(f"Select snapshot [1-{len(snapshots)}, L=latest] or 'cancel'", "cancel")
        
        # Handle 'latest' shortcut
        if choice.lower() == 'l':
            choice = "1"  # Latest is now first in list
        
        if choice.isdigit() and 1 <= int(choice) <= len(snapshots):
            snapshot = snapshots[int(choice) - 1][0]
            
            print()
            warn("This will stop the instance and restore data!")
            if confirm("Continue with restore?", False):
                say("Starting restore...")
                if backup_mgr.run_restore(snapshot):
                    ok("Restore completed!")
                    # Auto-repair CSRF settings after restore (handles old backups)
                    self._repair_csrf_settings(instance)
                    # Ensure consume services are running for this instance
                    self._ensure_consume_services(instance)
                else:
                    error("Restore failed!")
        
        input("\nPress Enter to continue...")
    
    def container_operations(self, instance: Instance) -> None:
        """Container operations for an instance."""
        while True:
            print_header(f"Containers: {instance.name}")
            
            options = [
                ("1", "Start containers"),
                ("2", "Stop containers"),
                ("3", "Restart containers"),
                ("4", "View status"),
                ("5", "View logs"),
                ("6", "Upgrade containers"),
                ("7", "Regenerate docker-compose.yml"),
                ("0", "Back")
            ]
            print_menu(options)
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._docker_command(instance, "up", "-d")
                input("\nPress Enter to continue...")
            elif choice == "2":
                self._docker_command(instance, "down")
                input("\nPress Enter to continue...")
            elif choice == "3":
                self._docker_command(instance, "restart")
                input("\nPress Enter to continue...")
            elif choice == "4":
                self._docker_command(instance, "ps")
                input("\nPress Enter to continue...")
            elif choice == "5":
                self._view_logs(instance)
            elif choice == "6":
                self._upgrade_containers(instance)
            elif choice == "7":
                self._offer_regenerate_compose(instance)
            else:
                warn("Invalid option")
    
    def _docker_command(self, instance: Instance, *args: str) -> None:
        """Run a docker compose command."""
        cmd = [
            "docker", "compose",
            "-f", str(instance.compose_file),
            "--env-file", str(instance.env_file),
            *args
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            error(f"Command failed with exit code {e.returncode}")
    
    def _view_logs(self, instance: Instance) -> None:
        """View container logs."""
        service = get_input("Service name (blank for all)", "")
        cmd = [
            "docker", "compose",
            "-f", str(instance.compose_file),
            "--env-file", str(instance.env_file),
            "logs", "--tail", "100", "--timestamps"
        ]
        if service:
            cmd.append(service)
        
        try:
            subprocess.run(cmd)
        except subprocess.CalledProcessError:
            error("Failed to view logs")
        
        input("\nPress Enter to continue...")
    
    def _upgrade_containers(self, instance: Instance) -> None:
        """Upgrade containers with automatic backup."""
        if confirm("Run backup before upgrade?", True):
            backup_mgr = BackupManager(instance)
            say("Running full backup before upgrade...")
            if not backup_mgr.run_backup("full"):
                error("Backup failed! Upgrade aborted.")
                input("\nPress Enter to continue...")
                return
            ok("Backup completed")
        
        say("Pulling latest images...")
        self._docker_command(instance, "pull")
        
        say("Recreating containers...")
        self._docker_command(instance, "up", "-d")
        
        ok("Upgrade complete!")
        input("\nPress Enter to continue...")
    
    def edit_instance(self, instance: Instance) -> None:
        """Edit instance settings - networking, domain, ports, backup schedule, etc."""
        while True:
            print_header(f"Edit: {instance.name}")
            
            # Show current settings
            box_line, box_width = create_box_helper(80)
            print(draw_box_top(box_width))
            print(box_line(f" Status: {'Running' if instance.is_running else 'Stopped'}"))
            print(box_line(f""))
            print(box_line(f" {colorize('Current Settings:', Colors.BOLD)}"))
            print(box_line(f"   Domain:        {instance.get_env_value('DOMAIN', 'localhost')}"))
            print(box_line(f"   HTTP Port:     {instance.get_env_value('HTTP_PORT', '8000')}"))
            print(box_line(f"   Traefik:       {instance.get_env_value('ENABLE_TRAEFIK', 'no')}"))
            print(box_line(f"   Cloudflare:    {instance.get_env_value('ENABLE_CLOUDFLARED', 'no')}"))
            print(box_line(f"   Tailscale:     {instance.get_env_value('ENABLE_TAILSCALE', 'no')}"))
            print(draw_box_divider(box_width))
            # Backup schedule info
            cron_incr = instance.get_env_value('CRON_INCR_TIME', '')
            cron_full = instance.get_env_value('CRON_FULL_TIME', '')
            cron_archive = instance.get_env_value('CRON_ARCHIVE_TIME', '')
            retention = instance.get_env_value('RETENTION_DAYS', '30')
            retention_monthly = instance.get_env_value('RETENTION_MONTHLY_DAYS', '180')
            
            backup_parts = []
            if cron_incr:
                backup_parts.append("incr")
            if cron_full:
                backup_parts.append("full")
            if cron_archive:
                backup_parts.append("archive")
            backup_str = " + ".join(backup_parts) if backup_parts else "Disabled"
            
            print(box_line(f" {colorize('Backup Schedule:', Colors.BOLD)}"))
            print(box_line(f"   Schedule:      {backup_str}"))
            print(box_line(f"   Retention:     {retention}d all, {retention_monthly}d monthly"))
            print(draw_box_bottom(box_width))
            print()
            
            # Show active access methods with emojis
            access_urls = instance.get_access_urls_formatted()
            if access_urls:
                print(colorize("Active Access Methods:", Colors.BOLD))
                for mode_label, url in access_urls:
                    print(f"  {mode_label}: {colorize(url, Colors.CYAN)}")
                print()
            
            options = [
                ("", colorize("Networking:", Colors.BOLD)),
                ("1", "  Change domain"),
                ("2", "  Change HTTP port"),
                ("3", "  Toggle Traefik (HTTPS)"),
                ("4", "  Toggle Cloudflare Tunnel"),
                ("5", "  Toggle Tailscale"),
                ("", ""),
                ("", colorize("Credentials:", Colors.BOLD)),
                ("6", "  Change admin password"),
                ("", ""),
                ("", colorize("Backups:", Colors.BOLD)),
                ("7", "  Change backup schedule"),
                ("", ""),
                ("0", colorize("◀ Back", Colors.CYAN))
            ]
            
            for key, desc in options:
                if key:
                    print(f"  {colorize(key + ')', Colors.BOLD)} {desc}")
                else:
                    print(f"  {desc}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._edit_instance_domain(instance)
            elif choice == "2":
                self._edit_instance_port(instance)
            elif choice == "3":
                self._toggle_instance_traefik(instance)
            elif choice == "4":
                self._toggle_instance_cloudflare(instance)
            elif choice == "5":
                self._toggle_instance_tailscale(instance)
            elif choice == "6":
                self._edit_instance_admin_password(instance)
            elif choice == "7":
                self._edit_instance_backup_schedule(instance)
            else:
                warn("Invalid option")
    
    def consume_input_menu(self, instance: Instance) -> None:
        """Configure consume folder input methods (Syncthing, Samba, SFTP)."""
        from lib.installer.consume import (
            load_consume_config, get_syncthing_status, load_global_consume_config,
            is_samba_running, is_sftp_available
        )
        
        while True:
            print_header(f"Consume Input Methods: {instance.name}")
            
            # Load current config from env file
            config = load_consume_config(instance.env_file)
            global_config = load_global_consume_config()
            
            box_line, box_width = create_box_helper(80)
            print(draw_box_top(box_width))
            print(box_line(f" Configure how documents get into your Paperless consume folder"))
            print(box_line(f""))
            print(box_line(f" Consume folder: {instance.data_root / 'consume'}"))
            print(draw_box_divider(box_width))
            
            # Syncthing status - brief summary
            if config.syncthing.enabled:
                syncthing_live_status = get_syncthing_status(instance.name)
                if syncthing_live_status["running"]:
                    uptime_str = f" (up {syncthing_live_status['uptime']})" if syncthing_live_status.get('uptime') else ""
                    syncthing_status = colorize(f"✓ RUNNING{uptime_str}", Colors.GREEN)
                elif syncthing_live_status["status"] == "exited":
                    syncthing_status = colorize(f"✗ CRASHED", Colors.RED)
                elif syncthing_live_status["status"] == "not found":
                    syncthing_status = colorize(f"⚠ NOT FOUND", Colors.YELLOW)
                else:
                    syncthing_status = colorize(f"⚠ {syncthing_live_status['status'].upper()}", Colors.YELLOW)
            else:
                syncthing_status = colorize("○ Disabled", Colors.YELLOW)
            print(box_line(f" {colorize('Syncthing:', Colors.BOLD)} {syncthing_status}"))
            
            # Samba status - check per-instance container
            if config.samba.enabled:
                samba_running = is_samba_running(instance.name)
                if samba_running:
                    ts_note = " (Tailscale only)" if global_config.samba_tailscale_only else ""
                    port_note = f" port {config.samba.port}"
                    samba_status = colorize(f"✓ RUNNING{port_note}{ts_note}", Colors.GREEN)
                else:
                    samba_status = colorize("⚠ NOT RUNNING", Colors.YELLOW)
            else:
                samba_status = colorize("○ Disabled", Colors.YELLOW)
            print(box_line(f" {colorize('Samba:', Colors.BOLD)} {samba_status}"))
            
            # SFTP status - check actual container, not just config
            if config.sftp.enabled:
                sftp_running = is_sftp_available()
                if sftp_running:
                    ts_note = " (Tailscale only)" if global_config.sftp_tailscale_only else ""
                    sftp_status = colorize(f"✓ RUNNING{ts_note}", Colors.GREEN)
                else:
                    sftp_status = colorize("⚠ NOT RUNNING", Colors.YELLOW)
            else:
                sftp_status = colorize("○ Disabled", Colors.YELLOW)
            print(box_line(f" {colorize('SFTP:', Colors.BOLD)} {sftp_status}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # Build clean menu
            print(colorize("  ── Services ──", Colors.CYAN))
            print(f"  {colorize('1)', Colors.BOLD)} {'Disable' if config.syncthing.enabled else 'Enable'} Syncthing")
            print(f"  {colorize('2)', Colors.BOLD)} {'Disable' if config.samba.enabled else 'Enable'} Samba")
            print(f"  {colorize('3)', Colors.BOLD)} {'Disable' if config.sftp.enabled else 'Enable'} SFTP")
            print()
            
            # Management submenus for enabled services
            has_management = config.syncthing.enabled or config.samba.enabled or config.sftp.enabled
            if has_management:
                print(colorize("  ── Manage ──", Colors.CYAN))
                if config.syncthing.enabled:
                    print(f"  {colorize('4)', Colors.BOLD)} Manage Syncthing →")
                if config.samba.enabled:
                    print(f"  {colorize('5)', Colors.BOLD)} Manage Samba →")
                if config.sftp.enabled:
                    print(f"  {colorize('6)', Colors.BOLD)} View SFTP credentials")
                print()
            
            # SFTP is the only global service now
            print(colorize("  ── Global Settings ──", Colors.CYAN))
            print(f"  {colorize('7)', Colors.BOLD)} SFTP network access (affects all instances)")
            print()
            
            print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._toggle_syncthing(instance, config)
            elif choice == "2":
                self._toggle_samba(instance, config)
            elif choice == "3":
                self._toggle_sftp(instance, config)
            elif choice == "4" and config.syncthing.enabled:
                self._manage_syncthing_menu(instance, config)
            elif choice == "5" and config.samba.enabled:
                self._manage_samba_menu(instance, config)
            elif choice == "6" and config.sftp.enabled:
                self._show_sftp_credentials(instance, config)
            elif choice == "7":
                self._sftp_network_settings_menu()
            else:
                warn("Invalid option")
    
    def _global_consume_settings_menu(self) -> None:
        """Configure global network access settings for Samba/SFTP."""
        from lib.installer.consume import (
            load_global_consume_config, save_global_consume_config,
            load_consume_config, start_samba, is_samba_running, is_sftp_available
        )
        from lib.installer.tailscale import get_ip as get_tailscale_ip, is_tailscale_installed
        
        while True:
            print_header("Network Access Settings (Global)")
            
            config = load_global_consume_config()
            ts_ip = get_tailscale_ip()
            local_ip = get_local_ip()
            
            box_line, box_width = create_box_helper(80)
            print(draw_box_top(box_width))
            print(box_line(f" These settings affect ALL instances using Samba/SFTP"))
            print(draw_box_divider(box_width))
            
            # Network status
            if ts_ip:
                print(box_line(f" Tailscale:   {colorize('● Connected', Colors.GREEN)} ({ts_ip})"))
            elif is_tailscale_installed():
                print(box_line(f" Tailscale:   {colorize('○ Not connected', Colors.YELLOW)}"))
            else:
                print(box_line(f" Tailscale:   {colorize('○ Not installed', Colors.YELLOW)}"))
            print(box_line(f" External IP: {local_ip}"))
            print(draw_box_divider(box_width))
            
            # Samba access mode
            if config.samba_tailscale_only:
                samba_mode = colorize("Tailscale only", Colors.GREEN) + " (most secure)"
            else:
                samba_mode = colorize("All networks", Colors.YELLOW) + " (external + Tailscale)"
            print(box_line(f" Samba:       {samba_mode}"))
            
            # SFTP access mode
            if config.sftp_tailscale_only:
                sftp_mode = colorize("Tailscale only", Colors.GREEN) + " (most secure)"
            else:
                sftp_mode = colorize("All networks", Colors.YELLOW) + " (external + Tailscale)"
            print(box_line(f" SFTP:        {sftp_mode}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # Menu
            print(colorize("  ── Access Mode ──", Colors.CYAN))
            samba_action = "Allow external access" if config.samba_tailscale_only else "Restrict to Tailscale only"
            sftp_action = "Allow external access" if config.sftp_tailscale_only else "Restrict to Tailscale only"
            print(f"  {colorize('1)', Colors.BOLD)} Samba: {samba_action}")
            print(f"  {colorize('2)', Colors.BOLD)} SFTP: {sftp_action}")
            print()
            print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                # Toggle Samba tailscale_only
                new_value = not config.samba_tailscale_only
                if new_value and not ts_ip:
                    print()
                    error("Cannot enable Tailscale-only mode: Tailscale is not connected!")
                    if not is_tailscale_installed():
                        say("Install Tailscale first, then try again.")
                    else:
                        say("Connect to Tailscale first, then try again.")
                    input("\nPress Enter to continue...")
                    continue
                
                config.samba_tailscale_only = new_value
                save_global_consume_config(config)
                
                # Restart all Samba containers to apply network binding changes
                say("Restarting Samba containers to apply changes...")
                for inst in self.instance_manager.list_instances():
                    try:
                        inst_config = load_consume_config(inst.env_file)
                        if inst_config.samba.enabled and is_samba_running(inst.name):
                            consume_path = inst.data_root / "consume"
                            puid = int(inst.get_env_value("PUID", "1000"))
                            pgid = int(inst.get_env_value("PGID", "1000"))
                            start_samba(inst.name, inst_config.samba, consume_path, puid=puid, pgid=pgid)
                    except Exception:
                        pass
                
                if new_value:
                    ok("Samba restricted to Tailscale network only")
                else:
                    ok("Samba accessible from all networks")
                input("\nPress Enter to continue...")
                
            elif choice == "2":
                # Toggle SFTP tailscale_only
                new_value = not config.sftp_tailscale_only
                if new_value and not ts_ip:
                    print()
                    error("Cannot enable Tailscale-only mode: Tailscale is not connected!")
                    if not is_tailscale_installed():
                        say("Install Tailscale first, then try again.")
                    else:
                        say("Connect to Tailscale first, then try again.")
                    input("\nPress Enter to continue...")
                    continue
                
                config.sftp_tailscale_only = new_value
                save_global_consume_config(config)
                
                # Would need to restart SFTP container - but need instance configs
                # For now just show message
                if is_sftp_available():
                    warn("Restart SFTP container manually to apply changes")
                    say("(Disable and re-enable SFTP from any instance)")
                
                if new_value:
                    ok("SFTP restricted to Tailscale network only")
                else:
                    ok("SFTP accessible from all networks")
                input("\nPress Enter to continue...")
            else:
                warn("Invalid option")
    
    def _manage_syncthing_menu(self, instance: Instance, config) -> None:
        """Syncthing management submenu with live status dashboard."""
        from lib.installer.consume import (
            get_syncthing_status, get_syncthing_logs, list_syncthing_devices,
            restart_syncthing_container, initialize_syncthing, get_syncthing_device_id,
            generate_syncthing_guide, get_pending_devices
        )
        
        while True:
            print_header(f"Manage Syncthing: {instance.name}")
            
            config_dir = instance.stack_dir / "syncthing-config"
            status = get_syncthing_status(instance.name)
            local_ip = get_local_ip()
            
            # ── Live Dashboard ──
            box_line, box_width = create_box_helper(80)
            print(draw_box_top(box_width))
            print(box_line(colorize(" SYNCTHING STATUS DASHBOARD", Colors.BOLD)))
            print(draw_box_divider(box_width))
            
            # Container status
            if status["running"]:
                uptime = status.get('uptime', '?')
                container_status = colorize(f"● Running (uptime: {uptime})", Colors.GREEN)
            elif status["status"] == "exited":
                container_status = colorize(f"✗ Crashed (exit code: {status.get('exit_code', '?')})", Colors.RED)
            elif status["status"] == "not found":
                container_status = colorize("✗ Container not found", Colors.RED)
            else:
                container_status = colorize(f"⚠ {status['status']}", Colors.YELLOW)
            print(box_line(f" Container:  {container_status}"))
            
            # Device ID
            device_id = config.syncthing.device_id or get_syncthing_device_id(instance.name)
            if device_id:
                print(box_line(f" Device ID:  {device_id}"))
            else:
                print(box_line(f" Device ID:  {colorize('Not available', Colors.RED)}"))
            
            # Web UI access info
            from lib.installer.tailscale import get_ip as get_tailscale_ip, is_tailscale_installed
            ts_ip = get_tailscale_ip()
            gui_port = config.syncthing.gui_port or 8384
            if ts_ip:
                print(box_line(f" Web UI:     http://{ts_ip}:{gui_port} (Tailscale)"))
            elif is_tailscale_installed():
                print(box_line(f" Web UI:     {colorize('Tailscale not connected', Colors.YELLOW)}"))
            else:
                print(box_line(f" Web UI:     localhost:{gui_port} (install Tailscale for remote access)"))
            print(box_line(f" Sync Port:  {config.syncthing.sync_port} (TCP/UDP)"))
            
            print(draw_box_divider(box_width))
            
            # Connected devices
            devices = []
            pending_devices = []
            if status["running"]:
                devices = list_syncthing_devices(instance.name, config.syncthing, config_dir)
                pending_devices = get_pending_devices(instance.name, config.syncthing, config_dir)
            
            connected = [d for d in devices if d.get("connected")]
            disconnected = [d for d in devices if not d.get("connected")]
            
            print(box_line(colorize(f" DEVICES ({len(devices)} configured)", Colors.BOLD)))
            if devices:
                for d in connected:
                    print(box_line(f"   {colorize('●', Colors.GREEN)} {d['name']} - Connected"))
                for d in disconnected:
                    print(box_line(f"   {colorize('○', Colors.YELLOW)} {d['name']} - Disconnected"))
            else:
                print(box_line(f"   No devices configured yet"))
            
            # Show pending devices (trying to connect but not trusted)
            if pending_devices:
                print(box_line(f""))
                print(box_line(colorize(f" PENDING ({len(pending_devices)} waiting to be added)", Colors.YELLOW)))
                for p in pending_devices[:3]:  # Show max 3
                    name = p.get('name', 'Unknown')[:30]
                    short_id = p['deviceID'][:7]
                    print(box_line(f"   {colorize('⏳', Colors.YELLOW)} {name} ({short_id}...)"))
            
            print(draw_box_divider(box_width))
            
            # Recent activity (last 5 log lines, cleaned up)
            print(box_line(colorize(" RECENT ACTIVITY", Colors.BOLD)))
            if status["running"] or status["status"] == "exited":
                logs = get_syncthing_logs(instance.name, 5)
                for line in logs.strip().split("\n")[-5:]:
                    if line.strip():
                        # Extract just the message part
                        if " INF " in line:
                            msg = line.split(" INF ", 1)[-1][:60]
                            print(box_line(f"   {colorize('ℹ', Colors.CYAN)} {msg}"))
                        elif " WRN " in line:
                            msg = line.split(" WRN ", 1)[-1][:60]
                            print(box_line(f"   {colorize('⚠', Colors.YELLOW)} {msg}"))
                        elif " ERR " in line:
                            msg = line.split(" ERR ", 1)[-1][:60]
                            print(box_line(f"   {colorize('✗', Colors.RED)} {msg}"))
            else:
                print(box_line(f"   No logs available"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # Menu options
            print(colorize("  ── Devices ──", Colors.CYAN))
            if pending_devices:
                print(f"  {colorize('1)', Colors.BOLD)} {colorize('Accept pending device', Colors.GREEN)} ({len(pending_devices)} waiting)")
            else:
                print(f"  {colorize('1)', Colors.BOLD)} Add a device manually")
            if devices:
                print(f"  {colorize('2)', Colors.BOLD)} Remove a device")
            print()
            
            print(colorize("  ── Help & Troubleshooting ──", Colors.CYAN))
            print(f"  {colorize('3)', Colors.BOLD)} View setup guide")
            print(f"  {colorize('4)', Colors.BOLD)} View full logs")
            print(f"  {colorize('5)', Colors.BOLD)} Restart container")
            print(f"  {colorize('6)', Colors.BOLD)} Recreate container")
            print(f"  {colorize('7)', Colors.BOLD)} {colorize('Factory reset', Colors.RED)} (new Device ID)")
            print()
            
            print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if pending_devices:
                    self._accept_pending_device(instance, config, pending_devices)
                else:
                    self._add_syncthing_device(instance, config)
            elif choice == "2" and devices:
                self._remove_syncthing_device(instance, config, devices)
            elif choice == "3":
                self._show_syncthing_guide(instance, config)
            elif choice == "4":
                self._view_syncthing_logs(instance, config)
            elif choice == "5":
                self._restart_syncthing(instance, config)
            elif choice == "6":
                self._recreate_syncthing(instance, config)
            elif choice == "7":
                self._factory_reset_syncthing(instance, config)
            else:
                warn("Invalid option")
    
    def _accept_pending_device(self, instance: Instance, config, pending_devices: list) -> None:
        """Accept a pending device that's trying to connect."""
        from lib.installer.consume import add_device_to_syncthing
        
        print_header("Accept Pending Device")
        
        print("  These devices are trying to connect to this server:")
        print()
        for i, device in enumerate(pending_devices, 1):
            name = device.get('name', 'Unknown Device')
            short_id = device['deviceID'][:20] + "..." + device['deviceID'][-7:]
            print(f"  {colorize(str(i) + ')', Colors.BOLD)} {name}")
            print(f"      ID: {short_id}")
            print()
        print(f"  {colorize('0)', Colors.BOLD)} Cancel")
        print()
        
        choice = get_input("Select device to accept", "0")
        
        if choice == "0":
            return
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(pending_devices):
                device = pending_devices[idx]
                device_name = device.get('name', 'Unknown Device')
                
                # Ask for a better name
                custom_name = get_input(f"Name for this device", device_name)
                
                config_dir = instance.stack_dir / "syncthing-config"
                if add_device_to_syncthing(instance.name, config.syncthing, config_dir, device['deviceID'], custom_name):
                    print()
                    ok(f"Device '{custom_name}' added and trusted!")
                    say("The device should now connect and receive the shared folder.")
                else:
                    error("Failed to add device")
                input("\nPress Enter to continue...")
            else:
                warn("Invalid selection")
        except ValueError:
            warn("Invalid selection")

    def _remove_syncthing_device(self, instance: Instance, config, devices: list) -> None:
        """Remove a device from Syncthing."""
        from lib.installer.consume import remove_device_from_syncthing
        
        print_header("Remove Syncthing Device")
        
        print("  Select a device to remove:")
        print()
        for i, device in enumerate(devices, 1):
            status = colorize("● Connected", Colors.GREEN) if device["connected"] else colorize("○ Disconnected", Colors.YELLOW)
            print(f"  {colorize(str(i) + ')', Colors.BOLD)} {device['name']} ({status})")
        print()
        print(f"  {colorize('0)', Colors.BOLD)} Cancel")
        print()
        
        choice = get_input("Select device", "0")
        
        if choice == "0":
            return
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(devices):
                device = devices[idx]
                if confirm(f"Remove '{device['name']}'?", False):
                    config_dir = instance.stack_dir / "syncthing-config"
                    if remove_device_from_syncthing(instance.name, config.syncthing, config_dir, device["deviceID"]):
                        ok(f"Removed '{device['name']}'")
                    else:
                        error("Failed to remove device")
                    input("\nPress Enter to continue...")
            else:
                warn("Invalid selection")
        except ValueError:
            warn("Invalid selection")
    
    def _show_syncthing_guide(self, instance: Instance, config) -> None:
        """Show Syncthing setup guide."""
        from lib.installer.consume import generate_syncthing_guide, get_syncthing_device_id
        
        # Refresh device ID
        if not config.syncthing.device_id or config.syncthing.device_id == "Starting up...":
            device_id = get_syncthing_device_id(instance.name)
            if device_id:
                config.syncthing.device_id = device_id
                self._update_instance_env(instance, "CONSUME_SYNCTHING_DEVICE_ID", device_id)
        
        guide = generate_syncthing_guide(instance.name, config.syncthing, get_local_ip())
        print(guide)
        input("\nPress Enter to continue...")
    
    def _view_syncthing_logs(self, instance: Instance, config) -> None:
        """View full Syncthing logs."""
        from lib.installer.consume import get_syncthing_logs
        
        print_header("Syncthing Logs")
        
        logs = get_syncthing_logs(instance.name, 50)
        for line in logs.split("\n"):
            if line.strip():
                if "ERR" in line or "error" in line.lower():
                    print(colorize(line, Colors.RED))
                elif "WRN" in line or "warning" in line.lower():
                    print(colorize(line, Colors.YELLOW))
                else:
                    print(line)
        
        input("\nPress Enter to continue...")
    
    def _restart_syncthing(self, instance: Instance, config) -> None:
        """Simple restart of Syncthing container."""
        from lib.installer.consume import restart_syncthing_container, get_syncthing_status
        import time
        
        say("Restarting Syncthing...")
        restart_syncthing_container(instance.name)
        
        say("Waiting for container to start...")
        time.sleep(3)
        
        status = get_syncthing_status(instance.name)
        if status["running"]:
            ok("Syncthing is now running")
        else:
            error(f"Syncthing failed to start: {status['status']}")
        
        input("\nPress Enter to continue...")
    
    def _recreate_syncthing(self, instance: Instance, config) -> None:
        """Recreate Syncthing container (fixes Web UI access issues)."""
        from lib.installer.consume import (
            stop_syncthing_container, start_syncthing_container, 
            get_syncthing_status, get_syncthing_device_id
        )
        from lib.installer.tailscale import get_ip as get_tailscale_ip
        import time
        
        config_dir = instance.stack_dir / "syncthing-config"
        consume_path = instance.data_root / "consume"
        
        say("Stopping Syncthing...")
        stop_syncthing_container(instance.name)
        
        say("Starting Syncthing with fresh container...")
        start_syncthing_container(instance.name, config.syncthing, consume_path, config_dir)
        
        say("Waiting for initialization...")
        time.sleep(3)
        
        status = get_syncthing_status(instance.name)
        if status["running"]:
            ok("Syncthing recreated successfully")
            device_id = get_syncthing_device_id(instance.name)
            if device_id:
                say(f"Device ID: {device_id}")
            ts_ip = get_tailscale_ip()
            gui_port = config.syncthing.gui_port or 8384
            if ts_ip:
                ok(f"Web UI: http://{ts_ip}:{gui_port} (Tailscale only)")
            else:
                say(f"Web UI: localhost:{gui_port} (no Tailscale detected)")
        else:
            error(f"Syncthing failed to start: {status['status']}")
        
        input("\nPress Enter to continue...")
    
    def _recreate_all_syncthing_containers(self, reason: str) -> None:
        """Recreate all Syncthing containers to pick up new network bindings.
        
        Called automatically when Tailscale connects/disconnects to ensure
        Syncthing GUI binds to the correct interface.
        """
        from lib.installer.consume import (
            stop_syncthing_container, start_syncthing_container,
            load_consume_config, get_syncthing_status
        )
        from lib.installer.tailscale import get_ip as get_tailscale_ip
        import time
        
        # Find all instances with Syncthing enabled
        instances = self.instance_manager.list_instances()
        syncthing_instances = []
        
        for inst in instances:
            config = load_consume_config(inst.env_file)
            if config.syncthing.enabled:
                syncthing_instances.append((inst, config))
        
        if not syncthing_instances:
            return
        
        say(f"Recreating Syncthing containers ({reason})...")
        ts_ip = get_tailscale_ip()
        
        for inst, config in syncthing_instances:
            config_dir = inst.stack_dir / "syncthing-config"
            consume_path = inst.data_root / "consume"
            
            say(f"  Recreating syncthing-{inst.name}...")
            stop_syncthing_container(inst.name)
            start_syncthing_container(inst.name, config.syncthing, consume_path, config_dir)
        
        # Brief wait for containers to stabilize
        time.sleep(2)
        
        # Report status
        all_ok = True
        for inst, config in syncthing_instances:
            status = get_syncthing_status(inst.name)
            if status["running"]:
                gui_port = config.syncthing.gui_port or 8384
                if ts_ip:
                    ok(f"  {inst.name}: bound to Tailscale ({ts_ip}:{gui_port})")
                else:
                    ok(f"  {inst.name}: bound to localhost:{gui_port}")
            else:
                error(f"  {inst.name}: failed to start")
                all_ok = False
        
        if all_ok:
            ok(f"All Syncthing containers recreated")
    
    def _factory_reset_syncthing(self, instance: Instance, config) -> None:
        """Factory reset Syncthing - delete all config and start fresh."""
        from lib.installer.consume import (
            stop_syncthing_container, start_syncthing_container, 
            SyncthingConfig, generate_folder_id, save_consume_config
        )
        import shutil
        
        print()
        warn("This will delete ALL Syncthing configuration including:")
        print("  • All paired devices")
        print("  • Sync history")
        print("  • Your Device ID will change")
        print()
        print("You'll need to re-pair all client devices after reset.")
        print()
        
        if not confirm("Factory reset Syncthing?", False):
            return
        
        say("Stopping Syncthing...")
        stop_syncthing_container(instance.name)
        
        # Delete config directory
        config_dir = instance.stack_dir / "syncthing-config"
        if config_dir.exists():
            shutil.rmtree(config_dir)
            say("Config directory deleted")
        
        # Generate fresh config with unique ports
        consume_dir = instance.data_root / "consume"
        
        # Get a fresh unique sync port if needed (check if current one conflicts)
        from lib.installer.consume import get_next_available_port
        sync_port = config.syncthing.sync_port if config.syncthing.sync_port else 22000
        gui_port = config.syncthing.gui_port if config.syncthing.gui_port else 8384
        
        # Check if the GUI port is actually available (might be in use by another instance)
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', gui_port))
        except OSError:
            # Port is in use - get a new one
            say(f"Port {gui_port} is in use, finding available port...")
            gui_port = get_next_available_port(8384)
            say(f"Using port {gui_port} for Syncthing GUI")
        
        folder_id = generate_folder_id()
        
        syncthing_config = SyncthingConfig(
            enabled=True,
            sync_port=sync_port,
            gui_port=gui_port,
            folder_id=folder_id,
            folder_label=f"Paperless {instance.name}",
            device_id=""  # Will be populated after container starts
        )
        
        say("Starting fresh Syncthing...")
        if start_syncthing_container(
            instance_name=instance.name,
            config=syncthing_config,
            consume_path=consume_dir,
            config_dir=config_dir
        ):
            config.syncthing = syncthing_config
            save_consume_config(config, instance.env_file)
            ok("Syncthing factory reset complete")
            say(f"New Device ID: {config.syncthing.device_id}")
            say("You'll need to re-pair your devices with the new Device ID")
        else:
            error("Failed to restart Syncthing")
        
        input("\nPress Enter to continue...")
    
    def _manage_samba_menu(self, instance: Instance, config) -> None:
        """Samba management submenu with live status dashboard."""
        from lib.installer.consume import (
            is_samba_running, restart_samba, stop_samba, start_samba,
            generate_samba_guide, load_global_consume_config, save_global_consume_config,
            get_samba_container_name, get_instance_puid_pgid
        )
        from lib.installer.tailscale import get_ip as get_tailscale_ip, is_tailscale_installed
        
        while True:
            print_header(f"Manage Samba: {instance.name}")
            
            ts_ip = get_tailscale_ip()
            local_ip = get_local_ip()
            global_config = load_global_consume_config()
            container_name = get_samba_container_name(instance.name)
            
            # ── Live Dashboard ──
            box_line, box_width = create_box_helper(80)
            print(draw_box_top(box_width))
            print(box_line(colorize(" SAMBA STATUS DASHBOARD", Colors.BOLD)))
            print(draw_box_divider(box_width))
            
            # Container status
            running = is_samba_running(instance.name)
            if running:
                # Get uptime if possible
                try:
                    result = subprocess.run(
                        ["docker", "inspect", "--format", "{{.State.StartedAt}}", container_name],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        from datetime import datetime
                        started = result.stdout.strip()[:19]
                        started_dt = datetime.fromisoformat(started.replace('T', ' '))
                        uptime = datetime.now() - started_dt
                        uptime_str = f"{uptime.days}d {uptime.seconds // 3600}h" if uptime.days else f"{uptime.seconds // 3600}h {(uptime.seconds % 3600) // 60}m"
                        container_status = colorize(f"● Running (uptime: {uptime_str})", Colors.GREEN)
                    else:
                        container_status = colorize("● Running", Colors.GREEN)
                except:
                    container_status = colorize("● Running", Colors.GREEN)
            else:
                container_status = colorize("✗ Stopped", Colors.RED)
            print(box_line(f" Container:  {container_status}"))
            
            # Port
            port = config.samba.port or 445
            print(box_line(f" Port:       {port}"))
            
            # Network binding
            if global_config.samba_tailscale_only:
                bind_mode = colorize("Tailscale only", Colors.GREEN) + " (most secure)"
            else:
                bind_mode = colorize("All networks", Colors.YELLOW) + " (external + Tailscale)"
            print(box_line(f" Binding:    {bind_mode}"))
            
            print(draw_box_divider(box_width))
            
            # Connection info
            print(box_line(colorize(" CONNECTION INFO", Colors.BOLD)))
            share_name = config.samba.share_name or f"paperless-{instance.name}"
            port_suffix = "" if port == 445 else f":{port}"
            
            if global_config.samba_tailscale_only:
                if ts_ip:
                    print(box_line(f" Share:      \\\\{ts_ip}{port_suffix}\\{share_name}"))
                else:
                    print(box_line(f" Share:      {colorize('Tailscale not connected!', Colors.RED)}"))
            else:
                if ts_ip:
                    print(box_line(f" Share:      \\\\{ts_ip}{port_suffix}\\{share_name} (Tailscale)"))
                if local_ip:
                    label = "             " if ts_ip else " Share:      "
                    print(box_line(f"{label}\\\\{local_ip}{port_suffix}\\{share_name} (Local)"))
            
            print(box_line(f" Username:   {config.samba.username}"))
            print(box_line(f" Password:   {config.samba.password}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # Menu options
            print(colorize("  ── Actions ──", Colors.CYAN))
            print(f"  {colorize('1)', Colors.BOLD)} View setup guide")
            print(f"  {colorize('2)', Colors.BOLD)} Regenerate password")
            print(f"  {colorize('3)', Colors.BOLD)} Restart container")
            print(f"  {colorize('4)', Colors.BOLD)} View logs")
            print()
            print(colorize("  ── Network ──", Colors.CYAN))
            if global_config.samba_tailscale_only:
                print(f"  {colorize('5)', Colors.BOLD)} Allow external access (currently Tailscale-only)")
            else:
                print(f"  {colorize('5)', Colors.BOLD)} Restrict to Tailscale only (currently all networks)")
            print()
            print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._show_samba_guide(instance, config)
            elif choice == "2":
                self._regenerate_samba_password(instance, config)
            elif choice == "3":
                self._restart_samba(instance, config)
            elif choice == "4":
                self._view_samba_logs(instance, config)
            elif choice == "5":
                self._toggle_samba_network_mode(instance, config, global_config)
            else:
                warn("Invalid option")
    
    def _show_samba_guide(self, instance: Instance, config) -> None:
        """Show Samba setup guide."""
        from lib.installer.consume import generate_samba_guide, load_global_consume_config
        from lib.installer.tailscale import get_ip as get_tailscale_ip
        
        ts_ip = get_tailscale_ip()
        local_ip = get_local_ip()
        global_config = load_global_consume_config()
        
        # Show appropriate IP based on global tailscale_only setting
        if global_config.samba_tailscale_only:
            if ts_ip:
                display_ip = ts_ip
                is_tailscale = True
            else:
                print()
                warn("Samba is configured for Tailscale-only, but Tailscale is not connected!")
                print()
                display_ip = "tailscale-ip"
                is_tailscale = True
        else:
            display_ip = ts_ip or local_ip
            is_tailscale = bool(ts_ip)
        
        guide = generate_samba_guide(instance.name, config.samba, display_ip, is_tailscale=is_tailscale)
        print(guide)
        
        # Show both IPs if not tailscale_only and both available
        if not global_config.samba_tailscale_only and ts_ip:
            port_suffix = "" if config.samba.port == 445 else f":{config.samba.port}"
            print(f"  Also accessible via external IP: \\\\{local_ip}{port_suffix}\\{config.samba.share_name}")
            print()
        elif not global_config.samba_tailscale_only and not ts_ip:
            print(colorize("  💡 Install Tailscale for secure remote access!", Colors.CYAN))
            print()
        
        input("Press Enter to continue...")
    
    def _regenerate_samba_password(self, instance: Instance, config) -> None:
        """Regenerate Samba password."""
        from lib.installer.consume import (
            generate_secure_password, save_consume_config, restart_samba,
            get_instance_puid_pgid
        )
        
        print()
        warn("This will generate a new password.")
        say("You'll need to update any saved credentials on client devices.")
        print()
        
        if confirm("Regenerate Samba password?", False):
            new_password = generate_secure_password()
            config.samba.password = new_password
            save_consume_config(config, instance.env_file)
            self._update_instance_env(instance, "CONSUME_SAMBA_PASSWORD", new_password)
            
            # Restart container to apply new password
            consume_path = instance.data_root / "consume"
            puid, pgid = get_instance_puid_pgid(instance.name)
            restart_samba(instance.name, config.samba, consume_path, puid=puid, pgid=pgid)
            
            print()
            ok("Password regenerated!")
            say(f"New password: {new_password}")
        
        input("\nPress Enter to continue...")
    
    def _restart_samba(self, instance: Instance, config) -> None:
        """Restart Samba container."""
        from lib.installer.consume import restart_samba, get_instance_puid_pgid
        
        say("Restarting Samba container...")
        consume_path = instance.data_root / "consume"
        puid, pgid = get_instance_puid_pgid(instance.name)
        
        if restart_samba(instance.name, config.samba, consume_path, puid=puid, pgid=pgid):
            ok("Samba container restarted")
        else:
            error("Failed to restart Samba")
        
        input("\nPress Enter to continue...")
    
    def _view_samba_logs(self, instance: Instance, config) -> None:
        """View Samba container logs."""
        from lib.installer.consume import get_samba_container_name
        
        print_header("Samba Logs")
        
        container_name = get_samba_container_name(instance.name)
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", "50", container_name],
                capture_output=True, text=True
            )
            logs = result.stdout + result.stderr
            for line in logs.split("\n"):
                if line.strip():
                    if "error" in line.lower() or "failed" in line.lower():
                        print(colorize(line, Colors.RED))
                    elif "warning" in line.lower():
                        print(colorize(line, Colors.YELLOW))
                    else:
                        print(line)
        except Exception as e:
            error(f"Could not get logs: {e}")
        
        input("\nPress Enter to continue...")
    
    def _toggle_samba_network_mode(self, instance: Instance, config, global_config) -> None:
        """Toggle Samba network binding (Tailscale-only vs all networks)."""
        from lib.installer.consume import (
            save_global_consume_config, is_samba_running, start_samba,
            get_instance_puid_pgid, load_consume_config
        )
        from lib.installer.tailscale import get_ip as get_tailscale_ip, is_tailscale_installed
        
        ts_ip = get_tailscale_ip()
        new_value = not global_config.samba_tailscale_only
        
        if new_value and not ts_ip:
            print()
            error("Cannot enable Tailscale-only mode: Tailscale is not connected!")
            if not is_tailscale_installed():
                say("Install Tailscale first, then try again.")
            else:
                say("Connect to Tailscale first, then try again.")
            input("\nPress Enter to continue...")
            return
        
        global_config.samba_tailscale_only = new_value
        save_global_consume_config(global_config)
        
        # Restart all Samba containers to apply network binding changes
        say("Restarting Samba containers to apply changes...")
        for inst in self.instance_manager.list_instances():
            try:
                inst_config = load_consume_config(inst.env_file)
                if inst_config.samba.enabled and is_samba_running(inst.name):
                    consume_path = inst.data_root / "consume"
                    puid, pgid = get_instance_puid_pgid(inst.name)
                    start_samba(inst.name, inst_config.samba, consume_path, puid=puid, pgid=pgid)
            except Exception:
                pass
        
        if new_value:
            ok("Samba restricted to Tailscale network only")
        else:
            ok("Samba accessible from all networks")
        input("\nPress Enter to continue...")
    
    def _sftp_network_settings_menu(self) -> None:
        """SFTP network access settings (global - affects all instances)."""
        from lib.installer.consume import load_global_consume_config, save_global_consume_config, is_sftp_available
        from lib.installer.tailscale import get_ip as get_tailscale_ip, is_tailscale_installed
        
        while True:
            print_header("SFTP Network Settings (Global)")
            
            ts_ip = get_tailscale_ip()
            config = load_global_consume_config()
            
            # Current status dashboard
            box_line, box_width = create_box_helper(70)
            print(draw_box_top(box_width))
            print(box_line(colorize(" SFTP NETWORK SETTINGS", Colors.BOLD)))
            print(draw_box_divider(box_width))
            
            # Tailscale status
            if ts_ip:
                ts_status = colorize(f"● Connected ({ts_ip})", Colors.GREEN)
            elif is_tailscale_installed():
                ts_status = colorize("○ Installed but not connected", Colors.YELLOW)
            else:
                ts_status = colorize("✗ Not installed", Colors.RED)
            print(box_line(f" Tailscale:   {ts_status}"))
            
            # SFTP access mode
            if config.sftp_tailscale_only:
                sftp_mode = colorize("Tailscale only", Colors.GREEN) + " (most secure)"
            else:
                sftp_mode = colorize("All networks", Colors.YELLOW) + " (external + Tailscale)"
            print(box_line(f" SFTP:        {sftp_mode}"))
            
            print(draw_box_divider(box_width))
            print(box_line(" Note: SFTP is a shared container serving all instances."))
            print(box_line(" Changing this setting affects SFTP for ALL instances."))
            print(draw_box_bottom(box_width))
            print()
            
            # Menu
            print(colorize("  ── Access Mode ──", Colors.CYAN))
            sftp_action = "Allow external access" if config.sftp_tailscale_only else "Restrict to Tailscale only"
            print(f"  {colorize('1)', Colors.BOLD)} {sftp_action}")
            print()
            print(f"  {colorize('0)', Colors.BOLD)} {colorize('◀ Back', Colors.CYAN)}")
            print()
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                # Toggle SFTP tailscale_only
                new_value = not config.sftp_tailscale_only
                if new_value and not ts_ip:
                    print()
                    error("Cannot enable Tailscale-only mode: Tailscale is not connected!")
                    if not is_tailscale_installed():
                        say("Install Tailscale first, then try again.")
                    else:
                        say("Connect to Tailscale first, then try again.")
                    input("\nPress Enter to continue...")
                    continue
                
                config.sftp_tailscale_only = new_value
                save_global_consume_config(config)
                
                # Would need to restart SFTP container - but need instance configs
                # For now just show message
                if is_sftp_available():
                    warn("Restart SFTP container manually to apply changes")
                    say("(Disable and re-enable SFTP from any instance)")
                
                if new_value:
                    ok("SFTP restricted to Tailscale network only")
                else:
                    ok("SFTP accessible from all networks")
                input("\nPress Enter to continue...")
            else:
                warn("Invalid option")

    def _show_samba_credentials(self, instance: Instance, config) -> None:
        """Show Samba credentials and connection info."""
        from lib.installer.consume import generate_samba_guide, load_global_consume_config
        from lib.installer.tailscale import get_ip as get_tailscale_ip
        
        ts_ip = get_tailscale_ip()
        local_ip = get_local_ip()
        global_config = load_global_consume_config()
        
        # Show appropriate IP based on global tailscale_only setting
        if global_config.samba_tailscale_only:
            if ts_ip:
                display_ip = ts_ip
                is_tailscale = True
            else:
                print()
                warn("Samba is configured for Tailscale-only, but Tailscale is not connected!")
                print()
                display_ip = "tailscale-ip"
                is_tailscale = True
        else:
            display_ip = ts_ip or local_ip
            is_tailscale = bool(ts_ip)
        
        guide = generate_samba_guide(instance.name, config.samba, display_ip, is_tailscale=is_tailscale)
        print(guide)
        
        # Show both IPs if not tailscale_only and both available
        if not global_config.samba_tailscale_only and ts_ip:
            print(f"  Also accessible via external IP: \\\\{local_ip}\\{config.samba.share_name}")
            print()
        elif not global_config.samba_tailscale_only and not ts_ip:
            print(colorize("  💡 Install Tailscale for secure remote access!", Colors.CYAN))
            print()
        
        input("Press Enter to continue...")
    
    def _show_sftp_credentials(self, instance: Instance, config) -> None:
        """Show SFTP credentials and connection info."""
        from lib.installer.consume import generate_sftp_guide, load_global_consume_config
        from lib.installer.tailscale import get_ip as get_tailscale_ip
        
        ts_ip = get_tailscale_ip()
        local_ip = get_local_ip()
        global_config = load_global_consume_config()
        
        # Show appropriate IP based on global tailscale_only setting
        if global_config.sftp_tailscale_only:
            if ts_ip:
                display_ip = ts_ip
                is_tailscale = True
            else:
                print()
                warn("SFTP is configured for Tailscale-only, but Tailscale is not connected!")
                print()
                display_ip = "tailscale-ip"
                is_tailscale = True
        else:
            display_ip = ts_ip or local_ip
            is_tailscale = bool(ts_ip)
        
        guide = generate_sftp_guide(instance.name, config.sftp, display_ip, is_tailscale=is_tailscale)
        print(guide)
        
        # Show both IPs if not tailscale_only and both available
        if not global_config.sftp_tailscale_only and ts_ip:
            print(f"  Also accessible via external IP: sftp://{local_ip}:{config.sftp.port}")
            print()
        elif not global_config.sftp_tailscale_only and not ts_ip:
            print(colorize("  💡 Install Tailscale for secure remote access!", Colors.CYAN))
            print()
        
        input("Press Enter to continue...")

    def _add_syncthing_device(self, instance: Instance, config) -> None:
        """Add a new device to Syncthing."""
        from lib.installer.consume import add_device_to_syncthing
        
        print_header("Add Syncthing Device")
        
        print("  To add a user's device, you need their Syncthing Device ID.")
        print("  They can find it in their Syncthing: Actions → Show ID")
        print()
        print("  Device IDs look like: XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX")
        print()
        
        device_id = get_input("Paste the Device ID (or Enter to cancel)", "").strip().upper()
        
        if not device_id:
            say("Cancelled")
            return
        
        # Basic validation - should be 7 groups of 7 chars separated by dashes
        if not (len(device_id) >= 50 and "-" in device_id):
            error("Invalid Device ID format. Should look like: XXXXXXX-XXXXXXX-XXXXXXX-...")
            input("\nPress Enter to continue...")
            return
        
        device_name = get_input("Name for this device (e.g., 'John's Laptop')", "User Device")
        
        config_dir = instance.stack_dir / "syncthing-config"
        
        if add_device_to_syncthing(instance.name, config.syncthing, config_dir, device_id, device_name):
            print()
            ok(f"Device '{device_name}' added successfully!")
            print()
            say("The user should now:")
            print("  1. Add THIS server's Device ID to their Syncthing")
            print("  2. Accept the shared folder when prompted")
            print()
            say(f"Server Device ID: {config.syncthing.device_id}")
        else:
            error("Failed to add device")
        
        input("\nPress Enter to continue...")

    def _toggle_syncthing(self, instance: Instance, config) -> None:
        """Toggle Syncthing for an instance."""
        from lib.installer.consume import (
            start_syncthing_container, stop_syncthing_container, 
            SyncthingConfig, save_consume_config, generate_folder_id
        )
        
        if config.syncthing.enabled:
            # Disable
            print()
            warn("This will stop the Syncthing container.")
            say("Your configuration and paired devices will be kept for when you re-enable.")
            print()
            
            if confirm("Disable Syncthing?", False):
                try:
                    stop_syncthing_container(instance.name)
                    config.syncthing.enabled = False
                    save_consume_config(config, instance.env_file)
                    self._update_instance_env(instance, "CONSUME_SYNCTHING_ENABLED", "false")
                    ok("Syncthing disabled")
                    say("Config preserved - re-enable to resume with same devices")
                except Exception as e:
                    error(f"Failed to disable Syncthing: {e}")
        else:
            # Enable
            print()
            say("Syncthing provides secure, encrypted peer-to-peer file synchronization.")
            say("Perfect for syncing documents from your phone or computer.")
            print()
            
            if confirm("Enable Syncthing?", True):
                try:
                    consume_dir = instance.data_root / "consume"
                    syncthing_config_dir = instance.stack_dir / "syncthing-config"
                    
                    # Find available sync port and gui port
                    sync_port = find_available_port(22000)
                    gui_port = find_available_port(8384)
                    folder_id = generate_folder_id()
                    
                    syncthing_config = SyncthingConfig(
                        enabled=True,
                        sync_port=sync_port,
                        gui_port=gui_port,
                        folder_id=folder_id,
                        folder_label=f"Paperless {instance.name}",
                        device_id=""  # Will be populated after container starts
                    )
                    
                    start_syncthing_container(
                        instance_name=instance.name,
                        config=syncthing_config,
                        consume_path=consume_dir,
                        config_dir=syncthing_config_dir
                    )
                    
                    config.syncthing = syncthing_config
                    save_consume_config(config, instance.env_file)
                    self._update_instance_env(instance, "CONSUME_SYNCTHING_ENABLED", "true")
                    self._update_instance_env(instance, "CONSUME_SYNCTHING_SYNC_PORT", str(sync_port))
                    self._update_instance_env(instance, "CONSUME_SYNCTHING_GUI_PORT", str(gui_port))
                    
                    ok("Syncthing enabled!")
                    say("  Use 'View setup guides' to see pairing instructions")
                except Exception as e:
                    error(f"Failed to enable Syncthing: {e}")
        
        input("\nPress Enter to continue...")
    
    def _toggle_samba(self, instance: Instance, config) -> None:
        """Toggle Samba share for an instance (per-instance container)."""
        from lib.installer.consume import (
            start_samba, stop_samba, is_samba_running, create_samba_config,
            SambaConfig, save_consume_config, generate_secure_password
        )
        
        if config.samba.enabled:
            # Disable
            print()
            warn("This will stop the Samba container for this instance.")
            print()
            
            if confirm("Disable Samba share?", False):
                try:
                    stop_samba(instance.name)
                    config.samba.enabled = False
                    save_consume_config(config, instance.env_file)
                    self._update_instance_env(instance, "CONSUME_SAMBA_ENABLED", "false")
                    ok("Samba share stopped")
                except Exception as e:
                    error(f"Failed to disable Samba: {e}")
        else:
            # Enable
            print()
            say("Samba provides Windows/macOS compatible file sharing.")
            say("Each instance gets its own dedicated Samba container.")
            print()
            
            if confirm("Enable Samba share?", True):
                try:
                    from lib.installer.tailscale import get_ip as get_tailscale_ip
                    from lib.installer.consume import load_global_consume_config
                    
                    ts_ip = get_tailscale_ip()
                    local_ip = get_local_ip()
                    global_config = load_global_consume_config()
                    
                    # Check if Tailscale-only mode is enabled globally
                    if global_config.samba_tailscale_only and not ts_ip:
                        print()
                        error("Samba is configured for Tailscale-only, but Tailscale is not connected!")
                        say("Either connect Tailscale, or change the setting in 'Network access settings'")
                        input("\nPress Enter to continue...")
                        return
                    
                    consume_dir = instance.data_root / "consume"
                    
                    # Create new config with auto-assigned port
                    samba_config = create_samba_config(instance.name)
                    
                    # Get PUID/PGID from instance
                    puid = int(instance.get_env_value("PUID", "1000"))
                    pgid = int(instance.get_env_value("PGID", "1000"))
                    
                    # Start the per-instance Samba container
                    if start_samba(instance.name, samba_config, consume_dir, puid=puid, pgid=pgid):
                        config.samba = samba_config
                        save_consume_config(config, instance.env_file)
                        self._update_instance_env(instance, "CONSUME_SAMBA_ENABLED", "true")
                        self._update_instance_env(instance, "CONSUME_SAMBA_SHARE_NAME", samba_config.share_name)
                        self._update_instance_env(instance, "CONSUME_SAMBA_USERNAME", samba_config.username)
                        self._update_instance_env(instance, "CONSUME_SAMBA_PASSWORD", samba_config.password)
                        self._update_instance_env(instance, "CONSUME_SAMBA_PORT", str(samba_config.port))
                        
                        ok(f"Samba share enabled on port {samba_config.port}!")
                        
                        # Build connection string based on port
                        port_suffix = "" if samba_config.port == 445 else f":{samba_config.port}"
                        
                        if global_config.samba_tailscale_only and ts_ip:
                            say(f"  Share: \\\\{ts_ip}{port_suffix}\\{samba_config.share_name} (Tailscale only)")
                        elif ts_ip:
                            say(f"  Share: \\\\{ts_ip}{port_suffix}\\{samba_config.share_name} (Tailscale)")
                            if local_ip:
                                say(f"         \\\\{local_ip}{port_suffix}\\{samba_config.share_name} (Local)")
                        elif local_ip:
                            say(f"  Share: \\\\{local_ip}{port_suffix}\\{samba_config.share_name}")
                            say("  💡 Install Tailscale for secure remote access!")
                        
                        say(f"  Username: {samba_config.username}")
                        say(f"  Password: {samba_config.password}")
                        say("  Use 'View setup guides' for detailed instructions")
                    else:
                        error("Failed to start Samba container")
                except Exception as e:
                    error(f"Failed to enable Samba: {e}")
        
        input("\nPress Enter to continue...")
    
    def _toggle_sftp(self, instance: Instance, config) -> None:
        """Toggle SFTP access for an instance."""
        from lib.installer.consume import (
            start_sftp_container, stop_sftp_container,
            SFTPConfig, save_consume_config, generate_secure_password
        )
        
        if config.sftp.enabled:
            # Disable
            print()
            warn("This will remove SFTP access for this instance.")
            print()
            
            if confirm("Disable SFTP access?", False):
                try:
                    # Note: For single-instance case, we just disable config
                    # In multi-instance scenario, we'd rebuild the SFTP container
                    config.sftp.enabled = False
                    save_consume_config(config, instance.env_file)
                    self._update_instance_env(instance, "CONSUME_SFTP_ENABLED", "false")
                    ok("SFTP access removed")
                except Exception as e:
                    error(f"Failed to disable SFTP: {e}")
        else:
            # Enable
            print()
            say("SFTP provides secure file transfer over SSH.")
            say("Works with most file managers and SFTP clients.")
            print()
            
            if confirm("Enable SFTP access?", True):
                try:
                    from lib.installer.tailscale import get_ip as get_tailscale_ip
                    from lib.installer.consume import load_global_consume_config
                    
                    ts_ip = get_tailscale_ip()
                    local_ip = get_local_ip()
                    global_config = load_global_consume_config()
                    
                    # Check if Tailscale-only mode is enabled globally
                    if global_config.sftp_tailscale_only and not ts_ip:
                        print()
                        error("SFTP is configured for Tailscale-only, but Tailscale is not connected!")
                        say("Either connect Tailscale, or change the setting in 'Network access settings'")
                        input("\nPress Enter to continue...")
                        return
                    
                    consume_dir = instance.data_root / "consume"
                    username = f"pl-{instance.name}"
                    password = generate_secure_password()
                    
                    # Find available port
                    sftp_port = find_available_port(2222)
                    
                    sftp_config = SFTPConfig(
                        enabled=True,
                        username=username,
                        password=password,
                        port=sftp_port
                    )
                    
                    # For simplified single-instance case, start container directly
                    # Build instances_config and data_roots for this instance
                    from lib.installer.consume import ConsumeConfig
                    instances_config = {instance.name: config}
                    instances_config[instance.name].sftp = sftp_config
                    data_roots = {instance.name: instance.data_root}
                    
                    start_sftp_container(instances_config, data_roots, sftp_port)
                    
                    config.sftp = sftp_config
                    save_consume_config(config, instance.env_file)
                    self._update_instance_env(instance, "CONSUME_SFTP_ENABLED", "true")
                    self._update_instance_env(instance, "CONSUME_SFTP_PORT", str(sftp_port))
                    self._update_instance_env(instance, "CONSUME_SFTP_USERNAME", username)
                    self._update_instance_env(instance, "CONSUME_SFTP_PASSWORD", password)
                    
                    ok(f"SFTP access enabled!")
                    if global_config.sftp_tailscale_only:
                        say(f"  Server: sftp://{ts_ip}:{sftp_port} (Tailscale only)")
                    elif ts_ip:
                        say(f"  Server: sftp://{ts_ip}:{sftp_port} (Tailscale)")
                        say(f"          sftp://{local_ip}:{sftp_port} (External)")
                    else:
                        say(f"  Server: sftp://{local_ip}:{sftp_port}")
                        say("  💡 Install Tailscale for secure remote access!")
                    say(f"  Username: {username}")
                    say(f"  Password: {password}")
                    say("  Use 'View setup guides' for detailed instructions")
                except Exception as e:
                    error(f"Failed to enable SFTP: {e}")
        
        input("\nPress Enter to continue...")
    
    # NOTE: _get_local_ip and _find_available_port removed - use imported functions:
    # from lib.instance import get_local_ip, find_available_port
    
    def _update_instance_env(self, instance: Instance, key: str, value: str) -> bool:
        """Update a single value in the instance's .env file."""
        try:
            if not instance.env_file.exists():
                error(f"Env file not found: {instance.env_file}")
                return False
            
            content = instance.env_file.read_text()
            lines = content.splitlines()
            updated = False
            
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    updated = True
                    break
            
            if not updated:
                # Add new key
                lines.append(f"{key}={value}")
            
            instance.env_file.write_text("\n".join(lines) + "\n")
            return True
        except Exception as e:
            error(f"Failed to update env file: {e}")
            return False
    
    def _edit_instance_domain(self, instance: Instance) -> None:
        """Edit instance domain."""
        current = instance.get_env_value("DOMAIN", "localhost")
        say(f"Current domain: {current}")
        new_domain = get_domain_input("New domain (or Enter to keep current)", current)
        
        if new_domain and new_domain != current:
            if self._update_instance_env(instance, "DOMAIN", new_domain):
                # Also update PAPERLESS_URL if traefik or cloudflare enabled
                enable_traefik = instance.get_env_value("ENABLE_TRAEFIK", "no")
                enable_cloudflared = instance.get_env_value("ENABLE_CLOUDFLARED", "no")
                if enable_traefik == "yes" or enable_cloudflared == "yes":
                    self._update_instance_env(instance, "PAPERLESS_URL", f"https://{new_domain}")
                    self._update_instance_env(instance, "PAPERLESS_CSRF_TRUSTED_ORIGINS", f"https://{new_domain},http://localhost")
                
                ok(f"Domain changed to: {new_domain}")
                warn("Restart containers for changes to take effect")
        input("\nPress Enter to continue...")
    
    def _edit_instance_port(self, instance: Instance) -> None:
        """Edit instance HTTP port."""
        current = instance.get_env_value("HTTP_PORT", "8000")
        say(f"Current port: {current}")
        new_port = get_port_input("New HTTP port (or Enter to keep current)", current)
        
        if new_port and new_port != current:
            if self._update_instance_env(instance, "HTTP_PORT", new_port):
                ok(f"HTTP port changed to: {new_port}")
                warn("You must recreate containers for port changes:")
                say(f"  docker compose -f {instance.compose_file} down")
                say(f"  docker compose -f {instance.compose_file} up -d")
        input("\nPress Enter to continue...")
    
    def _toggle_instance_traefik(self, instance: Instance) -> None:
        """Toggle Traefik HTTPS for instance."""
        current = instance.get_env_value("ENABLE_TRAEFIK", "no")
        
        if current == "yes":
            # Disable Traefik
            if confirm("Disable Traefik HTTPS for this instance?", False):
                self._update_instance_env(instance, "ENABLE_TRAEFIK", "no")
                port = instance.get_env_value("HTTP_PORT", "8000")
                self._update_instance_env(instance, "PAPERLESS_URL", f"http://localhost:{port}")
                self._update_instance_env(instance, "PAPERLESS_CSRF_TRUSTED_ORIGINS", "http://localhost")
                ok("Traefik disabled - instance will use direct HTTP")
                # Regenerate compose file to remove Traefik labels
                self._offer_regenerate_compose(instance)
        else:
            # Enable Traefik
            from lib.installer.traefik import is_traefik_running
            if not is_traefik_running():
                error("System Traefik is not running!")
                say("Install Traefik from main menu first")
            elif confirm("Enable Traefik HTTPS for this instance?", True):
                domain = instance.get_env_value("DOMAIN", "localhost")
                if domain == "localhost":
                    domain = get_domain_input("Enter domain for HTTPS", "paperless.example.com")
                    self._update_instance_env(instance, "DOMAIN", domain)
                
                self._update_instance_env(instance, "ENABLE_TRAEFIK", "yes")
                self._update_instance_env(instance, "ENABLE_CLOUDFLARED", "no")  # Mutually exclusive
                self._update_instance_env(instance, "PAPERLESS_URL", f"https://{domain}")
                self._update_instance_env(instance, "PAPERLESS_CSRF_TRUSTED_ORIGINS", f"https://{domain},http://localhost")
                
                # Stop and remove any existing Cloudflare tunnel service
                service_name = f"cloudflared-{instance.name}"
                try:
                    result = subprocess.run(["systemctl", "is-active", service_name], check=False, capture_output=True)
                    if result.returncode == 0:  # Service is running
                        say(f"Stopping Cloudflare tunnel service...")
                        subprocess.run(["systemctl", "stop", service_name], check=False, capture_output=True)
                        subprocess.run(["systemctl", "disable", service_name], check=False, capture_output=True)
                        service_file = Path(f"/etc/systemd/system/{service_name}.service")
                        if service_file.exists():
                            service_file.unlink()
                        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
                        ok("Cloudflare tunnel service removed")
                except:
                    pass
                
                ok(f"Traefik enabled for https://{domain}")
                warn("You must regenerate docker-compose.yml and recreate containers")
                self._offer_regenerate_compose(instance)
        
        input("\nPress Enter to continue...")
    
    def _toggle_instance_cloudflare(self, instance: Instance) -> None:
        """Toggle Cloudflare Tunnel for instance."""
        current = instance.get_env_value("ENABLE_CLOUDFLARED", "no")
        
        if current == "yes":
            # Disable Cloudflare
            if confirm("Disable Cloudflare Tunnel for this instance?", False):
                self._update_instance_env(instance, "ENABLE_CLOUDFLARED", "no")
                port = instance.get_env_value("HTTP_PORT", "8000")
                self._update_instance_env(instance, "PAPERLESS_URL", f"http://localhost:{port}")
                self._update_instance_env(instance, "PAPERLESS_CSRF_TRUSTED_ORIGINS", "http://localhost")
                
                # Stop and remove tunnel service
                service_name = f"cloudflared-{instance.name}"
                try:
                    subprocess.run(["systemctl", "stop", service_name], check=False, capture_output=True)
                    subprocess.run(["systemctl", "disable", service_name], check=False, capture_output=True)
                    service_file = Path(f"/etc/systemd/system/{service_name}.service")
                    if service_file.exists():
                        service_file.unlink()
                    subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
                except:
                    pass
                
                ok("Cloudflare Tunnel disabled")
        else:
            # Enable Cloudflare
            from lib.installer.cloudflared import is_cloudflared_installed, is_authenticated, create_tunnel
            if not is_cloudflared_installed():
                error("Cloudflared is not installed!")
                say("Install from main menu: Manage Cloudflare Tunnel")
            elif not is_authenticated():
                error("Cloudflared is not authenticated!")
                say("Authenticate from main menu: Manage Cloudflare Tunnel")
            elif confirm("Enable Cloudflare Tunnel for this instance?", True):
                domain = instance.get_env_value("DOMAIN", "localhost")
                if domain == "localhost":
                    domain = get_domain_input("Enter domain for Cloudflare Tunnel", "paperless.example.com")
                    self._update_instance_env(instance, "DOMAIN", domain)
                
                self._update_instance_env(instance, "ENABLE_CLOUDFLARED", "yes")
                self._update_instance_env(instance, "ENABLE_TRAEFIK", "no")  # Mutually exclusive
                self._update_instance_env(instance, "PAPERLESS_URL", f"https://{domain}")
                self._update_instance_env(instance, "PAPERLESS_CSRF_TRUSTED_ORIGINS", f"https://{domain},http://localhost")
                
                # Get the instance port
                port = int(instance.get_env_value("HTTP_PORT", "8000"))
                
                # Create tunnel
                say("Creating Cloudflare tunnel...")
                if create_tunnel(instance.name, domain, port):
                    # Create and start systemd service
                    self._create_cloudflare_service(instance.name)
                    ok(f"Cloudflare Tunnel enabled for https://{domain}")
                    # Regenerate compose file to remove Traefik labels (if switching from Traefik)
                    self._offer_regenerate_compose(instance)
                else:
                    warn("Tunnel creation failed - you may need to set it up manually")
        
        input("\nPress Enter to continue...")
    
    def _toggle_instance_tailscale(self, instance: Instance) -> None:
        """Toggle Tailscale access for instance."""
        current = instance.get_env_value("ENABLE_TAILSCALE", "no")
        
        if current == "yes":
            if confirm("Disable Tailscale access for this instance?", False):
                self._update_instance_env(instance, "ENABLE_TAILSCALE", "no")
                
                # Remove Tailscale Serve if configured
                from lib.installer.tailscale import remove_serve, is_serve_available
                if is_serve_available():
                    path = f"/{instance.name}"
                    remove_serve(path)
                
                ok("Tailscale access disabled")
        else:
            from lib.installer.tailscale import (
                is_tailscale_installed, is_connected, get_ip, get_hostname,
                is_serve_available, add_serve, get_serve_url
            )
            if not is_tailscale_installed():
                error("Tailscale is not installed!")
                say("Install from main menu: Manage Tailscale")
            elif not is_connected():
                error("Tailscale is not connected!")
                say("Connect from main menu: Manage Tailscale")
            elif confirm("Enable Tailscale access for this instance?", True):
                self._update_instance_env(instance, "ENABLE_TAILSCALE", "yes")
                ip = get_ip()
                port = instance.get_env_value("HTTP_PORT", "8000")
                
                # Check if instance already has HTTPS via Traefik or Cloudflare
                has_https = (
                    instance.get_env_value("ENABLE_TRAEFIK", "no").lower() == "yes" or
                    instance.get_env_value("ENABLE_CLOUDFLARED", "no").lower() == "yes"
                )
                
                # Only offer Tailscale Serve HTTPS if no other HTTPS is configured
                # (having multiple HTTPS URLs can confuse Paperless's URL configuration)
                if not has_https and is_serve_available():
                    hostname = get_hostname()
                    if hostname:
                        print()
                        say(f"Tailscale Serve can provide HTTPS: https://{hostname}/{instance.name}")
                        if confirm("Configure Tailscale Serve for HTTPS access?", True):
                            path = f"/{instance.name}"
                            if add_serve(path, int(port)):
                                serve_url = get_serve_url(path)
                                self._update_instance_env(instance, "TAILSCALE_SERVE_PATH", path)
                                ok(f"Tailscale Serve configured: {serve_url}")
                            else:
                                warn("Tailscale Serve setup failed - using direct IP access")
                                ok(f"Tailscale enabled - access via http://{ip}:{port}")
                        else:
                            ok(f"Tailscale enabled - access via http://{ip}:{port}")
                    else:
                        ok(f"Tailscale enabled - access via http://{ip}:{port}")
                else:
                    ok(f"Tailscale enabled - access via http://{ip}:{port}")
                
                say("Note: Tailscale works alongside other access methods")
        
        input("\nPress Enter to continue...")
    
    def _edit_instance_admin_password(self, instance: Instance) -> None:
        """Change the Paperless admin password."""
        say("This will update the admin password in the configuration.")
        warn("The password is stored in the .env file.")
        
        new_password = get_input("New admin password (or Enter to cancel)", "")
        if new_password:
            if len(new_password) < 8:
                error("Password must be at least 8 characters")
            else:
                if self._update_instance_env(instance, "PAPERLESS_ADMIN_PASSWORD", new_password):
                    ok("Admin password updated in configuration")
                    say("To apply, you need to recreate the admin user:")
                    say(f"  docker compose -f {instance.compose_file} exec paperless python manage.py changepassword admin")
        
        input("\nPress Enter to continue...")
    
    def _cron_to_human(self, cron: str) -> str:
        """Convert a cron expression to human-readable text."""
        if not cron:
            return "Not configured"
        
        parts = cron.split()
        if len(parts) != 5:
            return cron  # Return as-is if invalid
        
        minute, hour, day, month, dow = parts
        
        # Common patterns for our backup schedules
        # Incremental: every N hours
        if hour.startswith('*/'):
            interval = hour[2:]
            return f"Every {interval} hours"
        
        # Monthly: 1st of month
        if day == '1' and dow == '*':
            h = int(hour) if hour.isdigit() else 0
            m = int(minute) if minute.isdigit() else 0
            return f"1st of month @ {h:02d}:{m:02d}"
        
        # Weekly: specific day of week
        if dow != '*' and day == '*':
            days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
            day_name = days[int(dow)] if dow.isdigit() and int(dow) < 7 else dow
            h = int(hour) if hour.isdigit() else 0
            m = int(minute) if minute.isdigit() else 0
            return f"{day_name} @ {h:02d}:{m:02d}"
        
        # Daily: specific time each day
        if day == '*' and dow == '*' and hour.isdigit():
            h = int(hour)
            m = int(minute) if minute.isdigit() else 0
            return f"Daily @ {h:02d}:{m:02d}"
        
        # Fallback: return raw cron
        return cron
    
    def _edit_instance_backup_schedule(self, instance: Instance) -> None:
        """Change the backup schedule and retention policy for an instance."""
        print_header(f"Backup Schedule: {instance.name}")
        
        # Get current settings - check if explicitly set or using defaults
        cron_incr = instance.get_env_value('CRON_INCR_TIME', '')
        cron_full = instance.get_env_value('CRON_FULL_TIME', '')
        cron_archive = instance.get_env_value('CRON_ARCHIVE_TIME', '')
        retention = instance.get_env_value('RETENTION_DAYS', '')
        retention_monthly = instance.get_env_value('RETENTION_MONTHLY_DAYS', '')
        
        # Format cron for table display
        def fmt_cron_parts(val: str) -> tuple[str, str]:
            """Return (human readable, cron code) tuple."""
            if not val:
                return (colorize("Not configured", Colors.YELLOW), "-")
            human = self._cron_to_human(val)
            return (human, val)
        
        def fmt_retention(val: str, default: str) -> str:
            if not val:
                return f"{default} {colorize('(default)', Colors.YELLOW)}"
            return val
        
        # Get formatted parts
        incr_human, incr_cron = fmt_cron_parts(cron_incr)
        full_human, full_cron = fmt_cron_parts(cron_full)
        arch_human, arch_cron = fmt_cron_parts(cron_archive)
        
        # Show current settings as a table
        box_line, box_width = create_box_helper(80)
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Current Backup Schedule:', Colors.BOLD)}"))
        print(box_line(f""))
        # Table header
        print(box_line(f"   {'Type':<14} {'Schedule':<22} {'Cron'}"))
        print(box_line(f"   {'-'*14} {'-'*22} {'-'*14}"))
        # Table rows
        print(box_line(f"   {'Incremental':<14} {incr_human:<22} {colorize(incr_cron, Colors.DIM)}"))
        print(box_line(f"   {'Full':<14} {full_human:<22} {colorize(full_cron, Colors.DIM)}"))
        print(box_line(f"   {'Archive':<14} {arch_human:<22} {colorize(arch_cron, Colors.DIM)}"))
        print(box_line(f""))
        print(box_line(f" {colorize('Current Retention Policy:', Colors.BOLD)}"))
        print(box_line(f"   All backups:  {fmt_retention(retention, '30')} days"))
        
        # Only show monthly retention if archives are configured
        if cron_archive:
            print(box_line(f"   Monthly arch: {fmt_retention(retention_monthly, '180')} days"))
        else:
            print(box_line(f"   Monthly arch: {colorize('N/A (no archive schedule)', Colors.YELLOW)}"))
        print(draw_box_bottom(box_width))
        
        # Show warning if schedule not fully configured
        if not cron_incr and not cron_full and not cron_archive:
            print()
            warn("No backup schedule configured! Consider enabling backups.")
        elif not cron_archive:
            print()
            say("Tip: Enable archive backups for long-term monthly retention.")
        print()
        
        options = [
            ("1", "Change backup frequency preset"),
            ("2", "Change retention policy preset"),
            ("3", "Custom schedule (advanced)"),
            ("4", "Custom retention (advanced)"),
            ("5", "Disable all backups"),
            ("6", "Run retention cleanup now"),
            ("0", colorize("◀ Back", Colors.CYAN))
        ]
        print_menu(options)
        
        choice = get_input("Select option", "")
        
        if choice == "0":
            return
        elif choice == "1":
            self._edit_backup_frequency_preset(instance)
        elif choice == "2":
            self._edit_retention_preset(instance)
        elif choice == "3":
            self._edit_backup_schedule_custom(instance)
        elif choice == "4":
            self._edit_retention_custom(instance)
        elif choice == "5":
            self._disable_backups(instance)
        elif choice == "6":
            self._run_retention_cleanup(instance)
        else:
            warn("Invalid option")
            input("\nPress Enter to continue...")
    
    def _edit_backup_frequency_preset(self, instance: Instance) -> None:
        """Change backup frequency using preset options."""
        print()
        print(colorize("Select Backup Frequency:", Colors.BOLD))
        print()
        print(f"  {colorize('1)', Colors.BOLD)} Recommended: 6-hour incremental, weekly full, monthly archive")
        print(f"  {colorize('2)', Colors.BOLD)} Conservative: Daily incremental, weekly full, monthly archive")
        print(f"  {colorize('3)', Colors.BOLD)} Minimal: Weekly full, monthly archive only (no incremental)")
        print(f"  {colorize('4)', Colors.BOLD)} High-frequency: 2-hour incremental, daily full, weekly archive")
        print()
        
        choice = get_input("Select preset [1-4]", "1")
        
        if choice == "1":
            cron_incr = "0 */6 * * *"
            cron_full = "30 3 * * 0"
            cron_archive = "0 4 1 * *"
        elif choice == "2":
            cron_incr = "0 0 * * *"
            cron_full = "30 3 * * 0"
            cron_archive = "0 4 1 * *"
        elif choice == "3":
            cron_incr = ""
            cron_full = "30 3 * * 0"
            cron_archive = "0 4 1 * *"
        elif choice == "4":
            cron_incr = "0 */2 * * *"
            cron_full = "30 3 * * *"
            cron_archive = "0 4 * * 0"
        else:
            warn("Invalid choice")
            input("\nPress Enter to continue...")
            return
        
        # Update instance env file
        self._update_instance_env(instance, "CRON_INCR_TIME", cron_incr)
        self._update_instance_env(instance, "CRON_FULL_TIME", cron_full)
        self._update_instance_env(instance, "CRON_ARCHIVE_TIME", cron_archive)
        
        # Reinstall cron
        self._reinstall_backup_cron(instance)
        ok("Backup frequency updated")
        input("\nPress Enter to continue...")
    
    def _edit_retention_preset(self, instance: Instance) -> None:
        """Change retention policy using preset options."""
        print()
        print(colorize("Select Retention Policy:", Colors.BOLD))
        print()
        print(f"  {colorize('1)', Colors.BOLD)} Standard: Keep all for 30 days, monthly archives for 6 months")
        print(f"  {colorize('2)', Colors.BOLD)} Extended: Keep all for 60 days, monthly archives for 12 months")
        print(f"  {colorize('3)', Colors.BOLD)} Compact: Keep all for 14 days, monthly archives for 3 months")
        print(f"  {colorize('4)', Colors.BOLD)} Aggressive: Keep all for 7 days, monthly archives for 1 month")
        print()
        
        choice = get_input("Select preset [1-4]", "1")
        
        if choice == "1":
            retention = "30"
            retention_monthly = "180"
        elif choice == "2":
            retention = "60"
            retention_monthly = "365"
        elif choice == "3":
            retention = "14"
            retention_monthly = "90"
        elif choice == "4":
            retention = "7"
            retention_monthly = "30"
        else:
            warn("Invalid choice")
            input("\nPress Enter to continue...")
            return
        
        self._update_instance_env(instance, "RETENTION_DAYS", retention)
        self._update_instance_env(instance, "RETENTION_MONTHLY_DAYS", retention_monthly)
        
        ok("Retention policy updated")
        input("\nPress Enter to continue...")
    
    def _edit_backup_schedule_custom(self, instance: Instance) -> None:
        """Configure custom cron schedules for backups."""
        print()
        say("Enter cron expressions (or leave blank to disable)")
        say("Format: minute hour day-of-month month day-of-week")
        say("Examples: '0 */6 * * *' = every 6 hours, '30 3 * * 0' = Sunday 3:30 AM")
        print()
        
        current_incr = instance.get_env_value('CRON_INCR_TIME', '0 */6 * * *')
        current_full = instance.get_env_value('CRON_FULL_TIME', '30 3 * * 0')
        current_archive = instance.get_env_value('CRON_ARCHIVE_TIME', '0 4 1 * *')
        
        cron_incr = get_input(f"Incremental schedule [{current_incr}]", current_incr)
        cron_full = get_input(f"Full backup schedule [{current_full}]", current_full)
        cron_archive = get_input(f"Archive schedule [{current_archive}]", current_archive)
        
        self._update_instance_env(instance, "CRON_INCR_TIME", cron_incr)
        self._update_instance_env(instance, "CRON_FULL_TIME", cron_full)
        self._update_instance_env(instance, "CRON_ARCHIVE_TIME", cron_archive)
        
        self._reinstall_backup_cron(instance)
        ok("Custom backup schedule configured")
        input("\nPress Enter to continue...")
    
    def _edit_retention_custom(self, instance: Instance) -> None:
        """Configure custom retention periods."""
        print()
        say("Retention policy determines how long backups are kept:")
        say("  • All backups (incr/full/archive) kept for RETENTION_DAYS")
        say("  • After that, only monthly archives kept for RETENTION_MONTHLY_DAYS")
        print()
        
        current_retention = instance.get_env_value('RETENTION_DAYS', '30')
        current_monthly = instance.get_env_value('RETENTION_MONTHLY_DAYS', '180')
        
        retention = get_input(f"Keep ALL backups for how many days? [{current_retention}]", current_retention)
        retention_monthly = get_input(f"Keep monthly archives for how many days? [{current_monthly}]", current_monthly)
        
        # Validate inputs
        try:
            int(retention)
            int(retention_monthly)
        except ValueError:
            error("Invalid number entered")
            input("\nPress Enter to continue...")
            return
        
        self._update_instance_env(instance, "RETENTION_DAYS", retention)
        self._update_instance_env(instance, "RETENTION_MONTHLY_DAYS", retention_monthly)
        
        ok("Custom retention policy configured")
        input("\nPress Enter to continue...")
    
    def _disable_backups(self, instance: Instance) -> None:
        """Disable all backup schedules for an instance."""
        print()
        warn("This will disable all automatic backups for this instance.")
        say("You can still run manual backups from the instance menu.")
        print()
        
        if not confirm("Disable automatic backups?", False):
            return
        
        self._update_instance_env(instance, "CRON_INCR_TIME", "")
        self._update_instance_env(instance, "CRON_FULL_TIME", "")
        self._update_instance_env(instance, "CRON_ARCHIVE_TIME", "")
        
        self._reinstall_backup_cron(instance)
        ok("Automatic backups disabled")
        input("\nPress Enter to continue...")
    
    def _run_retention_cleanup(self, instance: Instance) -> None:
        """Run retention cleanup immediately for an instance."""
        print()
        say(f"Running retention cleanup for {instance.name}...")
        
        try:
            backup_script = instance.stack_dir / "backup.py"
            if backup_script.exists():
                result = subprocess.run(
                    ["python3", str(backup_script), "cleanup"],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={**os.environ, "ENV_FILE": str(instance.env_file)}
                )
                if result.returncode == 0:
                    ok("Retention cleanup completed")
                    if result.stdout:
                        print(result.stdout)
                else:
                    error("Cleanup failed")
                    if result.stderr:
                        print(result.stderr)
            else:
                error(f"Backup script not found at {backup_script}")
        except Exception as e:
            error(f"Failed to run cleanup: {e}")
        
        input("\nPress Enter to continue...")
    
    def _reinstall_backup_cron(self, instance: Instance) -> None:
        """Reinstall backup cron jobs for an instance."""
        try:
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import files
            
            # Load ALL settings from instance (ensures cron times are loaded)
            load_instance_config(instance)
            
            files.install_cron_backup()
        except Exception as e:
            warn(f"Failed to reinstall cron: {e}")
    
    def _configure_custom_backup_schedule(self) -> None:
        """Helper for configuring custom backup schedule during instance creation."""
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import common
        
        print()
        say("Enter cron expressions (or leave blank to disable)")
        say("Format: minute hour day-of-month month day-of-week")
        say("Examples: '0 */6 * * *' = every 6 hours, '30 3 * * 0' = Sunday 3:30 AM")
        print()
        
        common.cfg.cron_incr_time = get_input("Incremental schedule", "0 */6 * * *")
        common.cfg.cron_full_time = get_input("Full backup schedule", "30 3 * * 0")
        common.cfg.cron_archive_time = get_input("Archive schedule", "0 4 1 * *")

    def _create_cloudflare_service(self, instance_name: str) -> bool:
        """Create and start a systemd service for Cloudflare tunnel."""
        try:
            service_content = f"""[Unit]
Description=Cloudflare Tunnel for {instance_name}
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared tunnel --config /etc/cloudflared/{instance_name}.yml run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""
            service_file = Path(f"/etc/systemd/system/cloudflared-{instance_name}.service")
            service_file.write_text(service_content)
            
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", f"cloudflared-{instance_name}"], check=True)
            subprocess.run(["systemctl", "start", f"cloudflared-{instance_name}"], check=True)
            
            ok("Cloudflare tunnel service started")
            return True
        except Exception as e:
            warn(f"Failed to create service: {e}")
            return False
    
    def _offer_regenerate_compose(self, instance: Instance, skip_confirm: bool = False) -> None:
        """Offer to regenerate docker-compose.yml for the instance.
        
        Args:
            instance: The instance to regenerate compose for
            skip_confirm: If True, skip the initial confirmation prompt
        """
        if not skip_confirm and not confirm("Regenerate docker-compose.yml now?", True):
            return
        
        try:
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import files
            
            # Load ALL settings from instance's .env into common.cfg
            load_instance_config(instance)
            
            # Regenerate env file first to ensure all variables are present (including CSRF settings)
            say("Updating configuration...")
            files.write_env_file()
            
            # Write new compose file
            files.write_compose_file()
            ok("docker-compose.yml regenerated")
            
            if confirm("Recreate containers now?", True):
                self._docker_command(instance, "down")
                self._docker_command(instance, "up", "-d")
                ok("Containers recreated")
        except Exception as e:
            error(f"Failed to regenerate: {e}")

    def system_backup_menu(self) -> None:
        """System-level backup and restore menu."""
        while True:
            print_header("System Backup & Restore")
            
            if not self.rclone_configured:
                warn("Backup server not configured!")
                input("\nPress Enter to continue...")
                return
            
            instances = self.instance_manager.list_instances()
            
            # Check for existing system backups
            try:
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless-system"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                system_backups = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
            except:
                system_backups = []
            
            # System overview box
            box_line, box_width = create_box_helper(80)
            
            print(draw_box_top(box_width))
            print(box_line(f" Current System: {len(instances)} instance(s) configured"))
            print(box_line(f" System Backups: {len(system_backups)} available"))
            print(draw_box_bottom(box_width))
            print()
            
            print(colorize("What is System Backup?", Colors.BOLD))
            print("  • Backs up metadata about ALL instances")
            print("  • Records which instances exist, their config, state")
            print("  • System-level Samba/SFTP configs (Syncthing is per-instance)")
            print("  • Enables disaster recovery: restore entire multi-instance setup")
            print("  • Separate from individual instance data backups")
            print()
            
            options = [
                ("1", colorize("💾", Colors.GREEN) + " Backup current system"),
                ("2", colorize("📋", Colors.BLUE) + " View system backups"),
                ("3", colorize("🔄", Colors.YELLOW) + " Restore system from backup"),
                ("0", colorize("◀ Back", Colors.CYAN))
            ]
            print_menu(options)
            
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                self._backup_system()
            elif choice == "2":
                self._view_system_backups()
            elif choice == "3":
                self._restore_system()
            else:
                warn("Invalid option")
    
    def _backup_system(self) -> None:
        """Backup current system configuration."""
        print_header("Backup Current System")
        
        instances = self.instance_manager.list_instances()
        
        if not instances:
            warn("No instances to backup!")
            input("\nPress Enter to continue...")
            return
        
        print(f"This will backup metadata for {len(instances)} instance(s):")
        for inst in instances:
            status = "running" if inst.is_running else "stopped"
            print(f"  • {inst.name} ({status})")
        print()
        
        # Check what network configs exist
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import traefik, cloudflared, tailscale
        
        traefik_running = traefik.is_traefik_running()
        all_cloudflare_tunnels = cloudflared.list_tunnels() if cloudflared.is_authenticated() else []
        paperless_tunnels = [t for t in all_cloudflare_tunnels if t.get('name', '').startswith('paperless-')]
        tailscale_connected = tailscale.is_connected()
        rclone_conf = Path.home() / ".config" / "rclone" / "rclone.conf"
        
        print("Network configuration to backup:")
        print(f"  • Traefik: {'✓ Running' if traefik_running else '○ Not active'}")
        print(f"  • Cloudflare Tunnels: {len(paperless_tunnels)} paperless tunnel(s)")
        print(f"  • Tailscale: {'✓ Connected' if tailscale_connected else '○ Not active'}")
        print(f"  • rclone config: {'✓ Found' if rclone_conf.exists() else '○ Not found'}")
        print()
        
        if not confirm("Create system backup?", True):
            return
        
        try:
            from datetime import datetime
            import json
            import tempfile
            import shutil
            import base64
            
            # Optional: Name the backup
            print()
            default_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            backup_name = get_input("Backup name (or Enter for timestamp)", default_name)
            # Sanitize name - only allow alphanumeric, hyphens, underscores
            backup_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in backup_name)
            
            # Force full backup of all running instances first
            running_instances = [inst for inst in instances if inst.is_running]
            if running_instances:
                print()
                say(f"Creating full backup of {len(running_instances)} running instance(s)...")
                for inst in running_instances:
                    say(f"  Backing up {inst.name}...")
                    try:
                        backup_mgr = BackupManager(inst)
                        backup_mgr.run_backup(mode='full')
                        ok(f"  {inst.name} backed up")
                    except Exception as e:
                        warn(f"  {inst.name} backup failed: {e}")
                print()
            
            # Create temp directory for system backup
            work = Path(tempfile.mkdtemp(prefix="paperless-system-"))
            
            say(f"Creating system backup: {backup_name}")
            
            # ─── Backup Network Configurations ────────────────────────────────
            network_dir = work / "network"
            network_dir.mkdir(parents=True, exist_ok=True)
            
            network_info = {
                "traefik": {"enabled": False},
                "cloudflare": {"enabled": False, "tunnels": []},
                "tailscale": {"enabled": False},
                "rclone": {"enabled": False}
            }
            
            # Backup Traefik config
            traefik_dir = Path("/opt/traefik")
            if traefik_dir.exists():
                say("Backing up Traefik configuration...")
                traefik_backup_dir = network_dir / "traefik"
                traefik_backup_dir.mkdir(exist_ok=True)
                
                # traefik.yml (config)
                traefik_yml = traefik_dir / "traefik.yml"
                if traefik_yml.exists():
                    shutil.copy2(traefik_yml, traefik_backup_dir / "traefik.yml")
                
                # acme.json (SSL certificates) - this is sensitive!
                acme_json = traefik_dir / "acme.json"
                if acme_json.exists():
                    shutil.copy2(acme_json, traefik_backup_dir / "acme.json")
                
                network_info["traefik"] = {
                    "enabled": True,
                    "running": traefik_running,
                    "email": traefik.get_traefik_email()
                }
                ok("Traefik config backed up (including SSL certificates)")
            
            # Backup Cloudflare Tunnel configs
            cloudflared_etc = Path("/etc/cloudflared")
            cloudflared_home = Path.home() / ".cloudflared"
            
            if cloudflared_etc.exists() or cloudflared_home.exists():
                say("Backing up Cloudflare Tunnel configuration...")
                cf_backup_dir = network_dir / "cloudflared"
                cf_backup_dir.mkdir(exist_ok=True)
                
                tunnel_configs = []
                
                # Backup /etc/cloudflared/*.yml (tunnel configs)
                if cloudflared_etc.exists():
                    etc_backup = cf_backup_dir / "etc"
                    etc_backup.mkdir(exist_ok=True)
                    for yml_file in cloudflared_etc.glob("*.yml"):
                        shutil.copy2(yml_file, etc_backup / yml_file.name)
                        tunnel_configs.append(yml_file.name.replace(".yml", ""))
                
                # Backup ~/.cloudflared/ (credentials and cert)
                if cloudflared_home.exists():
                    home_backup = cf_backup_dir / "home"
                    home_backup.mkdir(exist_ok=True)
                    
                    # cert.pem (authentication cert)
                    cert_pem = cloudflared_home / "cert.pem"
                    if cert_pem.exists():
                        shutil.copy2(cert_pem, home_backup / "cert.pem")
                    
                    # *.json (tunnel credentials)
                    for json_file in cloudflared_home.glob("*.json"):
                        shutil.copy2(json_file, home_backup / json_file.name)
                
                # Backup systemd services
                services_backup = cf_backup_dir / "services"
                services_backup.mkdir(exist_ok=True)
                for service_file in Path("/etc/systemd/system").glob("cloudflared-*.service"):
                    shutil.copy2(service_file, services_backup / service_file.name)
                
                network_info["cloudflare"] = {
                    "enabled": True,
                    "authenticated": cloudflared.is_authenticated(),
                    "tunnels": tunnel_configs,
                    "tunnel_count": len(paperless_tunnels)  # Only count paperless tunnels
                }
                ok(f"Cloudflare config backed up ({len(tunnel_configs)} tunnel configs)")
            
            # Backup rclone config
            if rclone_conf.exists():
                say("Backing up rclone configuration...")
                rclone_backup_dir = network_dir / "rclone"
                rclone_backup_dir.mkdir(exist_ok=True)
                shutil.copy2(rclone_conf, rclone_backup_dir / "rclone.conf")
                network_info["rclone"] = {"enabled": True}
                ok("rclone config backed up")
            
            # ─── Backup Consume Folder Services Configuration ─────────────────
            consume_backup_dir = work / "consume"
            consume_backup_dir.mkdir(parents=True, exist_ok=True)
            consume_info = {"enabled": False, "global_config": False, "samba_config": False, "sftp_config": False}
            
            # Global consume config
            global_consume_conf = Path("/etc/paperless-bulletproof/consume-global.conf")
            if global_consume_conf.exists():
                shutil.copy2(global_consume_conf, consume_backup_dir / "consume-global.conf")
                consume_info["global_config"] = True
            
            # Samba config directory
            samba_config_dir = Path("/etc/paperless-bulletproof/samba")
            if samba_config_dir.exists():
                samba_backup = consume_backup_dir / "samba"
                shutil.copytree(samba_config_dir, samba_backup, dirs_exist_ok=True)
                consume_info["samba_config"] = True
            
            # SFTP config directory
            sftp_config_dir = Path("/etc/paperless-bulletproof/sftp")
            if sftp_config_dir.exists():
                sftp_backup = consume_backup_dir / "sftp"
                shutil.copytree(sftp_config_dir, sftp_backup, dirs_exist_ok=True)
                consume_info["sftp_config"] = True
            
            consume_info["enabled"] = consume_info["global_config"] or consume_info["samba_config"] or consume_info["sftp_config"]
            if consume_info["enabled"]:
                network_info["consume"] = consume_info
                ok("Consume folder services config backed up")
            
            # Note Tailscale status (can't really backup Tailscale auth)
            if tailscale_connected:
                ts_hostname = None
                ts_ip = None
                try:
                    ts_hostname = tailscale.get_hostname()
                    ts_ip = tailscale.get_ip()
                except:
                    pass
                network_info["tailscale"] = {
                    "enabled": True,
                    "hostname": ts_hostname,
                    "ip": ts_ip,
                    "note": "Tailscale requires re-authentication on new server"
                }
            
            # ─── Backup Instance Information ──────────────────────────────────
            system_info = {
                "backup_date": datetime.now(timezone.utc).isoformat(),
                "backup_name": backup_name,
                "backup_version": "2.1",  # Version 2.1 adds consume config
                "instance_count": len(instances),
                "network": network_info,
                "instances": {},
                "instances_registry": json.loads(self.instance_manager.config_file.read_text()) if self.instance_manager.config_file.exists() else {}
            }
            
            for inst in instances:
                inst_info = {
                    "name": inst.name,
                    "stack_dir": str(inst.stack_dir),
                    "data_root": str(inst.data_root),
                    "running": inst.is_running(),  # Call method to get bool, not method object
                    "env_vars": {},
                    "latest_backup": None
                }
                
                # Capture key env variables
                if inst.env_file.exists():
                    for key in ["DOMAIN", "PAPERLESS_URL", "POSTGRES_DB", "ENABLE_TRAEFIK", 
                               "ENABLE_CLOUDFLARED", "ENABLE_TAILSCALE", "HTTP_PORT",
                               "RCLONE_REMOTE_PATH", "INSTANCE_NAME", "COMPOSE_PROJECT_NAME"]:
                        inst_info["env_vars"][key] = inst.get_env_value(key, "")
                
                # Find latest backup for this instance
                try:
                    backup_mgr = BackupManager(inst)
                    snaps = backup_mgr.fetch_snapshots()
                    if snaps:
                        inst_info["latest_backup"] = snaps[0][0]  # First is newest after sorting
                except:
                    pass
                
                system_info["instances"][inst.name] = inst_info
            
            (work / "system-info.json").write_text(json.dumps(system_info, indent=2))
            
            # Create manifest
            manifest = f"""system_backup: true
backup_version: "2.1"
backup_date: {datetime.now(timezone.utc).isoformat()}
instance_count: {len(instances)}
network_config: true
traefik_enabled: {network_info['traefik']['enabled']}
cloudflare_tunnels: {len(network_info['cloudflare'].get('tunnels', []))}
rclone_config: {network_info['rclone']['enabled']}
consume_config: {network_info.get('consume', {}).get('enabled', False)}
"""
            (work / "manifest.yaml").write_text(manifest)
            
            # Upload to pCloud
            remote = f"pcloud:backups/paperless-system/{backup_name}"
            say("Uploading to backup server...")
            subprocess.run(
                ["rclone", "copy", str(work), remote],
                check=True,
                stdout=subprocess.DEVNULL
            )
            
            ok(f"System backup created: {backup_name}")
            print()
            
            box_line, box_width = create_box_helper(80)
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Backup Contents:', Colors.BOLD)}"))
            print(box_line(""))
            print(box_line(" ✓ Instance registry and metadata"))
            print(box_line(" ✓ References to latest data backups"))
            if network_info["traefik"]["enabled"]:
                print(box_line(" ✓ Traefik config + SSL certificates"))
            if network_info["cloudflare"]["enabled"]:
                print(box_line(f" ✓ Cloudflare tunnel configs ({len(network_info['cloudflare']['tunnels'])})"))
            if network_info["rclone"]["enabled"]:
                print(box_line(" ✓ rclone backup server config"))
            if network_info.get("consume", {}).get("enabled"):
                print(box_line(" ✓ Consume folder services config (Samba/SFTP)"))
            if network_info["tailscale"]["enabled"]:
                print(box_line(" ✓ Tailscale info (requires re-auth)"))
            print(draw_box_bottom(box_width))
            print()
            print("To restore on a new server:")
            print("  1. Install paperless-bulletproof")
            print("  2. Configure backup server connection")
            print("  3. Use 'Restore system from backup'")
            
            # Cleanup
            shutil.rmtree(work)
            
        except Exception as e:
            error(f"System backup failed: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def _view_system_backups(self) -> None:
        """View available system backups with delete option."""
        while True:
            print_header("System Backups")
            
            try:
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless-system"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn("No system backups found")
                    input("\nPress Enter to continue...")
                    return
                
                backups = sorted([l.split()[-1] for l in result.stdout.splitlines() if l.strip()], reverse=True)
                backup_info = []
                
                print(colorize("Available System Backups:", Colors.BOLD))
                print()
                
                for idx, backup in enumerate(backups, 1):
                    # Get backup info
                    try:
                        info = subprocess.run(
                            ["rclone", "cat", f"pcloud:backups/paperless-system/{backup}/system-info.json"],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if info.returncode == 0:
                            import json
                            data = json.loads(info.stdout)
                            inst_count = data.get("instance_count", "?")
                            print(f"  {idx}) {backup} - {inst_count} instance(s)")
                            backup_info.append((backup, inst_count))
                        else:
                            print(f"  {idx}) {backup}")
                            backup_info.append((backup, "?"))
                    except:
                        print(f"  {idx}) {backup}")
                        backup_info.append((backup, "?"))
                
                print()
                print(f"  {colorize('d)', Colors.BOLD)} Delete a backup")
                print(f"  {colorize('0)', Colors.BOLD)} Back")
                print()
                
                choice = get_input("Select option", "0")
                
                if choice == "0":
                    return
                elif choice.lower() == "d":
                    # Delete a backup
                    print()
                    del_choice = get_input(f"Enter backup number to delete [1-{len(backups)}]", "")
                    if del_choice.isdigit() and 1 <= int(del_choice) <= len(backups):
                        backup_to_delete = backups[int(del_choice) - 1]
                        if confirm(f"Delete system backup '{backup_to_delete}'?", False):
                            say(f"Deleting {backup_to_delete}...")
                            del_result = subprocess.run(
                                ["rclone", "purge", f"pcloud:backups/paperless-system/{backup_to_delete}"],
                                capture_output=True,
                                check=False
                            )
                            if del_result.returncode == 0:
                                ok(f"Deleted {backup_to_delete}")
                            else:
                                error(f"Failed to delete: {del_result.stderr}")
                            input("\nPress Enter to continue...")
                    else:
                        warn("Invalid selection")
                        input("\nPress Enter to continue...")
                else:
                    # View details of a specific backup (just refresh for now)
                    pass
                
            except Exception as e:
                error(f"Failed to list system backups: {e}")
                input("\nPress Enter to continue...")
                return
    
    def _restore_system(self) -> None:
        """Restore system from backup including network configuration."""
        print_header("Restore System from Backup")
        
        box_line, box_width = create_box_helper(80)
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('System Restore - Disaster Recovery', Colors.BOLD)}"))
        print(draw_box_divider(box_width))
        print(box_line(" This will restore:"))
        print(box_line("   • Instance registry and metadata"))
        print(box_line("   • Traefik configuration + SSL certificates"))
        print(box_line("   • Cloudflare tunnel configs and credentials"))
        print(box_line("   • Backup server (rclone) configuration"))
        print(box_line("   • Samba/SFTP configs (Syncthing is per-instance)"))
        print(draw_box_divider(box_width))
        print(box_line(f" {colorize('Note:', Colors.YELLOW)} Tailscale requires re-authentication"))
        print(draw_box_bottom(box_width))
        print()
        
        try:
            result = subprocess.run(
                ["rclone", "lsd", "pcloud:backups/paperless-system"],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode != 0 or not result.stdout.strip():
                warn("No system backups found")
                input("\nPress Enter to continue...")
                return
            
            backups = sorted([l.split()[-1] for l in result.stdout.splitlines() if l.strip()], reverse=True)
            
            print(colorize("Available system backups:", Colors.BOLD))
            print()
            
            for idx, backup in enumerate(backups, 1):
                # Get backup info
                try:
                    info = subprocess.run(
                        ["rclone", "cat", f"pcloud:backups/paperless-system/{backup}/system-info.json"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if info.returncode == 0:
                        import json
                        data = json.loads(info.stdout)
                        inst_count = data.get("instance_count", "?")
                        version = data.get("backup_version", "1.0")
                        network = "+" if data.get("network") else ""
                        print(f"  {idx}) {backup} - {inst_count} instance(s) {f'[v{version} network]' if network else ''}")
                    else:
                        print(f"  {idx}) {backup}")
                except:
                    print(f"  {idx}) {backup}")
            print()
            
            choice = get_input(f"Select backup [1-{len(backups)}] or 'cancel'", "cancel")
            
            if not choice.isdigit() or not (1 <= int(choice) <= len(backups)):
                return
            
            backup_name = backups[int(choice) - 1]
            
            # Download and parse system info
            say("Downloading system backup...")
            import json
            import tempfile
            import shutil
            
            work = Path(tempfile.mkdtemp(prefix="paperless-system-restore-"))
            subprocess.run(
                ["rclone", "copy", f"pcloud:backups/paperless-system/{backup_name}", str(work)],
                check=True,
                stdout=subprocess.DEVNULL
            )
            
            system_info = json.loads((work / "system-info.json").read_text())
            network_info = system_info.get("network", {})
            backup_version = system_info.get("backup_version", "1.0")
            
            print()
            print(draw_box_top(box_width))
            print(box_line(f" {colorize('Backup Details', Colors.BOLD)}"))
            print(draw_box_divider(box_width))
            print(box_line(f" Name: {backup_name}"))
            print(box_line(f" Date: {system_info['backup_date'][:19]}"))
            print(box_line(f" Instances: {system_info['instance_count']}"))
            print(box_line(f" Version: {backup_version}"))
            print(draw_box_divider(box_width))
            
            if backup_version >= "2.0" and network_info:
                print(box_line(f" {colorize('Network Configuration:', Colors.BOLD)}"))
                traefik_info = network_info.get("traefik", {})
                cf_info = network_info.get("cloudflare", {})
                rclone_info = network_info.get("rclone", {})
                ts_info = network_info.get("tailscale", {})
                consume_info = network_info.get("consume", {})
                
                if traefik_info.get("enabled"):
                    print(box_line(f"   ✓ Traefik + SSL certificates"))
                else:
                    print(box_line(f"   ○ Traefik: not configured"))
                
                if cf_info.get("enabled"):
                    tunnels = cf_info.get("tunnels", [])
                    print(box_line(f"   ✓ Cloudflare: {len(tunnels)} tunnel(s)"))
                else:
                    print(box_line(f"   ○ Cloudflare: not configured"))
                
                if rclone_info.get("enabled"):
                    print(box_line(f"   ✓ rclone backup config"))
                else:
                    print(box_line(f"   ○ rclone: not configured"))
                
                if consume_info.get("enabled"):
                    print(box_line(f"   ✓ Samba/SFTP configs"))
                else:
                    print(box_line(f"   ○ Samba/SFTP: not configured"))
                
                if ts_info.get("enabled"):
                    print(box_line(f"   ⚠ Tailscale: requires re-auth"))
                else:
                    print(box_line(f"   ○ Tailscale: not configured"))
            else:
                print(box_line(f" {colorize('Note:', Colors.YELLOW)} Legacy backup (no network config)"))
                traefik_info = {"enabled": system_info.get("traefik_enabled", False)}
                cf_info = {}
                rclone_info = {}
                ts_info = {}
                consume_info = {}
            
            print(draw_box_divider(box_width))
            print(box_line(" Instances:"))
            
            # Gather snapshot info for each instance
            instance_snapshots = {}  # {inst_name: {"latest": ..., "at_backup": ..., "all": [...]}}
            for inst_name, inst_data in system_info["instances"].items():
                rclone_path = inst_data.get("env_vars", {}).get("RCLONE_REMOTE_PATH", f"backups/paperless/{inst_name}")
                at_backup = inst_data.get("latest_backup")
                
                try:
                    result = subprocess.run(
                        ["rclone", "lsd", f"pcloud:{rclone_path}"],
                        capture_output=True, text=True, check=False, timeout=10
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        all_snaps = sorted([l.split()[-1] for l in result.stdout.splitlines() if l.strip()])
                        latest = all_snaps[-1] if all_snaps else None
                    else:
                        all_snaps = []
                        latest = None
                except:
                    all_snaps = []
                    latest = None
                
                instance_snapshots[inst_name] = {
                    "latest": latest,
                    "at_backup": at_backup,
                    "all": all_snaps,
                    "rclone_path": rclone_path,
                    "selected": latest  # Default to latest
                }
                
                # Display
                if latest:
                    if at_backup and at_backup != latest:
                        print(box_line(f"   • {inst_name}: latest={latest[:16]}, at-backup={at_backup[:16]}"))
                    else:
                        print(box_line(f"   • {inst_name}: {latest[:19]}"))
                else:
                    print(box_line(f"   • {inst_name}: {colorize('no backups found', Colors.RED)}"))
            
            print(draw_box_bottom(box_width))
            print()
            
            # Snapshot selection
            print(colorize("Snapshot Selection:", Colors.BOLD))
            print("  1) Use latest snapshot for all instances (recommended)")
            print("  2) Use snapshot from system backup time")
            print("  3) Custom - choose snapshot per instance")
            print("  0) Cancel")
            print()
            
            snap_choice = get_input("Select option", "1")
            
            if snap_choice == "0":
                shutil.rmtree(work)
                return
            elif snap_choice == "2":
                # Use snapshots from backup time
                for inst_name, snap_info in instance_snapshots.items():
                    if snap_info["at_backup"] and snap_info["at_backup"] in snap_info["all"]:
                        snap_info["selected"] = snap_info["at_backup"]
                    else:
                        # Fallback to latest if backup-time snapshot no longer exists
                        if snap_info["at_backup"]:
                            warn(f"{inst_name}: backup-time snapshot {snap_info['at_backup'][:16]} not found, using latest")
                ok("Using snapshots from system backup time")
            elif snap_choice == "3":
                # Custom selection per instance
                print()
                for inst_name, snap_info in instance_snapshots.items():
                    if not snap_info["all"]:
                        warn(f"{inst_name}: No backups available, skipping")
                        continue
                    
                    print(f"\n{colorize(inst_name, Colors.BOLD)} - Available snapshots:")
                    # Show newest first
                    display_snaps = list(reversed(snap_info["all"]))
                    for idx, snap in enumerate(display_snaps[:10], 1):  # Show max 10
                        markers = []
                        if snap == snap_info["latest"]:
                            markers.append(colorize("latest", Colors.GREEN))
                        if snap == snap_info["at_backup"]:
                            markers.append(colorize("at-backup", Colors.CYAN))
                        marker_str = f" ({', '.join(markers)})" if markers else ""
                        print(f"    {idx}) {snap}{marker_str}")
                    if len(snap_info["all"]) > 10:
                        print(f"    ... and {len(snap_info['all']) - 10} more")
                    
                    say("Tip: Enter 'L' for latest")
                    choice = get_input(f"Select snapshot for {inst_name} [1-{min(10, len(display_snaps))}, L=latest]", "L")
                    
                    if choice.lower() == "l":
                        snap_info["selected"] = snap_info["latest"]
                    elif choice.isdigit() and 1 <= int(choice) <= min(10, len(display_snaps)):
                        snap_info["selected"] = display_snaps[int(choice) - 1]
                    else:
                        snap_info["selected"] = snap_info["latest"]
                        
                ok("Custom snapshots selected")
            else:
                # Default: use latest
                ok("Using latest snapshots")
            
            # Show final selection
            print()
            print(colorize("Will restore:", Colors.BOLD))
            for inst_name, snap_info in instance_snapshots.items():
                if snap_info["selected"]:
                    print(f"  • {inst_name}: {snap_info['selected']}")
                else:
                    print(f"  • {inst_name}: {colorize('SKIP (no backup)', Colors.RED)}")
            print()
            
            if not confirm("Proceed with restore?", False):
                shutil.rmtree(work)
                return
            
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import traefik, cloudflared
            
            # ─── Restore Network Configurations ───────────────────────────────
            print()
            say("Restoring Network Configuration...")
            
            # Restore rclone config first (needed for other restores)
            rclone_backup = work / "network" / "rclone"
            if rclone_backup.exists() and (rclone_backup / "rclone.conf").exists():
                say("Restoring rclone configuration...")
                rclone_dest = Path.home() / ".config" / "rclone"
                rclone_dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rclone_backup / "rclone.conf", rclone_dest / "rclone.conf")
                ok("rclone config restored")
            
            # Restore Traefik
            traefik_backup = work / "network" / "traefik"
            if traefik_backup.exists() and traefik_info.get("enabled"):
                say("Restoring Traefik configuration...")
                traefik_dest = Path("/opt/traefik")
                traefik_dest.mkdir(parents=True, exist_ok=True)
                
                # Copy traefik.yml
                if (traefik_backup / "traefik.yml").exists():
                    shutil.copy2(traefik_backup / "traefik.yml", traefik_dest / "traefik.yml")
                
                # Copy acme.json (SSL certs) with correct permissions
                if (traefik_backup / "acme.json").exists():
                    shutil.copy2(traefik_backup / "acme.json", traefik_dest / "acme.json")
                    (traefik_dest / "acme.json").chmod(0o600)
                
                # Start or restart Traefik to pick up restored config
                if traefik.is_traefik_running():
                    # Restart to pick up restored config/certs
                    say("Restarting Traefik with restored certificates...")
                    subprocess.run(["docker", "restart", "traefik-system"], check=False, capture_output=True)
                    ok("Traefik restarted with restored SSL certificates")
                else:
                    say("Starting Traefik with restored certificates...")
                    # Ensure traefik network exists
                    traefik.ensure_traefik_network()
                    
                    # Start Traefik container directly (matches setup_system_traefik)
                    acme_file = traefik_dest / "acme.json"
                    config_file = traefik_dest / "traefik.yml"
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
                        ], check=True, capture_output=True)
                        ok("Traefik started with restored SSL certificates")
                    except subprocess.CalledProcessError:
                        # May need to set up fresh
                        email = traefik_info.get("email", "admin@example.com")
                        if traefik.setup_system_traefik(email):
                            ok("Traefik reinstalled (will regenerate SSL certs)")
                        else:
                            warn("Failed to start Traefik")
            elif traefik_info.get("enabled") and not traefik_backup.exists():
                # Legacy backup - just install Traefik
                if not traefik.is_traefik_running():
                    say("System backup had Traefik enabled. Installing Traefik...")
                    email = get_email_input("Let's Encrypt email for SSL certificates", "admin@example.com")
                    if traefik.setup_system_traefik(email):
                        ok("Traefik installed and running")
                    else:
                        warn("Failed to install Traefik - HTTPS instances may not work")
            
            # Restore Cloudflare tunnel configs
            cf_backup = work / "network" / "cloudflared"
            if cf_backup.exists() and cf_info.get("enabled"):
                say("Restoring Cloudflare Tunnel configuration...")
                
                # Restore ~/.cloudflared/ - but ONLY cert.pem (authentication)
                # Tunnel credentials will be created fresh during instance restore
                # (old tunnels are invalid after nuke, need new ones)
                home_backup = cf_backup / "home"
                if home_backup.exists():
                    cloudflared_home = Path.home() / ".cloudflared"
                    cloudflared_home.mkdir(parents=True, exist_ok=True)
                    
                    # Only restore cert.pem - this is the Cloudflare auth, not tunnel-specific
                    cert_pem = home_backup / "cert.pem"
                    if cert_pem.exists():
                        shutil.copy2(cert_pem, cloudflared_home / "cert.pem")
                    ok("Cloudflare authentication restored")
                
                # DON'T restore old tunnel configs or services
                # Instance restore will create fresh tunnels with new credentials
                # Old tunnels/configs are invalid after system nuke
                say("Cloudflare tunnels will be recreated during instance restore")
            
            # Restore Consume Folder Services Configuration (Samba/SFTP)
            consume_backup = work / "consume"
            consume_info = network_info.get("consume", {})
            if consume_backup.exists() and consume_info.get("enabled"):
                say("Restoring Consume Folder Services configuration...")
                
                # Restore global consume config
                global_conf_backup = consume_backup / "consume-global.conf"
                if global_conf_backup.exists():
                    dest_dir = Path("/etc/paperless-bulletproof")
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(global_conf_backup, dest_dir / "consume-global.conf")
                    ok("Global consume config restored")
                
                # Restore Samba config directory
                samba_backup = consume_backup / "samba"
                if samba_backup.exists():
                    samba_dest = Path("/etc/paperless-bulletproof/samba")
                    samba_dest.mkdir(parents=True, exist_ok=True)
                    for file in samba_backup.iterdir():
                        if file.is_file():
                            shutil.copy2(file, samba_dest / file.name)
                        elif file.is_dir():
                            shutil.copytree(file, samba_dest / file.name, dirs_exist_ok=True)
                    ok("Samba config restored")
                
                # Restore SFTP config directory
                sftp_backup = consume_backup / "sftp"
                if sftp_backup.exists():
                    sftp_dest = Path("/etc/paperless-bulletproof/sftp")
                    sftp_dest.mkdir(parents=True, exist_ok=True)
                    for file in sftp_backup.iterdir():
                        if file.is_file():
                            shutil.copy2(file, sftp_dest / file.name)
                        elif file.is_dir():
                            shutil.copytree(file, sftp_dest / file.name, dirs_exist_ok=True)
                    ok("SFTP config restored")
                
                ok("Consume folder services configuration restored")
            
            # ─── Restore Each Instance from Selected Backup ─────────────────────
            # NOTE: We DON'T restore the registry from backup!
            # Instead, each instance registers itself after successful restoration.
            # This prevents "ghost" entries where registry has instances but files don't exist.
            print()
            say("Restoring instance data from selected backups...")
            print()
            
            # CRITICAL: Ensure the library is installed/updated BEFORE any restore operations
            # ALWAYS update to ensure we have the latest code (fixes bug where old lib persists)
            lib_install_dir = Path("/usr/local/lib/paperless-bulletproof/lib")
            say("Updating library (required for restore operations)...")
            # Find where we're running from and copy lib folder
            current_lib = Path(__file__).resolve().parent
            lib_install_dir.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            if current_lib.exists():
                # Remove old library if it exists (ensures clean update)
                if lib_install_dir.exists():
                    shutil.rmtree(lib_install_dir)
                shutil.copytree(current_lib, lib_install_dir)
                # Also copy paperless.py
                paperless_src = current_lib.parent / "paperless.py"
                paperless_dst = lib_install_dir.parent / "paperless.py"
                if paperless_src.exists():
                    shutil.copy2(paperless_src, paperless_dst)
                    paperless_dst.chmod(0o755)
                ok("Library updated")
            else:
                error(f"Cannot find source library at {current_lib}")
                raise Exception("Library installation failed - cannot proceed with restore")
            
            sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
            from lib.installer import common, files
            
            instances_to_restore = system_info.get("instances", {})
            restored_count = 0
            failed_instances = []
            
            for inst_name, inst_data in instances_to_restore.items():
                print(f"\n{colorize('─' * 60, Colors.CYAN)}")
                say(f"Restoring instance: {colorize(inst_name, Colors.BOLD)}")
                
                # Get pre-selected snapshot info
                snap_info = instance_snapshots.get(inst_name, {})
                selected_snapshot = snap_info.get("selected")
                rclone_path = snap_info.get("rclone_path", f"backups/paperless/{inst_name}")
                
                if not selected_snapshot:
                    warn(f"  No snapshot selected for {inst_name}, skipping")
                    failed_instances.append((inst_name, "No snapshot available"))
                    continue
                
                try:
                    # Get instance paths from backup info
                    stack_dir = Path(inst_data.get("stack_dir", f"/home/docker/{inst_name}-setup"))
                    data_root = Path(inst_data.get("data_root", f"/home/docker/{inst_name}"))
                    remote_base = f"pcloud:{rclone_path}"
                    
                    say(f"  Using backup: {selected_snapshot}")
                    
                    # Validate backup actually has required files
                    validation_result = subprocess.run(
                        ["rclone", "lsf", f"{remote_base}/{selected_snapshot}"],
                        capture_output=True, text=True, check=False
                    )
                    if validation_result.returncode != 0 or ".env" not in validation_result.stdout:
                        warn(f"  Backup {selected_snapshot} appears incomplete or corrupted")
                        failed_instances.append((inst_name, f"Backup {selected_snapshot} incomplete"))
                        continue
                    
                    # Download the .env from the backup to get original settings
                    env_result = subprocess.run(
                        ["rclone", "cat", f"{remote_base}/{selected_snapshot}/.env"],
                        capture_output=True, text=True, check=False
                    )
                    
                    backup_env = {}
                    if env_result.returncode == 0 and env_result.stdout.strip():
                        for line in env_result.stdout.splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                backup_env[k.strip()] = v.strip()
                    
                    # Configure instance settings from backup
                    common.cfg.instance_name = inst_name
                    common.cfg.data_root = str(data_root)
                    common.cfg.stack_dir = str(stack_dir)
                    common.cfg.rclone_remote_name = "pcloud"
                    common.cfg.rclone_remote_path = f"backups/paperless/{inst_name}"
                    common.cfg.refresh_paths()
                    
                    # Load all settings from backup
                    load_backup_env_config(backup_env)
                    
                    common.cfg.refresh_paths()
                    
                    # Create directories
                    say(f"  Creating directories...")
                    common.ensure_dir_tree(common.cfg)
                    
                    # Write config files
                    say(f"  Writing configuration...")
                    files.write_env_file()
                    files.write_compose_file()
                    files.copy_helper_scripts()
                    
                    # Run the actual restore
                    say(f"  Restoring data...")
                    success = run_restore_with_env(
                        stack_dir=stack_dir,
                        data_root=data_root,
                        instance_name=inst_name,
                        remote_name="pcloud",
                        remote_path=rclone_path,  # Use the saved path, not hardcoded
                        snapshot=selected_snapshot,
                        fresh_config=True,  # Use fresh config since manager wrote it
                        restore_syncthing=True  # Restore Syncthing for disaster recovery
                    )
                    
                    if not success:
                        raise Exception("Restore operation failed")
                    
                    # Install backup cron
                    files.install_cron_backup()
                    
                    # Set up Cloudflare tunnel if it was enabled
                    if common.cfg.enable_cloudflared == "yes" and cf_info.get("enabled"):
                        say(f"  Setting up Cloudflare tunnel...")
                        port = int(common.cfg.http_port)
                        setup_cloudflare_tunnel(inst_name, common.cfg.domain, port)
                    
                    # Register the instance in the registry
                    self.instance_manager.add_instance(inst_name, stack_dir, data_root)
                    
                    # Auto-repair CSRF settings after restore (handles old backups)
                    restored_instance = self.instance_manager.get_instance(inst_name)
                    if restored_instance:
                        self._repair_csrf_settings(restored_instance)
                    
                    # Set up Tailscale serve if it was enabled (after Tailscale reconnection)
                    # This will be handled after Tailscale reconnects
                    
                    ok(f"  Instance '{inst_name}' restored successfully")
                    restored_count += 1
                    
                except Exception as e:
                    error(f"  Failed to restore {inst_name}: {e}")
                    failed_instances.append((inst_name, str(e)))
            
            # Reload instances after restoration
            self.instance_manager.load_instances()
            
            # ─── Start Consume Services (Samba/SFTP) ────────────────────────────
            # Samba: per-instance containers, SFTP: shared container
            # Initialize these before the block so they're available for the summary
            need_samba = False
            need_sftp = False
            
            if consume_info.get("enabled") and restored_count > 0:
                print()
                say("Starting consume folder services...")
                
                from lib.installer.consume import (
                    start_samba, is_samba_running,
                    start_sftp_container, is_sftp_available,
                    load_consume_config, restart_sftp_with_config
                )
                
                # Collect all instance configs for SFTP (shared container)
                instances_config = {}
                data_roots = {}
                samba_started = 0
                samba_failed = []
                need_samba = False
                need_sftp = False
                
                for inst in self.instance_manager.list_instances():
                    try:
                        config = load_consume_config(inst.env_file)
                        instances_config[inst.name] = config
                        data_roots[inst.name] = inst.data_root
                        
                        # Start per-instance Samba container if enabled
                        if config.samba.enabled:
                            need_samba = True
                            say(f"  Starting Samba for {inst.name}...")
                            consume_path = inst.data_root / "consume"
                            puid = int(inst.get_env_value("PUID", "1000"))
                            pgid = int(inst.get_env_value("PGID", "1000"))
                            if start_samba(inst.name, config.samba, consume_path, puid=puid, pgid=pgid):
                                samba_started += 1
                            else:
                                samba_failed.append(inst.name)
                        
                        if config.sftp.enabled:
                            need_sftp = True
                    except Exception as e:
                        warn(f"  Could not load config for {inst.name}: {e}")
                
                # Report Samba status
                if samba_started > 0:
                    ok(f"Samba started for {samba_started} instance(s)")
                if samba_failed:
                    warn(f"  Failed to start Samba for: {', '.join(samba_failed)}")
                    warn("  Run 'Disable/Enable Samba' for these instances to retry")
                
                # Start SFTP if any instance needs it
                if need_sftp:
                    say("Configuring SFTP...")
                    # SFTP container reads config from instances at startup
                    if not is_sftp_available():
                        start_sftp_container(instances_config, data_roots)
                    else:
                        # Restart to pick up new users
                        restart_sftp_with_config(instances_config, data_roots)
                    ok("SFTP configured")
            
            # ─── Summary & IP Change Guidance ─────────────────────────────────
            print()
            print(f"\n{colorize('─' * 60, Colors.CYAN)}")
            print()
            print(draw_box_top(box_width))
            
            # Determine overall status
            total_instances = len(instances_to_restore)
            if restored_count == total_instances and total_instances > 0:
                # All succeeded
                print(box_line(f" {colorize('✓ System Restore Complete', Colors.GREEN)}"))
            elif restored_count > 0:
                # Partial success
                print(box_line(f" {colorize('⚠ System Restore Partially Complete', Colors.YELLOW)}"))
            else:
                # All failed
                print(box_line(f" {colorize('✗ System Restore Failed', Colors.RED)}"))
            print(draw_box_divider(box_width))
            
            # Instance restoration summary
            print(box_line(f" {colorize('Instance Restoration:', Colors.BOLD)}"))
            if restored_count > 0:
                print(box_line(f"   ✓ {restored_count} instance(s) restored successfully"))
            if failed_instances:
                print(box_line(f"   ✗ {len(failed_instances)} instance(s) failed:"))
                for name, reason in failed_instances:
                    print(box_line(f"     • {name}: {reason}"))
            print(box_line(""))
            
            # Next steps - only if there were failures or additional config needed
            if failed_instances:
                print(box_line(f" {colorize('For failed instances:', Colors.YELLOW)}"))
                print(box_line("   → Manage Instances → [instance] → Restore snapshot"))
                print(box_line(""))
            
            if restored_count > 0:
                print(box_line(f" {colorize('To start your instances:', Colors.BOLD)}"))
                print(box_line("   → Manage Instances → [instance] → Container operations → Start"))
            
            # Consume services note - these are now auto-restored
            if consume_info.get("enabled") and (need_samba or need_sftp):
                print(box_line(""))
                print(box_line(f" {colorize('Consume folder services:', Colors.BOLD)}"))
                if need_samba:
                    print(box_line("   ✓ Samba shares restored and configured"))
                if need_sftp:
                    print(box_line("   ✓ SFTP users restored and configured"))
                print(box_line("   ✓ Syncthing containers restored (per-instance)"))
            
            print(draw_box_divider(box_width))
            
            # IP/Server change guidance
            print(box_line(f" {colorize('If Server IP Changed:', Colors.YELLOW)}"))
            print(box_line(""))
            
            # Traefik guidance
            if traefik_info.get("enabled"):
                print(box_line(f"   {colorize('Traefik (HTTPS):', Colors.CYAN)}"))
                print(box_line("   → Update DNS A records to point to new IP"))
                print(box_line("   → SSL certs restored (will auto-renew)"))
                print(box_line(""))
            
            # Cloudflare guidance
            if cf_info.get("enabled"):
                print(box_line(f"   {colorize('Cloudflare Tunnels:', Colors.CYAN)}"))
                print(box_line("   → Tunnels auto-reconnect (IP doesn't matter)"))
                print(box_line("   → Check: systemctl status cloudflared-*"))
                print(box_line("   → If issues: cloudflared service install"))
                print(box_line(""))
            
            # Tailscale guidance  
            if ts_info.get("enabled"):
                print(box_line(f"   {colorize('Tailscale:', Colors.CYAN)}"))
                print(box_line(f"   → Previous: {ts_info.get('hostname', '?')} ({ts_info.get('ip', '?')})"))
                print(box_line("   → Re-authenticate: sudo tailscale up"))
                print(box_line("   → Re-enable serve paths for each instance"))
                print(box_line(""))
            
            # If no network config needed special handling
            if not (traefik_info.get("enabled") or cf_info.get("enabled") or ts_info.get("enabled")):
                print(box_line("   → No network services need reconfiguration"))
                print(box_line(""))
            
            print(draw_box_bottom(box_width))
            
            # Print command reference
            print()
            print(colorize("Useful Commands:", Colors.BOLD))
            print("  paperless                     - Open management TUI")
            print("  systemctl status cloudflared-*  - Check Cloudflare tunnels")
            print("  tailscale status              - Check Tailscale connection")
            print("  docker ps                     - Check running containers")
            
            shutil.rmtree(work)
            
            # Offer to reconnect Tailscale if it was previously enabled
            if ts_info.get("enabled"):
                print()
                from lib.installer.tailscale import is_tailscale_installed, is_connected, connect
                if is_tailscale_installed() and not is_connected():
                    if confirm("Reconnect Tailscale now?", True):
                        say("Starting Tailscale authentication...")
                        if connect():
                            ok("Tailscale reconnected!")
                            # Note: Serve paths need to be re-enabled per instance
                            say("Note: Re-enable Tailscale Serve for each instance if needed")
                        else:
                            warn("Tailscale connection failed - reconnect from Manage Tailscale menu")
            
        except Exception as e:
            error(f"System restore failed: {e}")
            import traceback
            traceback.print_exc()
        
        input("\nPress Enter to continue...")
    
    def backups_menu(self) -> None:
        """Backups explorer and management."""
        while True:
            print_header("Backup Explorer")
            
            say("Scanning backup server...")
            
            try:
                # Get all instance folders from backup
                result = subprocess.run(
                    ["rclone", "lsd", "pcloud:backups/paperless"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10
                )
                
                if result.returncode != 0 or not result.stdout.strip():
                    warn("No backups found or unable to connect")
                    input("\nPress Enter to continue...")
                    return
                
                # Parse instance names
                backup_instances = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if parts:
                        backup_instances.append(parts[-1])
                
                if not backup_instances:
                    warn("No backup instances found")
                    input("\nPress Enter to continue...")
                    return
                
                # Show instances
                print(f"Backed up instances ({len(backup_instances)}):")
                for idx, name in enumerate(backup_instances, 1):
                    # Count snapshots for this instance
                    snap_result = subprocess.run(
                        ["rclone", "lsd", f"pcloud:backups/paperless/{name}"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5
                    )
                    snap_count = len([l for l in snap_result.stdout.splitlines() if l.strip()])
                    
                    # Also check archive folder
                    arch_result = subprocess.run(
                        ["rclone", "lsd", f"pcloud:backups/paperless/{name}/archive"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5
                    )
                    arch_count = len([l for l in arch_result.stdout.splitlines() if l.strip()])
                    
                    if arch_count > 0:
                        print(f"  {idx}) {name} ({snap_count} snapshots, {arch_count} archives)")
                    else:
                        print(f"  {idx}) {name} ({snap_count} snapshots)")
                print()
                
                options = [(str(i), f"Explore '{backup_instances[i-1]}'" ) for i in range(1, len(backup_instances) + 1)]
                options.append(("", colorize("─── Maintenance ───", Colors.CYAN)))
                options.append((str(len(backup_instances) + 1), colorize("🔄", Colors.YELLOW) + " Run retention cleanup (all instances)"))
                options.append((str(len(backup_instances) + 2), colorize("🧹", Colors.YELLOW) + " Clean empty folders (auto)"))
                options.append((str(len(backup_instances) + 3), colorize("🧹", Colors.YELLOW) + " Clean empty folders (select)"))
                options.append(("0", "Back to main menu"))
                print_menu(options)
                
                choice = get_input("Select instance", "")
                
                if choice == "0":
                    break
                elif choice.isdigit() and 1 <= int(choice) <= len(backup_instances):
                    self._explore_instance_backups(backup_instances[int(choice) - 1])
                elif choice == str(len(backup_instances) + 1):
                    self._run_global_retention_cleanup()
                elif choice == str(len(backup_instances) + 2):
                    self._clean_empty_backup_folders()
                elif choice == str(len(backup_instances) + 3):
                    self._clean_empty_backup_folders_selective()
                else:
                    warn("Invalid option")
                    
            except Exception as e:
                error(f"Failed to list backups: {e}")
                input("\nPress Enter to continue...")
                return
    
    def _explore_instance_backups(self, instance_name: str) -> None:
        """Explore backups for a specific instance."""
        while True:
            print_header(f"Backups: {instance_name}")
            
            remote_path = f"pcloud:backups/paperless/{instance_name}"
            
            try:
                # Use shared method from BackupManager to fetch snapshots
                snapshot_objs = BackupManager.fetch_snapshots_for_path(remote_path)
                
                if not snapshot_objs:
                    warn(f"No snapshots found for {instance_name}")
                    input("\nPress Enter to continue...")
                    return
                
                # Convert Snapshot objects to tuples for existing code
                snapshots = [
                    (s.name, s.mode, s.parent, s.created, s.has_docker_versions)
                    for s in snapshot_objs
                ]
                
                # Display snapshots
                print(colorize("Available Snapshots:", Colors.BOLD))
                print()
                print(f"{colorize('#', Colors.BOLD):<5} {colorize('Snapshot Name', Colors.BOLD):<30} {colorize('Mode', Colors.BOLD):<10} {colorize('Created', Colors.BOLD):<20} {colorize('Docker', Colors.BOLD)}")
                print(colorize("─" * 85, Colors.CYAN))
                
                for idx, (name, mode, parent, created, has_vers) in enumerate(snapshots, 1):
                    mode_color = Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN
                    vers_icon = colorize("✓", Colors.GREEN) if has_vers else colorize("✗", Colors.RED)
                    print(f"{idx:<5} {name:<30} {colorize(mode.upper(), mode_color):<20} {created:<20} {vers_icon}")
                print()
                
                # Options
                options = []
                for i in range(1, len(snapshots) + 1):
                    options.append((str(i), f"View details of snapshot #{i}"))
                options.append((str(len(snapshots) + 1), colorize("↻", Colors.GREEN) + " Restore to new instance"))
                options.append((str(len(snapshots) + 2), colorize("🔄", Colors.YELLOW) + " Run retention cleanup"))
                options.append((str(len(snapshots) + 3), colorize("✗", Colors.RED) + " Delete snapshot"))
                options.append((str(len(snapshots) + 4), colorize("🗑", Colors.RED) + " Delete entire backup folder"))
                options.append(("0", colorize("◀ Back", Colors.CYAN)))
                print_menu(options)
                
                choice = get_input("Select option", "")
                
                if choice == "0":
                    break
                elif choice.isdigit() and 1 <= int(choice) <= len(snapshots):
                    self._view_snapshot_details(instance_name, snapshots[int(choice) - 1])
                elif choice == str(len(snapshots) + 1):
                    self._restore_from_explorer(instance_name, snapshots)
                elif choice == str(len(snapshots) + 2):
                    self._run_instance_retention_cleanup_from_explorer(instance_name)
                elif choice == str(len(snapshots) + 3):
                    self._delete_snapshot(instance_name, snapshots)
                elif choice == str(len(snapshots) + 4):
                    self._delete_instance_backup_folder(instance_name, len(snapshots))
                else:
                    warn("Invalid option")
                    
            except Exception as e:
                error(f"Failed to explore backups: {e}")
                input("\nPress Enter to continue...")
                return
    
    def _view_snapshot_details(self, instance_name: str, snapshot: tuple) -> None:
        """View detailed information about a snapshot."""
        name, mode, parent, created, has_versions = snapshot
        
        print_header(f"Snapshot: {name}")
        
        box_line, box_width = create_box_helper(80)
        
        print(draw_box_top(box_width))
        print(box_line(f" Instance:  {colorize(instance_name, Colors.BOLD)}"))
        print(box_line(f" Snapshot:  {name}"))
        mode_display = colorize(mode.upper(), Colors.GREEN if mode == "full" else Colors.YELLOW if mode == "incr" else Colors.CYAN)
        print(box_line(f" Mode:      {mode_display}"))
        print(box_line(f" Created:   {created}"))
        if mode == "incr" and parent != "?":
            print(box_line(f" Parent:    {parent}"))
        print(draw_box_bottom(box_width))
        print()
        
        remote_path = f"pcloud:backups/paperless/{instance_name}/{name}"
        
        # Show Docker versions FIRST and prominently if available
        if has_versions:
            print(colorize("▸ Docker Container Versions at Backup Time:", Colors.BOLD))
            print()
            versions = subprocess.run(
                ["rclone", "cat", f"{remote_path}/docker-images.txt"],
                capture_output=True,
                text=True,
                check=False
            )
            if versions.returncode == 0:
                for line in versions.stdout.strip().splitlines():
                    # Parse and colorize
                    if ":" in line:
                        print(f"  {colorize('•', Colors.GREEN)} {line}")
                    else:
                        print(f"  {line}")
            else:
                warn("Could not load Docker version information")
            print()
        else:
            warn("⚠  No Docker version information in this snapshot")
            print("   (This snapshot was created before version tracking was added)")
            print()
        
        # Show files in snapshot
        print(colorize("▸ Snapshot Contents:", Colors.BOLD))
        print()
        result = subprocess.run(
            ["rclone", "ls", remote_path],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            total_size = 0
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    size_bytes = int(parts[0])
                    total_size += size_bytes
                    filename = parts[1]
                    # Convert to human readable
                    if size_bytes < 1024:
                        size = f"{size_bytes}B"
                    elif size_bytes < 1024 * 1024:
                        size = f"{size_bytes / 1024:.1f}KB"
                    elif size_bytes < 1024 * 1024 * 1024:
                        size = f"{size_bytes / (1024 * 1024):.1f}MB"
                    else:
                        size = f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"
                    
                    # Color-code file types
                    if filename.endswith('.tar.gz'):
                        filename = colorize(filename, Colors.CYAN)
                    elif filename.endswith('.sql'):
                        filename = colorize(filename, Colors.GREEN)
                    elif filename.endswith('.yaml') or filename.endswith('.yml'):
                        filename = colorize(filename, Colors.YELLOW)
                    
                    print(f"  {size:>10}  {filename}")
            
            # Show total
            if total_size > 0:
                if total_size < 1024 * 1024 * 1024:
                    total = f"{total_size / (1024 * 1024):.1f}MB"
                else:
                    total = f"{total_size / (1024 * 1024 * 1024):.2f}GB"
                print()
                print(f"  {colorize('Total:', Colors.BOLD)} {colorize(total, Colors.GREEN)}")
        
        print()
        input("\nPress Enter to continue...")
    
    def _run_global_retention_cleanup(self) -> None:
        """Run retention cleanup for all configured instances."""
        print_header("Global Retention Cleanup")
        
        # Get all configured instances
        instances = self.instance_manager.list_instances()
        
        if not instances:
            warn("No instances configured")
            input("\nPress Enter to continue...")
            return
        
        # Show retention policy summary
        box_line, box_width = create_box_helper(80)
        print(draw_box_top(box_width))
        print(box_line(f" {colorize('Retention Cleanup', Colors.BOLD)}"))
        print(box_line(f""))
        print(box_line(f" This will apply retention policy to all instances:"))
        print(box_line(f"   • Delete standard backups older than RETENTION_DAYS"))
        print(box_line(f"   • Keep only monthly archives beyond that"))
        print(box_line(f"   • Delete monthly archives older than RETENTION_MONTHLY_DAYS"))
        print(box_line(f""))
        print(box_line(f" Instances to process: {len(instances)}"))
        for inst in instances:
            ret_days = inst.get_env_value('RETENTION_DAYS', '30')
            ret_monthly = inst.get_env_value('RETENTION_MONTHLY_DAYS', '180')
            print(box_line(f"   • {inst.name}: {ret_days}d / {ret_monthly}d monthly"))
        print(draw_box_bottom(box_width))
        print()
        
        if not confirm("Run retention cleanup for all instances?", True):
            return
        
        print()
        for inst in instances:
            say(f"Processing {inst.name}...")
            try:
                backup_script = inst.stack_dir / "backup.py"
                if backup_script.exists():
                    result = subprocess.run(
                        ["python3", str(backup_script), "cleanup"],
                        capture_output=True,
                        text=True,
                        check=False,
                        env={**os.environ, "ENV_FILE": str(inst.env_file)}
                    )
                    if result.returncode == 0:
                        ok(f"  {inst.name}: cleanup complete")
                    else:
                        warn(f"  {inst.name}: cleanup failed")
                else:
                    warn(f"  {inst.name}: backup script not found")
            except Exception as e:
                error(f"  {inst.name}: {e}")
        
        print()
        ok("Global retention cleanup finished")
        input("\nPress Enter to continue...")

    def _clean_empty_backup_folders(self) -> None:
        """Scan and delete empty instance backup folders (lists before deleting)."""
        print_header("Clean Empty Backup Folders")
        try:
            # List instance directories
            result = subprocess.run(
                ["rclone", "lsd", "pcloud:backups/paperless"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            if result.returncode != 0:
                error("Unable to list backup root")
                input("\nPress Enter to continue...")
                return
            instance_dirs = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
            empty = []
            for name in instance_dirs:
                check = subprocess.run(
                    ["rclone", "lsd", f"pcloud:backups/paperless/{name}"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                # Consider empty when there are no snapshot subfolders
                if not check.stdout.strip():
                    empty.append(name)
            
            if not empty:
                ok("No empty backup folders found")
                input("\nPress Enter to continue...")
                return
            
            print(colorize("Empty backup folders:", Colors.BOLD))
            for name in empty:
                print(f"  • {name}")
            print()
            if confirm("Delete ALL listed empty folders?", False):
                deleted = 0
                for name in empty:
                    try:
                        # Purge (safe even if empty); ensures removal across remotes
                        subprocess.run(["rclone", "purge", f"pcloud:backups/paperless/{name}"], check=False)
                        deleted += 1
                    except Exception:
                        pass
                ok(f"Deleted {deleted}/{len(empty)} empty folders")
            else:
                say("No changes made")
        except Exception as e:
            error(f"Cleanup failed: {e}")
        input("\nPress Enter to continue...")

    def _clean_empty_backup_folders_selective(self) -> None:
        """List empty instance backup folders and allow selective deletion."""
        print_header("Clean Empty Folders (Select)")
        try:
            result = subprocess.run(
                ["rclone", "lsd", "pcloud:backups/paperless"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            if result.returncode != 0:
                error("Unable to list backup root")
                input("\nPress Enter to continue...")
                return
            instance_dirs = [l.split()[-1] for l in result.stdout.splitlines() if l.strip()]
            empties = []
            for name in instance_dirs:
                check = subprocess.run(
                    ["rclone", "lsd", f"pcloud:backups/paperless/{name}"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                if not check.stdout.strip():
                    empties.append(name)
            if not empties:
                ok("No empty backup folders found")
                input("\nPress Enter to continue...")
                return
            print(colorize("Empty folders:", Colors.BOLD))
            for idx, name in enumerate(empties, 1):
                print(f"  {idx}) {name}")
            print()
            choice = get_input("Enter numbers to delete (space-separated), 'all' or 'cancel'", "cancel")
            if choice == "cancel":
                say("Cancelled")
                input("\nPress Enter to continue...")
                return
            targets = empties if choice.strip().lower() == "all" else []
            if not targets:
                for part in choice.split():
                    if part.isdigit() and 1 <= int(part) <= len(empties):
                        targets.append(empties[int(part) - 1])
            if not targets:
                warn("No valid selections")
                input("\nPress Enter to continue...")
                return
            if confirm(f"Delete {len(targets)} empty folder(s)?", False):
                deleted = 0
                for name in targets:
                    try:
                        subprocess.run(["rclone", "purge", f"pcloud:backups/paperless/{name}"], check=False)
                        deleted += 1
                    except Exception:
                        pass
                ok(f"Deleted {deleted}/{len(targets)} folders")
            else:
                say("No changes made")
        except Exception as e:
            error(f"Cleanup failed: {e}")
        input("\nPress Enter to continue...")
    
    def _run_instance_retention_cleanup_from_explorer(self, instance_name: str) -> None:
        """Run retention cleanup for an instance from the backup explorer."""
        print_header(f"Retention Cleanup: {instance_name}")
        
        # Check if this instance is currently configured locally
        instances = self.instance_manager.list_instances()
        local_instance = next((i for i in instances if i.name == instance_name), None)
        
        if local_instance:
            # Use the local instance's settings
            retention = local_instance.get_env_value('RETENTION_DAYS', '30')
            retention_monthly = local_instance.get_env_value('RETENTION_MONTHLY_DAYS', '180')
            
            say(f"Using local instance settings:")
            say(f"  • Keep all backups for: {retention} days")
            say(f"  • Keep monthly archives for: {retention_monthly} days")
            print()
            
            if confirm("Run retention cleanup with these settings?", True):
                backup_script = local_instance.stack_dir / "backup.py"
                if backup_script.exists():
                    say("Running cleanup...")
                    result = subprocess.run(
                        ["python3", str(backup_script), "cleanup"],
                        capture_output=True,
                        text=True,
                        check=False,
                        env={**os.environ, "ENV_FILE": str(local_instance.env_file)}
                    )
                    if result.returncode == 0:
                        ok("Retention cleanup complete")
                        if result.stdout:
                            print(result.stdout)
                    else:
                        error("Cleanup failed")
                        if result.stderr:
                            print(result.stderr)
                else:
                    error("Backup script not found")
        else:
            # Instance not configured locally - offer manual cleanup
            warn(f"Instance '{instance_name}' is not configured on this system")
            say("You can specify custom retention settings for cleanup:")
            print()
            
            retention = get_input("Keep all backups for how many days?", "30")
            retention_monthly = get_input("Keep monthly archives for how many days?", "180")
            
            if not confirm(f"Clean up backups older than {retention}d, keep monthly for {retention_monthly}d?", False):
                input("\nPress Enter to continue...")
                return
            
            # Run cleanup directly using rclone
            say("Running manual cleanup...")
            remote_path = f"pcloud:backups/paperless/{instance_name}"
            
            try:
                # Delete standard backups older than retention_days
                say(f"  Cleaning standard backups older than {retention} days...")
                subprocess.run(
                    ["rclone", "delete", remote_path, "--min-age", f"{retention}d", "--fast-list"],
                    check=False
                )
                subprocess.run(["rclone", "rmdirs", remote_path, "--leave-root"], check=False)
                
                # For archives, we'd need more complex logic - just inform user
                say(f"  Note: Archive cleanup requires instance to be configured locally")
                say(f"         for full monthly retention logic")
                
                ok("Basic cleanup complete")
            except Exception as e:
                error(f"Cleanup failed: {e}")
        
        input("\nPress Enter to continue...")
    
    def _restore_from_explorer(self, instance_name: str, snapshots: list) -> None:
        """Restore a snapshot to a new instance - uses unified restore method."""
        print("Select snapshot to restore:")
        for idx, (name, mode, parent, created, _) in enumerate(snapshots, 1):
            latest_marker = " (latest)" if idx == 1 else ""
            print(f"  {idx}) {name} ({mode}, {created}){latest_marker}")
        print()
        
        say("Tip: Enter 'L' for latest snapshot")
        choice = get_input(f"Select snapshot [1-{len(snapshots)}, L=latest]", "L")
        
        if choice.lower() == "l":
            snapshot_name = snapshots[0][0]  # Latest is now first
        elif choice.isdigit() and 1 <= int(choice) <= len(snapshots):
            snapshot_name = snapshots[int(choice) - 1][0]
        else:
            return
        
        # Use the unified restore method
        self.restore_instance_from_backup(backup_instance=instance_name, snapshot=snapshot_name)
    
    def _delete_snapshot(self, instance_name: str, snapshots: list) -> None:
        """Delete a snapshot from backup server."""
        print_header("Delete Snapshot")
        
        warn("⚠️  DANGER: This permanently deletes the backup!")
        print()
        
        print("Select snapshot to DELETE:")
        for idx, (name, mode, parent, created, _) in enumerate(snapshots, 1):
            print(f"  {idx}) {name} ({mode}, {created})")
        print()
        
        choice = get_input(f"Select snapshot [1-{len(snapshots)}] or 'cancel'", "cancel")
        
        if not choice.isdigit() or not (1 <= int(choice) <= len(snapshots)):
            return
        
        snapshot_name = snapshots[int(choice) - 1][0]
        
        print()
        if confirm(f"PERMANENTLY DELETE {instance_name}/{snapshot_name}?", False):
            remote_path = f"pcloud:backups/paperless/{instance_name}/{snapshot_name}"
            try:
                say(f"Deleting {snapshot_name}...")
                subprocess.run(["rclone", "purge", remote_path], check=True)
                ok("Snapshot deleted")
            except Exception as e:
                error(f"Failed to delete snapshot: {e}")
        
        input("\nPress Enter to continue...")

    def _delete_instance_backup_folder(self, instance_name: str, snapshot_count: int) -> None:
        """Delete the entire backup folder for an instance (warn if non-empty)."""
        print_header("Delete Backup Folder")
        if snapshot_count > 0:
            warn(f"Folder '{instance_name}' contains {snapshot_count} snapshot(s)")
        else:
            say(f"Folder '{instance_name}' is empty")
        print()
        confirm_text = get_input(f"Type DELETE {instance_name} to confirm (or just 'DELETE')", "")
        if confirm_text not in (f"DELETE {instance_name}", "DELETE"):
            say("Cancelled")
            input("\nPress Enter to continue...")
            return
        try:
            subprocess.run(["rclone", "purge", f"pcloud:backups/paperless/{instance_name}"], check=False)
            ok(f"Deleted backup folder '{instance_name}'")
        except Exception as e:
            error(f"Failed to delete folder: {e}")
        input("\nPress Enter to continue...")
    
    def cloudflared_menu(self) -> None:
        """Cloudflare Tunnel management menu."""
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import cloudflared
        
        while True:
            print_header("Manage Cloudflare Tunnel")
            
            installed = cloudflared.is_cloudflared_installed()
            authenticated = cloudflared.is_authenticated() if installed else False
            
            if not installed:
                say(colorize("⚠ Cloudflared not installed", Colors.YELLOW))
                print("\nCloudflare Tunnel provides secure access without exposing ports.")
                print()
                options = [("1", "Install cloudflared"), ("0", "Back to main menu")]
            elif not authenticated:
                say(colorize("⚠ Not authenticated with Cloudflare", Colors.YELLOW))
                print()
                options = [("1", "Authenticate with Cloudflare"), ("0", "Back to main menu")]
            else:
                say(colorize("✓ Cloudflared installed and authenticated", Colors.GREEN))
                
                # Show per-instance tunnel status
                instances = self.instance_manager.list_instances()
                tunnels = cloudflared.list_tunnels()
                paperless_tunnels = [t for t in tunnels if t.get('name', '').startswith('paperless-')]
                
                if instances:
                    print()
                    print(colorize("Instance Tunnel Status:", Colors.BOLD))
                    for inst in instances:
                        tunnel = cloudflared.get_tunnel_for_instance(inst.name)
                        cf_enabled = inst.get_env_value("ENABLE_CLOUDFLARED", "no") == "yes"
                        
                        # Check if service is running
                        service_active = False
                        try:
                            result = subprocess.run(
                                ["systemctl", "is-active", f"cloudflared-{inst.name}"],
                                capture_output=True, check=False
                            )
                            service_active = result.returncode == 0
                        except:
                            pass
                        
                        if tunnel and service_active:
                            status = colorize("● Active", Colors.GREEN)
                            domain = inst.get_env_value("DOMAIN", "?")
                            print(f"  {inst.name}: {status} → https://{domain}")
                        elif tunnel:
                            status = colorize("○ Configured", Colors.YELLOW)
                            print(f"  {inst.name}: {status} (tunnel exists, service stopped)")
                        elif cf_enabled:
                            status = colorize("⚠ Misconfigured", Colors.RED)
                            print(f"  {inst.name}: {status} (enabled but no tunnel)")
                        else:
                            status = colorize("○ Not enabled", Colors.CYAN)
                            print(f"  {inst.name}: {status}")
                
                print()
                options = [
                    ("1", "List all tunnels"),
                    ("2", "Enable tunnel for an instance"),
                    ("3", "Disable tunnel for an instance"),
                    ("0", "Back to main menu")
                ]
            
            print_menu(options)
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if not installed:
                    if cloudflared.install_cloudflared():
                        ok("Cloudflared installed!")
                    else:
                        error("Installation failed")
                elif not authenticated:
                    if cloudflared.authenticate():
                        ok("Authentication successful!")
                    else:
                        error("Authentication failed")
                else:
                    # List tunnels
                    tunnels = cloudflared.list_tunnels()
                    paperless_tunnels = [t for t in tunnels if t.get('name', '').startswith('paperless-')]
                    other_tunnels = [t for t in tunnels if not t.get('name', '').startswith('paperless-')]
                    
                    print()
                    if paperless_tunnels:
                        print(colorize("Paperless Tunnels:", Colors.BOLD))
                        for t in paperless_tunnels:
                            print(f"  {t.get('name')} - {t.get('id')}")
                    else:
                        say("No paperless tunnels found")
                    
                    if other_tunnels:
                        print()
                        if confirm(f"Show {len(other_tunnels)} non-paperless tunnel(s)?", False):
                            print(colorize("\nOther Tunnels:", Colors.BOLD))
                            for t in other_tunnels:
                                print(f"  {t.get('name')} - {t.get('id')}")
                input("\nPress Enter to continue...")
            elif choice == "2" and authenticated:
                # Enable tunnel for an instance
                instances = self.instance_manager.list_instances()
                available = [i for i in instances if i.get_env_value("ENABLE_CLOUDFLARED", "no") != "yes"]
                if not available:
                    say("All instances already have Cloudflare enabled")
                else:
                    print("\nSelect instance to enable Cloudflare tunnel:")
                    for idx, inst in enumerate(available, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(available)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(available):
                        inst = available[int(sel) - 1]
                        self._toggle_instance_cloudflare(inst)
                input("\nPress Enter to continue...")
            elif choice == "3" and authenticated:
                # Disable tunnel for an instance
                instances = self.instance_manager.list_instances()
                enabled = [i for i in instances if i.get_env_value("ENABLE_CLOUDFLARED", "no") == "yes"]
                if not enabled:
                    say("No instances have Cloudflare enabled")
                else:
                    print("\nSelect instance to disable Cloudflare tunnel:")
                    for idx, inst in enumerate(enabled, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(enabled)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(enabled):
                        inst = enabled[int(sel) - 1]
                        self._toggle_instance_cloudflare(inst)
                input("\nPress Enter to continue...")
    
    def tailscale_menu(self) -> None:
        """Tailscale management menu."""
        sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
        from lib.installer import tailscale
        
        while True:
            print_header("Manage Tailscale")
            
            installed = tailscale.is_tailscale_installed()
            connected = tailscale.is_connected() if installed else False
            
            if not installed:
                say(colorize("⚠ Tailscale not installed", Colors.YELLOW))
                print("\nTailscale provides secure private network access.")
                print()
                options = [("1", "Install Tailscale"), ("0", "Back to main menu")]
            elif not connected:
                say(colorize("⚠ Tailscale not connected", Colors.YELLOW))
                print()
                options = [
                    ("1", "Connect to Tailscale"),
                    ("0", "Back to main menu")
                ]
            else:
                say(colorize("✓ Tailscale connected", Colors.GREEN))
                ip = tailscale.get_ip()
                hostname = tailscale.get_hostname()
                
                if hostname:
                    print(f"Hostname: {colorize(hostname, Colors.CYAN)}")
                if ip:
                    print(f"IP: {colorize(ip, Colors.CYAN)}")
                
                # Show per-instance Tailscale status
                instances = self.instance_manager.list_instances()
                if instances:
                    print()
                    print(colorize("Instance Tailscale Status:", Colors.BOLD))
                    for inst in instances:
                        ts_enabled = inst.get_env_value("ENABLE_TAILSCALE", "no") == "yes"
                        port = inst.get_env_value("HTTP_PORT", "8000")
                        
                        if ts_enabled:
                            status = colorize("● HTTP", Colors.GREEN)
                            print(f"  {inst.name}: {status} → http://{ip}:{port}")
                        else:
                            status = colorize("○ Not enabled", Colors.CYAN)
                            print(f"  {inst.name}: {status}")
                
                print()
                options = [
                    ("1", "View status"),
                    ("2", "Enable Tailscale for an instance"),
                    ("3", "Disable Tailscale for an instance"),
                    ("4", "Disconnect from Tailscale"),
                    ("0", "Back to main menu")
                ]
            
            print_menu(options)
            choice = get_input("Select option", "")
            
            if choice == "0":
                break
            elif choice == "1":
                if not installed:
                    if tailscale.install_tailscale():
                        ok("Tailscale installed!")
                    else:
                        error("Installation failed")
                elif not connected:
                    if tailscale.connect():
                        ok("Connected to Tailscale!")
                        # Recreate Syncthing containers to bind to Tailscale IP
                        self._recreate_all_syncthing_containers("Tailscale connected")
                    else:
                        error("Connection failed")
                else:
                    # Show status
                    print(tailscale.get_status())
                input("\nPress Enter to continue...")
            elif choice == "2" and connected:
                # Enable Tailscale for an instance
                instances = self.instance_manager.list_instances()
                available = [i for i in instances if i.get_env_value("ENABLE_TAILSCALE", "no") != "yes"]
                if not available:
                    say("All instances already have Tailscale enabled")
                else:
                    print("\nSelect instance to enable Tailscale:")
                    for idx, inst in enumerate(available, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(available)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(available):
                        inst = available[int(sel) - 1]
                        self._toggle_instance_tailscale(inst)
                input("\nPress Enter to continue...")
            elif choice == "3" and connected:
                # Disable Tailscale for an instance
                instances = self.instance_manager.list_instances()
                enabled = [i for i in instances if i.get_env_value("ENABLE_TAILSCALE", "no") == "yes"]
                if not enabled:
                    say("No instances have Tailscale enabled")
                else:
                    print("\nSelect instance to disable Tailscale:")
                    for idx, inst in enumerate(enabled, 1):
                        print(f"  {idx}) {inst.name}")
                    sel = get_input(f"Select [1-{len(enabled)}]", "")
                    if sel.isdigit() and 1 <= int(sel) <= len(enabled):
                        inst = enabled[int(sel) - 1]
                        self._toggle_instance_tailscale(inst)
                input("\nPress Enter to continue...")
            elif choice == "4" and connected:
                if tailscale.disconnect():
                    ok("Disconnected from Tailscale")
                    # Recreate Syncthing containers to bind to localhost
                    self._recreate_all_syncthing_containers("Tailscale disconnected")
                input("\nPress Enter to continue...")
    
    def nuke_setup(self) -> None:
        """Nuclear option - delete all instances and Docker resources with optional cleanups."""
        print_header("Nuke Setup (Clean Start)")
        
        instances = self.instance_manager.list_instances()
        
        warn("This will DELETE core system components:")
        print("  • All Docker containers (stopped and running)")
        print("  • All Docker networks")
        print("  • All Docker volumes")
        print("  • All instance directories (/home/docker/*)")
        print("  • All instance tracking data")
        print()
        
        # Optional cleanups
        print(colorize("Optional cleanups (you will be asked):", Colors.YELLOW))
        print("  • Traefik configuration")
        print("  • Cloudflare tunnels (only paperless-* tunnels)")
        print("  • Tailscale connection")
        print("  • All pCloud backups")
        print()
        
        # Single confirmation with NUKE
        confirmation = get_input("Type the word NUKE in capitals to confirm", "")
        if confirmation != "NUKE":
            say("Cancelled - confirmation did not match")
            input("\nPress Enter to continue...")
            return
        
        # Ask about optional cleanups
        delete_traefik = confirm("Also delete Traefik configuration?", False)
        delete_cloudflared = confirm("Also delete Cloudflare tunnels? (only paperless-* tunnels)", False)
        delete_tailscale = confirm("Also disconnect Tailscale?", False)
        delete_backups = False
        if self.rclone_configured:
            print()
            warn("⚠️  DANGER ZONE ⚠️")
            warn("Deleting backups is PERMANENT and IRREVERSIBLE!")
            warn("Your backups are the ONLY way to recover your documents.")
            print()
            if confirm("Do you want to delete ALL cloud backups?", False):
                # Require typing confirmation for this destructive action
                print()
                warn("To confirm backup deletion, type: DELETE BACKUPS")
                backup_confirm = get_input("Type confirmation", "")
                if backup_confirm == "DELETE BACKUPS":
                    delete_backups = True
                    error("Backups WILL be permanently deleted!")
                else:
                    say("Backup deletion cancelled - backups will be preserved")
                    delete_backups = False
        
        print()
        say("Starting nuclear cleanup...")
        print()
        
        # Check if Traefik was running (to restart it after cleanup)
        from lib.installer.traefik import is_traefik_running, get_traefik_email
        traefik_was_running = is_traefik_running() if not delete_traefik else False
        traefik_email = get_traefik_email() if traefik_was_running else None
        
        try:
            # ─── STEP 1: Stop ALL containers first (before any file deletion) ───
            say("Stopping ALL Docker containers...")
            
            # Stop Syncthing containers explicitly (they run outside compose)
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=syncthing-", "--format", "{{.Names}}"],
                capture_output=True, text=True, check=False
            )
            for container in result.stdout.splitlines():
                if container.strip():
                    subprocess.run(["docker", "stop", container.strip()], 
                                   capture_output=True, check=False)
            
            # Stop Samba/SFTP containers
            subprocess.run(["docker", "stop", "paperless-samba"], capture_output=True, check=False)
            subprocess.run(["docker", "stop", "paperless-sftp"], capture_output=True, check=False)
            
            # Stop Traefik
            subprocess.run(["docker", "stop", "traefik"], capture_output=True, check=False)
            
            # Stop all remaining containers
            subprocess.run(
                "docker stop $(docker ps -aq) 2>/dev/null",
                shell=True, check=False, capture_output=True
            )
            
            # ─── STEP 2: Remove ALL containers ───────────────────────────────
            say("Removing ALL Docker containers...")
            subprocess.run(
                "docker rm -f $(docker ps -aq) 2>/dev/null",
                shell=True, check=False, capture_output=True
            )
            
            # ─── STEP 3: Remove ALL Docker volumes ───────────────────────────
            say("Removing ALL Docker volumes...")
            # Force remove all volumes (not just unused)
            result = subprocess.run(
                ["docker", "volume", "ls", "-q"],
                capture_output=True, text=True, check=False
            )
            for volume in result.stdout.splitlines():
                if volume.strip():
                    subprocess.run(["docker", "volume", "rm", "-f", volume.strip()], 
                                   capture_output=True, check=False)
            # Also prune for good measure
            subprocess.run(["docker", "volume", "prune", "-f"], check=False, capture_output=True)
            
            # ─── STEP 4: Remove ALL Docker networks (except defaults) ────────
            say("Removing Docker networks...")
            result = subprocess.run(
                ["docker", "network", "ls", "--format", "{{.Name}}"],
                capture_output=True, text=True, check=False
            )
            for network in result.stdout.splitlines():
                if network.strip() and network not in ["bridge", "host", "none"]:
                    subprocess.run(["docker", "network", "rm", network], 
                                   check=False, capture_output=True)
            
            # ─── STEP 5: Clean instance tracking BEFORE directory removal ────
            # (so remove_instance doesn't try to access deleted dirs)
            if instances:
                say(f"Cleaning up {len(instances)} instance(s)...")
                for inst in instances:
                    try:
                        # Remove cloudflared service
                        service_file = Path(f"/etc/systemd/system/cloudflared-{inst.name}.service")
                        if service_file.exists():
                            subprocess.run(["systemctl", "stop", f"cloudflared-{inst.name}"], 
                                          capture_output=True, check=False)
                            subprocess.run(["systemctl", "disable", f"cloudflared-{inst.name}"], 
                                          capture_output=True, check=False)
                            service_file.unlink()
                        
                        # Remove Tailscale serve
                        try:
                            port = inst.get_env_value("HTTP_PORT", "8000")
                            subprocess.run(["tailscale", "serve", "off", f":{port}"],
                                          capture_output=True, check=False)
                        except:
                            pass
                        
                        # Remove cron jobs for this instance
                        try:
                            result = subprocess.run(["crontab", "-l"],
                                capture_output=True, text=True, check=False)
                            if result.returncode == 0:
                                lines = result.stdout.splitlines()
                                new_lines = [l for l in lines if inst.name not in l]
                                if len(new_lines) != len(lines):
                                    subprocess.run(["crontab", "-"],
                                        input="\n".join(new_lines) + "\n",
                                        text=True, check=False)
                        except:
                            pass
                    except Exception as e:
                        warn(f"Error cleaning up {inst.name}: {e}")
            
            # ─── STEP 6: Remove ALL instance directories ─────────────────────
            say("Removing ALL instance directories...")
            docker_home = Path("/home/docker")
            if docker_home.exists():
                for item in docker_home.iterdir():
                    # Use rm -rf for reliability with postgres-owned db directories
                    result = subprocess.run(
                        ["rm", "-rf", str(item)],
                        capture_output=True,
                        check=False
                    )
                    if result.returncode != 0:
                        warn(f"Could not remove {item}")
            
            # Optional: Remove Traefik
            if delete_traefik:
                say("Removing Traefik configuration...")
                traefik_dir = Path("/opt/traefik")
                if traefik_dir.exists():
                    subprocess.run(["rm", "-rf", str(traefik_dir)], check=False, capture_output=True)
                ok("Traefik removed")
            
            # Optional: Delete all Cloudflare tunnels and local configs
            if delete_cloudflared:
                say("Deleting all Cloudflare tunnels...")
                sys.path.insert(0, "/usr/local/lib/paperless-bulletproof")
                try:
                    from lib.installer.cloudflared import list_tunnels
                    tunnels = list_tunnels()
                    for tunnel in tunnels:
                        if tunnel.get('name', '').startswith('paperless-'):
                            try:
                                subprocess.run(
                                    ["cloudflared", "tunnel", "delete", "-f", tunnel.get('name')],
                                    check=False,
                                    capture_output=True
                                )
                            except:
                                pass
                    ok("Cloudflare tunnels deleted")
                except Exception as e:
                    warn(f"Could not delete tunnels: {e}")
                
                # Also remove local cloudflared configs
                cloudflared_config_dir = Path("/etc/cloudflared")
                if cloudflared_config_dir.exists():
                    # Remove paperless-related configs but keep cert.pem (auth)
                    for config_file in cloudflared_config_dir.glob("*.yml"):
                        config_file.unlink(missing_ok=True)
                    say("Cloudflared local configs removed")
            
            # Optional: Disconnect Tailscale
            if delete_tailscale:
                say("Disconnecting Tailscale...")
                try:
                    subprocess.run(["tailscale", "logout"], check=False, capture_output=True)
                    ok("Tailscale disconnected")
                except:
                    warn("Could not disconnect Tailscale")
            
            # Optional: Delete all backups
            if delete_backups:
                warn("Deleting ALL pCloud backups...")
                try:
                    # Delete instance backups
                    subprocess.run(
                        ["rclone", "purge", "pcloud:backups/paperless"],
                        check=False,
                        capture_output=True
                    )
                    # Also delete system backups
                    subprocess.run(
                        ["rclone", "purge", "pcloud:backups/paperless-system"],
                        check=False,
                        capture_output=True
                    )
                    ok("All backups deleted (instance + system)")
                except Exception as e:
                    warn(f"Could not delete backups: {e}")
            
            # Remove instance tracking
            say("Removing instance tracking...")
            tracking_file = Path("/etc/paperless-bulletproof/instances.json")
            if tracking_file.exists():
                tracking_file.unlink()
            
            # Also remove old tracking file location if it exists
            old_tracking = Path("/root/.paperless_instances.json")
            if old_tracking.exists():
                old_tracking.unlink()
            
            # Remove consume service config files
            say("Removing consume service configs...")
            consume_config_files = [
                Path("/etc/paperless-bulletproof/consume-global.conf"),
                Path("/etc/paperless-bulletproof/samba"),
                Path("/etc/paperless-bulletproof/sftp"),
            ]
            for config_path in consume_config_files:
                if config_path.exists():
                    subprocess.run(["rm", "-rf", str(config_path)], 
                                   capture_output=True, check=False)
            
            ok("Consume folder services cleaned up")
            
            # Reload instance manager to reflect changes
            self.instance_manager = InstanceManager()
            
            # Restart Traefik if it was running and not deleted
            if traefik_was_running:
                say("Restarting Traefik (was running before nuke)...")
                try:
                    from lib.installer.traefik import setup_system_traefik
                    if setup_system_traefik(traefik_email or "admin@example.com"):
                        ok("Traefik restarted")
                    else:
                        warn("Could not restart Traefik - use Manage Traefik menu")
                except Exception as e:
                    warn(f"Could not restart Traefik: {e}")
            
            ok("Nuclear cleanup complete!")
            say("System is now in clean state")
            if not delete_backups:
                say("Backups preserved on pCloud")
            say("You can start fresh by creating new instances")
            
        except Exception as e:
            error(f"Cleanup error: {e}")
        
        input("\nPress Enter to continue...")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Paperless-NGX Bulletproof Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    args = parser.parse_args()
    
    try:
        app = PaperlessManager()
        app.run()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user\n")
        sys.exit(0)
    except Exception as e:
        error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
