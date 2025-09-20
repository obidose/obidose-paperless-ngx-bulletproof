#!/usr/bin/env python3
"""Test script to verify TTY detection works correctly in piped environment."""

import sys
import os

def test_tty_detection():
    """Test the TTY detection logic from the installer."""
    
    def _get_tty_path() -> str:
        """Best-effort path to a readable/writable TTY."""
        for key in ("TTY", "SSH_TTY", "SUDO_TTY"):
            path = os.environ.get(key)
            if path:
                return path
        for fd in (0, 1, 2):
            try:
                return os.ttyname(fd)
            except OSError:
                continue
        return "/dev/tty"
    
    print("=== TTY Detection Test ===")
    print(f"sys.stdin.isatty(): {sys.stdin.isatty()}")
    print(f"sys.stdout.isatty(): {sys.stdout.isatty()}")
    
    # Test environment variables
    for key in ("TTY", "SSH_TTY", "SUDO_TTY"):
        value = os.environ.get(key)
        print(f"${key}: {value}")
    
    # Test file descriptors
    for fd in (0, 1, 2):
        try:
            tty_name = os.ttyname(fd)
            print(f"os.ttyname({fd}): {tty_name}")
        except OSError as e:
            print(f"os.ttyname({fd}): Error - {e}")
    
    # Test TTY path function
    tty_path = _get_tty_path()
    print(f"_get_tty_path(): {tty_path}")
    
    # Test if we can actually open it
    try:
        with open(tty_path, "r+") as tty:
            print(f"Successfully opened {tty_path} for read/write")
            # Try a simple read/write test
            print("Enter 'test' to verify TTY works: ", end="", flush=True, file=tty)
            response = tty.readline().strip()
            print(f"Received: '{response}'")
            return True
    except OSError as e:
        print(f"Failed to open {tty_path}: {e}")
        return False

if __name__ == "__main__":
    success = test_tty_detection()
    print(f"\nTTY Detection: {'SUCCESS' if success else 'FAILED'}")