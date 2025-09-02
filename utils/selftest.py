#!/usr/bin/env python3
"""Simple stack health checks used after install and restore."""
from __future__ import annotations

from pathlib import Path
import subprocess


def run_stack_tests(compose_file: Path, env_file: Path) -> bool:
    """Run basic checks against the Paperless stack.

    Returns True if all checks pass, False otherwise.
    """
    ok = True
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "ps",
            ],
            check=True,
        )
    except Exception:
        ok = False
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "exec",
                "-T",
                "paperless",
                "python",
                "manage.py",
                "check",
            ],
            check=True,
        )
    except Exception:
        ok = False
    return ok
