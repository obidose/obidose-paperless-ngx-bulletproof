#!/usr/bin/env python3
"""
Input validation functions for Paperless-NGX Bulletproof.

Provides validation and input helpers for domains, emails, ports, and instance names.
"""
from __future__ import annotations

import re
import socket
from typing import TYPE_CHECKING

from lib.ui import Colors, colorize, error

if TYPE_CHECKING:
    pass


# ─── Input Helpers ────────────────────────────────────────────────────────────

def get_input(prompt: str, default: str = "") -> str:
    """Get user input with optional default value."""
    if default:
        user_input = input(f"{prompt} [{colorize(default, Colors.CYAN)}]: ").strip()
        return user_input if user_input else default
    return input(f"{prompt}: ").strip()


def confirm(prompt: str, default: bool = False) -> bool:
    """Ask for yes/no confirmation."""
    suffix = "[Y/n]" if default else "[y/N]"
    response = input(f"{prompt} {suffix}: ").strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


# ─── Domain Validation ────────────────────────────────────────────────────────

def is_valid_domain(domain: str) -> tuple[bool, str]:
    """Validate a domain name.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not domain:
        return False, "Domain cannot be empty"
    
    # Check for @ symbol (common mistake: entering email instead of domain)
    if '@' in domain:
        return False, "Domain cannot contain '@' - did you enter an email address?"
    
    # Check for spaces
    if ' ' in domain:
        return False, "Domain cannot contain spaces"
    
    # Check for protocol prefix
    if domain.startswith(('http://', 'https://')):
        return False, "Domain should not include http:// or https://"
    
    # Check for path components
    if '/' in domain:
        return False, "Domain should not include path components"
    
    # Check for port specification
    if ':' in domain:
        return False, "Domain should not include port number"
    
    # Basic domain pattern check (allows subdomains)
    domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
    if not re.match(domain_pattern, domain):
        # Check for common issues
        if domain.startswith('-') or domain.endswith('-'):
            return False, "Domain labels cannot start or end with hyphens"
        if '..' in domain:
            return False, "Domain cannot have consecutive dots"
        if not '.' in domain:
            return False, "Domain must have at least one dot (e.g., example.com)"
        return False, "Invalid domain format"
    
    # Check length constraints
    if len(domain) > 253:
        return False, "Domain name too long (max 253 characters)"
    
    # Check each label length
    for label in domain.split('.'):
        if len(label) > 63:
            return False, f"Domain label '{label}' too long (max 63 characters)"
    
    return True, ""


def get_domain_input(prompt: str, default: str = "") -> str:
    """Get and validate a domain input from user."""
    while True:
        value = get_input(prompt, default)
        is_valid, err_msg = is_valid_domain(value)
        if is_valid:
            return value
        error(err_msg)
        if default:
            default = value  # Use their input as new default for easier correction


# ─── Email Validation ─────────────────────────────────────────────────────────

def is_valid_email(email: str) -> tuple[bool, str]:
    """Validate an email address.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not email:
        return False, "Email cannot be empty"
    
    # Check for spaces
    if ' ' in email:
        return False, "Email cannot contain spaces"
    
    # Check for @ symbol
    if '@' not in email:
        return False, "Email must contain '@'"
    
    # Split into local and domain parts
    parts = email.split('@')
    if len(parts) != 2:
        return False, "Email must have exactly one '@'"
    
    local, domain = parts
    
    # Check local part
    if not local:
        return False, "Email local part (before @) cannot be empty"
    if len(local) > 64:
        return False, "Email local part too long (max 64 characters)"
    
    # Check domain part
    if not domain:
        return False, "Email domain (after @) cannot be empty"
    if not '.' in domain:
        return False, "Email domain must have at least one dot"
    
    # Basic email pattern check
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return False, "Invalid email format"
    
    return True, ""


def get_email_input(prompt: str, default: str = "") -> str:
    """Get and validate an email input from user."""
    while True:
        value = get_input(prompt, default)
        is_valid, err_msg = is_valid_email(value)
        if is_valid:
            return value
        error(err_msg)
        if default:
            default = value


# ─── Port Validation ──────────────────────────────────────────────────────────

def is_valid_port(port: str) -> tuple[bool, str]:
    """Validate a port number.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not port:
        return False, "Port cannot be empty"
    
    try:
        port_num = int(port)
    except ValueError:
        return False, "Port must be a number"
    
    if port_num < 1 or port_num > 65535:
        return False, "Port must be between 1 and 65535"
    
    if port_num < 1024:
        return False, "Ports below 1024 are reserved - use 1024 or higher"
    
    return True, ""


def get_port_input(prompt: str, default: str = "") -> str:
    """Get and validate a port input from user."""
    while True:
        value = get_input(prompt, default)
        is_valid, err_msg = is_valid_port(value)
        if is_valid:
            return value
        error(err_msg)
        if default:
            default = value


# ─── Instance Name Validation ─────────────────────────────────────────────────

def is_valid_instance_name(name: str, existing_instances: list[str] = None) -> tuple[bool, str]:
    """Validate an instance name.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not name:
        return False, "Instance name cannot be empty"
    
    # Check length
    if len(name) < 2:
        return False, "Instance name must be at least 2 characters"
    if len(name) > 32:
        return False, "Instance name too long (max 32 characters)"
    
    # Check for valid characters (lowercase, numbers, hyphens)
    if not re.match(r'^[a-z][a-z0-9-]*[a-z0-9]$', name) and not re.match(r'^[a-z][a-z0-9]?$', name):
        if name[0].isupper() or any(c.isupper() for c in name):
            return False, "Instance name must be lowercase"
        if name[0].isdigit():
            return False, "Instance name must start with a letter"
        if name.endswith('-'):
            return False, "Instance name cannot end with a hyphen"
        if '--' in name:
            return False, "Instance name cannot have consecutive hyphens"
        if not re.match(r'^[a-z0-9-]+$', name):
            return False, "Instance name can only contain lowercase letters, numbers, and hyphens"
        return False, "Invalid instance name format"
    
    # Check for duplicates
    if existing_instances and name in existing_instances:
        return False, f"Instance '{name}' already exists"
    
    return True, ""


def get_instance_name_input(prompt: str, default: str = "", existing_instances: list[str] = None) -> str:
    """Get and validate an instance name input from user."""
    while True:
        value = get_input(prompt, default)
        is_valid, err_msg = is_valid_instance_name(value, existing_instances)
        if is_valid:
            return value
        error(err_msg)
        if default:
            default = value


# ─── Network Utilities ────────────────────────────────────────────────────────

def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('', port))
            return False
        except OSError:
            return True


def find_available_port(start_port: int, max_tries: int = 100) -> int:
    """Find an available port starting from start_port."""
    for offset in range(max_tries):
        port = start_port + offset
        if not is_port_in_use(port):
            return port
    return start_port  # Fall back to original


def get_local_ip() -> str:
    """Get the local IP address of this machine."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Doesn't actually connect, just determines routing
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
