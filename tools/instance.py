"""
Instance management for the Paperless-ngx bulletproof tool.

This module handles instance discovery, lifecycle management, and operations
like starting, stopping, deleting, and renaming instances.
"""

import os
import subprocess
import shutil
import socket
from pathlib import Path
from ui import say, ok, warn, error, _read, print_instances_table


def find_available_port(start_port: int = 8000, max_attempts: int = 100) -> int:
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(('', port))
                return port
        except OSError:
            continue
    return start_port  # Fallback to start_port if none found


def load_env(path: Path) -> None:
    """Load environment variables from a .env file."""
    if path.exists():
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    os.environ[key] = value


def _cron_desc(expr: str) -> str:
    """Convert cron expression to human-readable description."""
    if not expr or expr.strip() == "":
        return "disabled"
    
    try:
        parts = expr.strip().split()
        if len(parts) != 5:
            return "invalid"
        
        minute, hour, day, month, weekday = parts
        
        # Specific common patterns first
        if expr == "0 0 * * *":
            return "daily at midnight"
        elif expr == "0 3 * * 0":
            return "weekly on Sunday at 3 AM"
        elif expr == "30 3 * * 0":
            return "weekly on Sunday at 3:30 AM"
        elif expr == "0 0 1 * *":
            return "monthly on 1st at midnight"
        elif expr == "30 3 1 * *":
            return "monthly on 1st at 3:30 AM"
        
        # Weekly patterns (weekday specified)
        elif weekday != "*":
            weekdays = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
            if weekday.isdigit() and 0 <= int(weekday) <= 6:
                day_name = weekdays[int(weekday)]
                if hour.isdigit() and minute.isdigit():
                    time_str = f"{hour}:{minute.zfill(2)}"
                    return f"weekly on {day_name} at {time_str}"
                else:
                    return f"weekly on {day_name}"
            else:
                return f"weekly on day {weekday}"
        
        # Monthly patterns (day of month specified)
        elif day != "*":
            if day.isdigit():
                day_num = int(day)
                if 1 <= day_num <= 31:
                    if hour.isdigit() and minute.isdigit():
                        time_str = f"{hour}:{minute.zfill(2)}"
                        suffix = "st" if day_num == 1 or day_num == 21 or day_num == 31 else \
                                "nd" if day_num == 2 or day_num == 22 else \
                                "rd" if day_num == 3 or day_num == 23 else "th"
                        return f"monthly on {day_num}{suffix} at {time_str}"
                    else:
                        return f"monthly on {day_num}"
            return f"monthly on day {day}"
        
        # Daily patterns (hour and minute specified, no specific day/weekday)
        elif hour != "*" and minute != "*":
            if hour.isdigit() and minute.isdigit():
                time_str = f"{hour}:{minute.zfill(2)}"
                return f"daily at {time_str}"
            else:
                return "daily (complex schedule)"
        
        # Hourly patterns
        elif minute != "*":
            if minute.isdigit():
                return f"hourly at :{minute.zfill(2)}"
            else:
                return "hourly (complex schedule)"
        
        # Default fallback - show the raw expression
        else:
            return f"custom: {expr}"
            
    except Exception:
        return "invalid"


class Instance:
    """Represents a Paperless-ngx instance."""
    
    def __init__(self, name: str, stack_dir: Path, data_dir: Path, env: dict[str, str]):
        self.name = name
        self.stack_dir = stack_dir
        self.data_dir = data_dir
        self.env = env
        self.env_file = stack_dir / ".env"
        self.compose_file = stack_dir / "docker-compose.yml"
        self.backup_script = stack_dir / "backup.py"
    
    def status(self) -> str:
        """Get the running status of the instance."""
        try:
            res = subprocess.run(
                ["docker", "compose", "ps", "-q"],
                cwd=self.stack_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                # Check if containers are actually running
                containers = res.stdout.strip().split('\n')
                for container in containers:
                    if container.strip():
                        status_res = subprocess.run(
                            ["docker", "inspect", "-f", "{{.State.Running}}", container.strip()],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if status_res.returncode == 0 and status_res.stdout.strip() == "true":
                            return "running"
                return "stopped"
            else:
                return "stopped"
        except Exception:
            return "unknown"
    
    def schedule_desc(self) -> str:
        """Get human-readable backup schedule description."""
        full_time = self.env.get("CRON_FULL_TIME", "")
        incr_time = self.env.get("CRON_INCR_TIME", "")
        archive_time = self.env.get("CRON_ARCHIVE_TIME", "")
        
        schedules = []
        if full_time and full_time.strip():
            schedules.append(f"Full: {_cron_desc(full_time)}")
        if incr_time and incr_time.strip():
            schedules.append(f"Incr: {_cron_desc(incr_time)}")
        if archive_time and archive_time.strip():
            schedules.append(f"Arch: {_cron_desc(archive_time)}")
        
        if not schedules:
            return "No backups configured"
        elif len(schedules) == 1:
            return schedules[0]
        else:
            return " | ".join(schedules)
    
    def env_for_subprocess(self) -> dict[str, str]:
        """Get environment variables for subprocess execution."""
        env = os.environ.copy()
        env.update(self.env)
        return env


def parse_env(path: Path) -> dict[str, str]:
    """Parse environment file and return as dictionary."""
    env = {}
    if path.exists():
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def find_instances() -> list[Instance]:
    """Find all Paperless-ngx instances on the system."""
    instances = []
    docker_dir = Path("/home/docker")
    if docker_dir.exists():
        for item in docker_dir.iterdir():
            if item.is_dir() and item.name.endswith("-setup"):
                env_file = item / ".env"
                if env_file.exists():
                    env = parse_env(env_file)
                    instance_name = env.get("INSTANCE_NAME", item.name[:-6])  # Remove -setup suffix
                    data_dir = Path(env.get("DATA_ROOT", f"/home/docker/{instance_name}"))
                    instances.append(Instance(instance_name, item, data_dir, env))
    return instances


def cleanup_orphans() -> None:
    """Clean up orphaned instance directories."""
    docker_dir = Path("/home/docker")
    if not docker_dir.exists():
        return
    
    orphaned_stacks = []
    orphaned_data = []
    
    for item in docker_dir.iterdir():
        if item.is_dir():
            if item.name.endswith("-setup"):
                env_file = item / ".env"
                if not env_file.exists():
                    orphaned_stacks.append(item)
            else:
                # Check if this is a data directory without corresponding stack
                stack_dir = docker_dir / f"{item.name}-setup"
                if not stack_dir.exists():
                    orphaned_data.append(item)
    
    if orphaned_stacks or orphaned_data:
        say("Found orphaned directories:")
        for stack in orphaned_stacks:
            warn(f"Orphaned stack: {stack}")
        for data in orphaned_data:
            warn(f"Orphaned data: {data}")
        
        if _read("Remove orphaned directories? [y/N]: ").strip().lower().startswith('y'):
            for stack in orphaned_stacks:
                try:
                    subprocess.run(["rm", "-rf", str(stack)], check=False)
                    ok(f"Removed {stack}")
                except Exception as e:
                    warn(f"Failed to remove {stack}: {e}")
            
            for data in orphaned_data:
                try:
                    subprocess.run(["rm", "-rf", str(data)], check=False)
                    ok(f"Removed {data}")
                except Exception as e:
                    warn(f"Failed to remove {data}: {e}")


def install_instance(name: str) -> None:
    """Install and start a new instance."""
    say(f"Installing instance: {name}")
    
    # Check if instance exists
    instances = find_instances()
    instance = next((inst for inst in instances if inst.name == name), None)
    
    if not instance:
        error(f"Instance '{name}' not found")
        return
    
    if not instance.compose_file.exists():
        error(f"Docker compose file not found: {instance.compose_file}")
        return
    
    # Change to stack directory and run docker compose
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=instance.stack_dir,
            env=instance.env_for_subprocess(),
            check=True
        )
        ok(f"Instance '{name}' started successfully")
    except subprocess.CalledProcessError as e:
        error(f"Failed to start instance '{name}': {e}")
    except Exception as e:
        error(f"Error installing instance '{name}': {e}")


def _create_instance_structure(name: str, data_root: str, stack_dir: str, restore_mode: bool = False, config: dict | None = None) -> bool:
    """
    Unified function to create instance structure for both restore and new instances.
    
    Args:
        name: Instance name
        data_root: Data directory path
        stack_dir: Stack directory path
        restore_mode: If True, only create directories. If False, create full instance structure.
        config: Configuration dictionary for new instances (ignored in restore mode)
    
    Returns:
        bool: True if successful, False otherwise
    """
    data_path = Path(data_root)
    stack_path = Path(stack_dir)
    
    try:
        # Create directories (both restore and new instances need this)
        data_path.mkdir(parents=True, exist_ok=True)
        stack_path.mkdir(parents=True, exist_ok=True)
        say(f"Created directories: {data_path}, {stack_path}")
        
        if restore_mode:
            # For restore mode, only create directories
            return True
        
        # For new instances, create full structure
        if config is None:
            warn("Configuration required for new instance creation")
            return False
        
        remote_name = os.environ.get("RCLONE_REMOTE_NAME", "pcloud")
        
        # Determine port configuration
        if config.get('use_https'):
            # For HTTPS instances, no direct port exposure needed
            port_config = "# Port managed by Traefik"
            paperless_url = f"https://{config['domain']}"
        else:
            # For direct instances, find available port
            http_port = find_available_port(8000)
            port_config = f"HTTP_PORT={http_port}"
            paperless_url = f"http://localhost:{http_port}"
        
        # Generate .env file
        env_content = f"""# Paperless-ngx Configuration for {name}
INSTANCE_NAME={name}
DATA_ROOT={data_root}
STACK_DIR={stack_dir}

# Paperless Configuration
PAPERLESS_TIME_ZONE={config['timezone']}
PAPERLESS_ADMIN_USER={config['admin_user']}
PAPERLESS_ADMIN_PASSWORD={config['admin_password']}
PAPERLESS_URL={paperless_url}

# Database Configuration
POSTGRES_VERSION=15
POSTGRES_DB=paperless
POSTGRES_USER=paperless
POSTGRES_PASSWORD={config['db_password']}

# Directory Configuration
DIR_DB={data_root}/pgdata
DIR_DATA={data_root}/data
DIR_MEDIA={data_root}/media
DIR_EXPORT={data_root}/export
DIR_CONSUME={data_root}/consume
DIR_TIKA_CACHE={data_root}/tika-cache

# Port Configuration
{port_config}

# User Configuration
PUID=1000
PGID=1000
TZ={config['timezone']}

# Backup Configuration
RCLONE_REMOTE_NAME={remote_name}
RCLONE_REMOTE_PATH=backups/paperless/{name}
REMOTE={remote_name}:backups/paperless/{name}

# Backup Schedule
CRON_FULL_TIME={config.get('full_schedule', '30 3 * * 0')}
CRON_INCR_TIME={config.get('incr_schedule', '0 0 * * *')}
CRON_ARCHIVE_TIME={config.get('archive_schedule', '')}
"""
        
        if config.get('use_https'):
            env_content += f"""DOMAIN={config['domain']}
EMAIL={config['email']}
TRAEFIK_ENABLED=yes
"""
        
        env_file = stack_path / ".env"
        env_file.write_text(env_content)
        say(f"Created configuration file: {env_file}")
        
        # Generate docker-compose.yml based on configuration
        compose_template = "traefik" if config.get('use_https') else "direct"
        compose_source = Path(__file__).parent.parent / "compose" / f"docker-compose-{compose_template}.yml"
        compose_dest = stack_path / "docker-compose.yml"
        
        if compose_source.exists():
            # Copy the appropriate compose file
            shutil.copy2(compose_source, compose_dest)
            say(f"Created docker-compose.yml from {compose_template} template")
        else:
            # Fallback: create a comprehensive compose file if templates don't exist
            basic_compose = f"""services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]

  db:
    image: postgres:15-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: paperless
      POSTGRES_USER: paperless
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
    volumes:
      - {data_root}/pgdata:/var/lib/postgresql/data

  gotenberg:
    image: gotenberg/gotenberg:8
    restart: unless-stopped
    command: ["gotenberg", "--chromium-disable-javascript=true"]

  tika:
    image: ghcr.io/paperless-ngx/tika:latest
    restart: unless-stopped
    volumes:
      - {data_root}/tika-cache:/cache

  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    restart: unless-stopped
    depends_on:
      - db
      - redis
      - gotenberg
      - tika
    environment:
      PUID: 1000
      PGID: 1000
      TZ: ${{PAPERLESS_TIME_ZONE}}
      PAPERLESS_REDIS: redis://redis:6379
      PAPERLESS_DBHOST: db
      PAPERLESS_DBPORT: 5432
      PAPERLESS_DBNAME: paperless
      PAPERLESS_DBUSER: paperless
      PAPERLESS_DBPASS: ${{POSTGRES_PASSWORD}}
      PAPERLESS_ADMIN_USER: ${{PAPERLESS_ADMIN_USER}}
      PAPERLESS_ADMIN_PASSWORD: ${{PAPERLESS_ADMIN_PASSWORD}}
      PAPERLESS_TIME_ZONE: ${{PAPERLESS_TIME_ZONE}}
      PAPERLESS_URL: ${{PAPERLESS_URL}}
      PAPERLESS_TIKA_ENABLED: "1"
      PAPERLESS_TIKA_GOTENBERG_ENDPOINT: http://gotenberg:3000
      PAPERLESS_TIKA_ENDPOINT: http://tika:9998
      PAPERLESS_CONSUMER_POLLING: "10"
    volumes:
      - {data_root}/data:/usr/src/paperless/data
      - {data_root}/media:/usr/src/paperless/media
      - {data_root}/export:/usr/src/paperless/export
      - {data_root}/consume:/usr/src/paperless/consume"""
            
            # Add port configuration only for direct instances
            if not config.get('use_https'):
                basic_compose += f"""
    ports:
      - "${{HTTP_PORT}}:8000"
"""
            
            compose_dest.write_text(basic_compose)
            say("Created comprehensive docker-compose.yml with gotenberg and tika")
        
        # Copy backup script
        backup_script_source = Path(__file__).parent.parent / "modules" / "backup.py"
        backup_script_dest = stack_path / "backup.py"
        
        if backup_script_source.exists():
            shutil.copy2(backup_script_source, backup_script_dest)
            backup_script_dest.chmod(0o755)  # Make executable
            say("Installed backup script")
        
        return True
    
    except Exception as e:
        warn(f"Failed to create instance structure: {e}")
        return False


def backup_instance(inst: Instance, mode: str) -> None:
    """Run backup for an instance."""
    script = inst.backup_script
    if not script.exists():
        error(f"Backup script not found: {script}")
        return
    subprocess.run([str(script), mode], env=inst.env_for_subprocess(), check=False)


def manage_instance(inst: Instance) -> None:
    """Open instance management interface."""
    subprocess.run([str(Path(__file__)), "--instance", inst.name])


def delete_instance(inst: Instance) -> None:
    """Delete an instance and its data."""
    warn(f"This will permanently delete instance '{inst.name}' and all its data!")
    confirm = _read("Type 'DELETE' to confirm: ").strip()
    
    if confirm == "DELETE":
        try:
            # Stop the instance first
            subprocess.run(
                ["docker", "compose", "down", "-v"],
                cwd=inst.stack_dir,
                env=inst.env_for_subprocess(),
                check=False
            )
            
            # Remove any associated networks
            net_name = f"{inst.name}_default"
            subprocess.run(["docker", "network", "rm", net_name], check=False)
            
            # Remove directories
            subprocess.run(["rm", "-rf", str(inst.stack_dir)], check=False)
            subprocess.run(["rm", "-rf", str(inst.data_dir)], check=False)
            
            ok(f"Instance '{inst.name}' deleted successfully")
        except Exception as e:
            error(f"Failed to delete instance: {e}")
    else:
        say("Deletion cancelled")


def down_instance(inst: Instance) -> None:
    """Stop an instance."""
    subprocess.run(
        ["docker", "compose", "down"],
        cwd=inst.stack_dir,
        env=inst.env_for_subprocess(),
        check=False
    )


def up_instance(inst: Instance) -> None:
    """Start an instance."""
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=inst.stack_dir,
        env=inst.env_for_subprocess(),
        check=False
    )


def start_all(insts: list[Instance]) -> None:
    """Start all instances."""
    for inst in insts:
        up_instance(inst)


def stop_all(insts: list[Instance]) -> None:
    """Stop all instances."""
    for inst in insts:
        down_instance(inst)


def delete_all(insts: list[Instance]) -> None:
    """Delete all instances."""
    warn("This will permanently delete ALL instances and their data!")
    confirm = _read("Type 'DELETE ALL' to confirm: ").strip()
    
    if confirm == "DELETE ALL":
        for inst in insts:
            delete_instance(inst)
    else:
        say("Deletion cancelled")


def rename_instance(inst: Instance, new: str) -> None:
    """Rename an instance."""
    old_name = inst.name
    
    # Check if new name already exists
    instances = find_instances()
    if any(i.name == new for i in instances):
        error(f"Instance '{new}' already exists")
        return
    
    try:
        # Stop the instance
        down_instance(inst)
        
        # Update environment file
        env_lines = []
        if inst.env_file.exists():
            env_lines = inst.env_file.read_text().splitlines()
        
        for i, line in enumerate(env_lines):
            if line.startswith("INSTANCE_NAME="):
                env_lines[i] = f"INSTANCE_NAME={new}"
                break
        
        inst.env_file.write_text("\n".join(env_lines) + "\n")
        
        # Rename directories
        new_stack_dir = inst.stack_dir.parent / f"{new}-setup"
        new_data_dir = inst.data_dir.parent / new
        
        inst.stack_dir.rename(new_stack_dir)
        inst.data_dir.rename(new_data_dir)
        
        ok(f"Instance renamed from '{old_name}' to '{new}'")
        
    except Exception as e:
        error(f"Failed to rename instance: {e}")


def restore_instance(inst: Instance, snap: str | None = None, source: str | None = None) -> None:
    """Restore an instance from backup."""
    # Implementation would go here - placeholder for now
    warn("Restore functionality not yet implemented in this module")
    say("Please use the main bulletproof CLI for restore operations")