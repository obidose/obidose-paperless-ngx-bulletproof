"""
UI utilities for the Paperless-ngx bulletproof tool.

This module contains all the visual/UI functions for consistent formatting,
colors, headers, and user feedback throughout the application.
"""

import sys
from pathlib import Path


def _read(prompt: str) -> str:
    """Enhanced read function with TTY detection."""
    # Use sys.stdin/stdout if they're connected to a TTY
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            # Re-raise KeyboardInterrupt instead of returning empty string
            raise KeyboardInterrupt
    
    # Fall back to direct TTY access for non-interactive environments
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(prompt)
            tty.flush()
            return tty.readline().strip()
    except (OSError, IOError):
        # Last resort: use stdin/stdout even if not TTY
        try:
            print(prompt, end="", flush=True)
            return input()
        except (EOFError, KeyboardInterrupt):
            # Re-raise KeyboardInterrupt instead of returning empty string
            raise KeyboardInterrupt


# Enhanced color scheme and visual elements
COLOR_OFF = "\033[0m"
COLOR_RED = "\033[31m"
COLOR_GREEN = "\033[32m"
COLOR_YELLOW = "\033[33m"
COLOR_BLUE = "\033[34m"
COLOR_MAGENTA = "\033[35m"
COLOR_CYAN = "\033[36m"
COLOR_WHITE = "\033[37m"
COLOR_DIM = "\033[2m"
COLOR_BOLD = "\033[1m"

# Status indicators
STATUS_RUNNING = f"{COLOR_GREEN}●{COLOR_OFF}"
STATUS_STOPPED = f"{COLOR_RED}●{COLOR_OFF}"
STATUS_UNKNOWN = f"{COLOR_YELLOW}●{COLOR_OFF}"

# Icons and symbols
ICON_SUCCESS = f"{COLOR_GREEN}✓{COLOR_OFF}"
ICON_ERROR = f"{COLOR_RED}✗{COLOR_OFF}"
ICON_WARNING = f"{COLOR_YELLOW}⚠{COLOR_OFF}"
ICON_INFO = f"{COLOR_BLUE}ℹ{COLOR_OFF}"
ICON_BULLET = f"{COLOR_CYAN}•{COLOR_OFF}"


def print_header(title: str, subtitle: str = "") -> None:
    """Print a styled header with optional subtitle."""
    width = 60
    print()
    print(f"{COLOR_CYAN}╔{'═' * (width-2)}╗{COLOR_OFF}")
    print(f"{COLOR_CYAN}║{COLOR_OFF} {COLOR_BOLD}{title.center(width-2)}{COLOR_OFF} {COLOR_CYAN}║{COLOR_OFF}")
    if subtitle:
        print(f"{COLOR_CYAN}║{COLOR_OFF} {COLOR_DIM}{subtitle.center(width-2)}{COLOR_OFF} {COLOR_CYAN}║{COLOR_OFF}")
    print(f"{COLOR_CYAN}╚{'═' * (width-2)}╝{COLOR_OFF}")
    print()


def print_separator(char: str = "─", length: int = 60) -> None:
    """Print a visual separator line."""
    print(f"{COLOR_DIM}{char * length}{COLOR_OFF}")


def say(msg: str) -> None:
    """Print an informational message."""
    print(f"{ICON_INFO} {msg}")


def ok(msg: str) -> None:
    """Print a success message."""
    print(f"{ICON_SUCCESS} {COLOR_GREEN}{msg}{COLOR_OFF}")


def warn(msg: str) -> None:
    """Print a warning message."""
    print(f"{ICON_WARNING} {COLOR_YELLOW}{msg}{COLOR_OFF}")


def error(msg: str) -> None:
    """Print an error message."""
    print(f"{ICON_ERROR} {COLOR_RED}{msg}{COLOR_OFF}")


def print_instances_table(insts) -> None:
    """Print a formatted table of instances."""
    if not insts:
        warn("No instances found")
        return
    
    # Calculate column widths
    name_width = max(len(inst.name) for inst in insts)
    name_width = max(name_width, 8)  # Minimum width
    
    # Print header
    header = f"{'#':>3} │ {'Name':<{name_width}} │ {'Status':<15} │ Schedule"
    print(f"{COLOR_BOLD}{header}{COLOR_OFF}")
    print("─" * len(header))
    
    # Print instances
    for idx, inst in enumerate(insts, 1):
        status = inst.status()
        status_text = f"{STATUS_RUNNING} Running" if status == "running" else f"{STATUS_STOPPED} Stopped"
        schedule = inst.schedule_desc()
        row = f"{COLOR_WHITE}{idx:>3}{COLOR_OFF} │ {COLOR_BOLD}{inst.name:<{name_width}}{COLOR_OFF} │ {status_text:<15} │ {COLOR_DIM}{schedule}{COLOR_OFF}"
        print(row)


def print_menu_options(options: list[tuple[str, str]], title: str = "Actions") -> None:
    """Print a formatted menu of options."""
    print(f"\n{COLOR_BOLD}{title}:{COLOR_OFF}")
    max_key_width = max(len(key) for key, _ in options) if options else 0
    
    for key, desc in options:
        print(f"  {COLOR_CYAN}{key.ljust(max_key_width)}{COLOR_OFF} - {desc}")
    print()