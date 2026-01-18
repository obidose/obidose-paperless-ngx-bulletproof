#!/usr/bin/env python3
"""
Shared utilities for Paperless-NGX Bulletproof.

This module consolidates common functionality used across the codebase:
- Terminal colors and output formatting
- Environment file loading
- Docker compose command building
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


# ─── Terminal Colors ──────────────────────────────────────────────────────────

class Colors:
    """ANSI color codes for terminal output."""
    BLUE = "\033[1;34m"
    GREEN = "\033[1;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[1;31m"
    CYAN = "\033[1;36m"
    MAGENTA = "\033[1;35m"
    BOLD = "\033[1m"
    OFF = "\033[0m"


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes."""
    return f"{color}{text}{Colors.OFF}"


# ─── Output Functions ─────────────────────────────────────────────────────────

def say(msg: str) -> None:
    """Print an info message with blue prefix."""
    print(f"{Colors.BLUE}[*]{Colors.OFF} {msg}")


def log(msg: str) -> None:
    """Alias for say()."""
    say(msg)


def ok(msg: str) -> None:
    """Print a success message with green prefix."""
    print(f"{Colors.GREEN}[✓]{Colors.OFF} {msg}")


def warn(msg: str) -> None:
    """Print a warning message with yellow prefix."""
    print(f"{Colors.YELLOW}[!]{Colors.OFF} {msg}")


def error(msg: str) -> None:
    """Print an error message with red prefix."""
    print(f"{Colors.RED}[✗]{Colors.OFF} {msg}")


def die(msg: str, code: int = 1) -> None:
    """Print an error and exit."""
    error(msg)
    sys.exit(code)


# ─── Environment Loading ──────────────────────────────────────────────────────

def load_env(path: Path) -> dict[str, str]:
    """Load environment variables from a .env file, returning as dict.
    
    Args:
        path: Path to the .env file
        
    Returns:
        Dictionary of key-value pairs from the file
    """
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def load_env_to_environ(path: Path) -> None:
    """Load environment variables from a .env file into os.environ.
    
    Only sets variables that aren't already defined (uses setdefault).
    """
    env = load_env(path)
    for key, value in env.items():
        os.environ.setdefault(key, value)


# ─── Docker Compose Helpers ───────────────────────────────────────────────────

def docker_compose_cmd(
    project_name: str,
    compose_file: Path,
    *args: str,
    env_file: Optional[Path] = None
) -> list[str]:
    """Build a docker compose command with project name.
    
    Args:
        project_name: Docker compose project name (e.g., "paperless-myinstance")
        compose_file: Path to docker-compose.yml
        *args: Additional arguments to pass to docker compose
        env_file: Optional path to .env file
        
    Returns:
        List of command arguments ready for subprocess
    """
    cmd = [
        "docker", "compose",
        "--project-name", project_name,
        "-f", str(compose_file),
    ]
    if env_file:
        cmd.extend(["--env-file", str(env_file)])
    cmd.extend(args)
    return cmd
