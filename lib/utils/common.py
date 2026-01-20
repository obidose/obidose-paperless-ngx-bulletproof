#!/usr/bin/env python3
"""
Shared utilities for Paperless-NGX Bulletproof.

This module consolidates common functionality used across the codebase:
- Terminal colors and output formatting (re-exported from lib.ui)
- Environment file loading
- Docker compose command building
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# ─── Re-export terminal utilities from lib.ui ─────────────────────────────────
# This provides backward compatibility for code importing from lib.utils.common

from lib.ui import (
    Colors, colorize, say, log, ok, warn, error, die
)

__all__ = [
    'Colors', 'colorize', 'say', 'log', 'ok', 'warn', 'error', 'die',
    'load_env', 'load_env_to_environ', 'docker_compose_cmd'
]


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

