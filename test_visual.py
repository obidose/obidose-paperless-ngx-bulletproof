#!/usr/bin/env python3
"""
Quick visual test of the enhanced CLI elements
"""

import sys
import os
from pathlib import Path

# Add the tools directory to the path so we can import bulletproof
sys.path.insert(0, str(Path(__file__).parent / "tools"))

from bulletproof import (
    print_header, print_separator, print_menu_options, print_instances_table,
    Instance, COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_CYAN, COLOR_WHITE, 
    COLOR_BOLD, COLOR_OFF, ICON_INFO, ICON_SUCCESS, ICON_WARNING, ICON_ERROR,
    ICON_ARROW
)

def demo_visual_enhancements():
    """Demonstrate the enhanced visual elements."""
    
    # Demo: Enhanced header
    print_header("Paperless-ngx Bulletproof - Enhanced CLI Demo")
    print()
    
    # Demo: Info section with colors and icons
    print(f"{COLOR_BOLD}{ICON_INFO} System Information{COLOR_OFF}")
    print(f"  {COLOR_CYAN}Tool Version:{COLOR_OFF} {COLOR_WHITE}2.0.0-enhanced{COLOR_OFF}")
    print(f"  {COLOR_CYAN}Docker:{COLOR_OFF} {COLOR_GREEN}✓ Available{COLOR_OFF}")
    print(f"  {COLOR_CYAN}Rclone:{COLOR_OFF} {COLOR_YELLOW}⚠ Not configured{COLOR_OFF}")
    print()
    
    # Demo: Separator
    print_separator()
    
    # Demo: Fake instances table
    print(f"{COLOR_BOLD}{ICON_SUCCESS} Mock Instances{COLOR_OFF}")
    print("(This would show your actual instances in real usage)")
    print()
    
    fake_instances = [
        # We'll create mock Instance objects for demo
        type('MockInstance', (), {
            'name': 'production',
            'stack_dir': Path('/data/paperless-production'),
            'data_dir': Path('/data/paperless-production/data'),
            'env': {}
        })(),
        type('MockInstance', (), {
            'name': 'development', 
            'stack_dir': Path('/data/paperless-dev'),
            'data_dir': Path('/data/paperless-dev/data'),
            'env': {}
        })(),
        type('MockInstance', (), {
            'name': 'testing',
            'stack_dir': Path('/data/paperless-test'), 
            'data_dir': Path('/data/paperless-test/data'),
            'env': {}
        })()
    ]
    
    # Show the instances table header manually since print_instances_table needs actual instances
    print(f"{COLOR_CYAN}╔═══════════════════════════╗{COLOR_OFF}")
    print(f"{COLOR_CYAN}║  Paperless-ngx Instances  ║{COLOR_OFF}")
    print(f"{COLOR_CYAN}║     3 instances found     ║{COLOR_OFF}")
    print(f"{COLOR_CYAN}╚═══════════════════════════╝{COLOR_OFF}")
    print()
    print(f"  # │ NAME                 │ STATUS   │ BACKUP SCHEDULE")
    print(f"────┼──────────────────────┼──────────┼───────────────────────────────")
    print(f"  1 │ production           │ {COLOR_GREEN}● up{COLOR_OFF}   │ Full: daily 02:00, Incr: every 6h")
    print(f"  2 │ development          │ {COLOR_RED}● down{COLOR_OFF} │ Full: weekly Sun 03:00, Incr: daily 09:00")
    print(f"  3 │ testing              │ {COLOR_YELLOW}● partial{COLOR_OFF} │ Full: monthly 1 04:00, Incr: every 12h")
    print()
    
    # Demo: Enhanced menu options
    demo_options = [
        ("1", "Manage specific instance"),
        ("2", "Backup single instance"),
        ("3", "Backup all instances"),
        ("4", "Add new instance"),
        ("5", "Start all instances"),
        ("6", "Stop all instances"),
        ("7", "Delete all instances"),
        ("8", "Explore remote backups"),
        ("0", "Quit")
    ]
    
    print_menu_options(demo_options, "Multi-Instance Actions")
    print()
    
    # Demo: Status messages with icons
    print(f"{COLOR_BOLD}Status Messages Demo:{COLOR_OFF}")
    print(f"{ICON_SUCCESS} {COLOR_GREEN}Successfully started instance 'production'{COLOR_OFF}")
    print(f"{ICON_WARNING} {COLOR_YELLOW}Instance 'development' has no recent backups{COLOR_OFF}")
    print(f"{ICON_ERROR} {COLOR_RED}Failed to connect to instance 'testing'{COLOR_OFF}")
    print(f"{ICON_INFO} {COLOR_CYAN}Found 15 backup snapshots for 'production'{COLOR_OFF}")
    print()
    
    print(f"{COLOR_BOLD}The CLI now features:{COLOR_OFF}")
    print(f"  {ICON_ARROW} Enhanced color scheme with better contrast")
    print(f"  {ICON_ARROW} Beautiful boxed headers and separators")
    print(f"  {ICON_ARROW} Tabular display for instances with status indicators")
    print(f"  {ICON_ARROW} Consistent icons for different message types")
    print(f"  {ICON_ARROW} Modern menu formatting with descriptions")
    print(f"  {ICON_ARROW} Visual feedback for user choices and status")
    print()
    
    print(f"{COLOR_GREEN}✨ Aesthetic enhancement complete! ✨{COLOR_OFF}")

if __name__ == "__main__":
    demo_visual_enhancements()