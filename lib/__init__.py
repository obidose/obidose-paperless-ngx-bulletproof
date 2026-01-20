"""Paperless-NGX Bulletproof library modules.

Module structure:
    - ui: Terminal colors, output functions, box drawing
    - validation: Input validation (domain, email, port, instance name)
    - instance: Instance class, InstanceManager, config loading helpers
    - health: HealthChecker for instance health monitoring
    - backup_ops: BackupManager and restore operations
    - manager: Main PaperlessManager application controller
"""
__version__ = "2.0.0"

# Export commonly used items from submodules
from lib.ui import (
    Colors, colorize, say, ok, warn, error, die,
    print_header, print_menu
)
from lib.validation import (
    get_input, confirm,
    is_valid_domain, get_domain_input,
    is_valid_email, get_email_input,
    is_valid_port, get_port_input,
    is_valid_instance_name, get_instance_name_input
)
from lib.instance import (
    Instance, InstanceManager,
    load_instance_config, load_backup_env_config
)
from lib.health import HealthChecker
from lib.backup_ops import BackupManager
