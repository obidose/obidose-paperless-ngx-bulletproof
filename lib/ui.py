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
    """Create a box line helper with specified inner width.
    
    Returns:
        tuple: (box_line function, width) for compatibility with existing code.
    """
    def box_line(content: str) -> str:
        """Create a properly padded box line with cyan borders."""
        # Strip ANSI codes for length calculation
        clean = re.sub(r'\033\[[0-9;]+m', '', content)
        
        # Account for emoji display width (actual emojis are 2 chars wide visually but count as 1-2 in len())
        # Only include actual wide emojis, NOT simple Unicode symbols like ✓ ○ ● etc.
        wide_emojis = ['🌐', '🔐', '💾', '📋', '🔄', '☁']
        emoji_adjustment = 0
        for emoji in wide_emojis:
            emoji_adjustment += clean.count(emoji)
        
        # Also handle variation selectors (like ☁️ which is ☁ + U+FE0F)
        emoji_adjustment += clean.count('\ufe0f')
        
        padding = width - len(clean) - emoji_adjustment
        if padding < 0:
            truncated = clean[:width-3] + "..."
            return colorize("│", Colors.CYAN) + truncated + colorize("│", Colors.CYAN)
        return colorize("│", Colors.CYAN) + content + " " * padding + colorize("│", Colors.CYAN)
    return box_line, width


def draw_box_top(width: int = 80) -> str:
    """Draw box top border with rounded corners in cyan."""
    return colorize("╭" + "─" * width + "╮", Colors.CYAN)


def draw_box_bottom(width: int = 80) -> str:
    """Draw box bottom border with rounded corners in cyan."""
    return colorize("╰" + "─" * width + "╯", Colors.CYAN)


def draw_box_divider(width: int = 80) -> str:
    """Draw box horizontal divider in cyan."""
    return colorize("├" + "─" * width + "┤", Colors.CYAN)


def draw_section_header(title: str, width: int = 80) -> str:
    """Draw a section header within content area with decorative lines."""
    padding = width - len(title) - 2
    left_pad = padding // 2
    right_pad = padding - left_pad
    return (colorize("│", Colors.CYAN) + " " + 
            colorize("─" * left_pad, Colors.CYAN) + 
            f" {colorize(title, Colors.BOLD)} " + 
            colorize("─" * right_pad, Colors.CYAN) + " " + 
            colorize("│", Colors.CYAN))


# ─── Menu Utilities ───────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    """Print a decorative header box in cyan."""
    width = max(80, len(title) + 10)
    print()
    print(colorize("╔" + "═" * (width - 2) + "╗", Colors.CYAN))
    print(colorize(f"║{title.center(width - 2)}║", Colors.CYAN))
    print(colorize("╚" + "═" * (width - 2) + "╝", Colors.CYAN))
    print()


def print_menu(options: list[tuple[str, str]], prompt: str = "Choose") -> None:
    """Print a menu with numbered options."""
    for key, description in options:
        print(f"  {colorize(key + ')', Colors.BOLD)} {description}")
    print()
    print()
