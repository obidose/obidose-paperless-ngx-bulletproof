#!/usr/bin/env python3
"""
UI utilities for Paperless-NGX Bulletproof.

Provides terminal colors, output functions, and box-drawing helpers.
"""
from __future__ import annotations

import re
import sys


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
    DIM = "\033[2m"
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


# ─── Box Drawing Utilities ────────────────────────────────────────────────────

def create_box_helper(width: int = 80):
    """Create a box line formatter for the given width."""
    def box_line(content: str) -> str:
        """Format a line to fit within the box, handling ANSI codes."""
        # Strip ANSI codes for length calculation
        plain = re.sub(r'\033\[[0-9;]*m', '', content)
        padding = width - 4 - len(plain)
        return f"│ {content}{' ' * max(0, padding)} │"
    return box_line


def draw_box_top(width: int = 80) -> str:
    """Draw the top border of a box."""
    return "┌" + "─" * (width - 2) + "┐"


def draw_box_bottom(width: int = 80) -> str:
    """Draw the bottom border of a box."""
    return "└" + "─" * (width - 2) + "┘"


def draw_box_divider(width: int = 80) -> str:
    """Draw a horizontal divider within a box."""
    return "├" + "─" * (width - 2) + "┤"


def draw_section_header(title: str, width: int = 80) -> str:
    """Draw a section header with centered title."""
    title_len = len(title)
    left_padding = (width - 4 - title_len) // 2
    right_padding = width - 4 - title_len - left_padding
    return "│" + " " * left_padding + colorize(title, Colors.BOLD) + " " * right_padding + "│"


# ─── Menu Utilities ───────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    """Print a decorative header."""
    print()
    print(colorize("═" * 60, Colors.CYAN))
    print(colorize(f"  {title}", Colors.BOLD))
    print(colorize("═" * 60, Colors.CYAN))
    print()


def print_menu(options: list[tuple[str, str]], prompt: str = "Choose") -> None:
    """Print a numbered menu of options."""
    for key, label in options:
        print(f"  {colorize(f'{key})', Colors.BOLD)} {label}")
    print()
