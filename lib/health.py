#!/usr/bin/env python3
"""
Health checking for Paperless-NGX Bulletproof.

Provides comprehensive health checks for Paperless-NGX instances.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import urlopen
from urllib.error import URLError

from lib.ui import Colors, colorize, say, ok, warn, error
from lib.instance import Instance

if TYPE_CHECKING:
    pass


class HealthChecker:
    """Performs comprehensive health checks on a Paperless-NGX instance."""
    
    def __init__(self, instance: Instance):
        self.instance = instance

    def _docker_compose_cmd(self) -> list[str]:
        """Get the docker compose command for this instance."""
        compose_file = self.instance.stack_dir / "docker-compose.yml"
        if not compose_file.exists():
            return []
        return ["docker", "compose", "-f", str(compose_file)]

    def check_all(self) -> dict[str, bool]:
        """Run all health checks and return results."""
        results = {
            "instance_exists": self.check_instance_exists(),
            "docker_running": self.check_docker(),
            "compose_file": self.check_compose_file(),
            "env_file": self.check_env_file(),
            "data_dirs": self.check_data_dirs(),
            "containers_running": self.check_containers(),
            "container_names": self.check_container_names(),
            "database": self.check_database(),
            "redis": self.check_redis(),
            "django": self.check_django(),
            "http_endpoint": self.check_http_endpoint(),
            "rclone_installed": self.check_rclone(),
            "backup_remote": self.check_backup_remote(),
        }
        return results

    def check_instance_exists(self) -> bool:
        """Check if instance directories exist."""
        return self.instance.stack_dir.exists() and self.instance.data_root.exists()

    def check_docker(self) -> bool:
        """Check if Docker daemon is running."""
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, check=False
        )
        return result.returncode == 0

    def check_compose_file(self) -> bool:
        """Check if docker-compose.yml exists."""
        return (self.instance.stack_dir / "docker-compose.yml").exists()

    def check_env_file(self) -> bool:
        """Check if .env file exists."""
        return (self.instance.stack_dir / ".env").exists()

    def check_data_dirs(self) -> bool:
        """Check if all required data directories exist."""
        required = ["data", "media", "consume", "export", "db"]
        return all((self.instance.data_root / d).exists() for d in required)

    def check_containers(self) -> bool:
        """Check if all required containers are running."""
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        result = subprocess.run(
            cmd + ["ps", "--format", "json"],
            capture_output=True, text=True, check=False
        )
        
        if result.returncode != 0:
            return False
        
        # Check for required containers
        output = result.stdout.lower()
        required = ["webserver", "broker", "db"]
        return all(r in output for r in required)

    def check_container_names(self) -> bool:
        """Verify container names match the expected pattern for this instance."""
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        result = subprocess.run(
            cmd + ["ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, check=False
        )
        
        if result.returncode != 0:
            return False
        
        container_names = result.stdout.strip().split('\n')
        expected_prefix = self.instance.name
        return all(expected_prefix in name for name in container_names if name)

    def check_database(self) -> bool:
        """Check if PostgreSQL is responding."""
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        result = subprocess.run(
            cmd + ["exec", "-T", "db", "pg_isready"],
            capture_output=True, check=False
        )
        return result.returncode == 0

    def check_redis(self) -> bool:
        """Check if Redis is responding."""
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        result = subprocess.run(
            cmd + ["exec", "-T", "broker", "redis-cli", "ping"],
            capture_output=True, text=True, check=False
        )
        return result.returncode == 0 and "PONG" in result.stdout

    def check_django(self) -> bool:
        """Check if Django app is responding."""
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        result = subprocess.run(
            cmd + ["exec", "-T", "webserver", "python3", "manage.py", "check"],
            capture_output=True, check=False
        )
        return result.returncode == 0

    def check_http_endpoint(self, retry: bool = False) -> bool:
        """Check if HTTP endpoint is responding."""
        port = self.instance.get_env_value("HTTP_PORT", "8000")
        url = f"http://localhost:{port}/api/"
        
        max_attempts = 3 if retry else 1
        for attempt in range(max_attempts):
            try:
                with urlopen(url, timeout=5) as response:
                    return response.status in (200, 401, 403)
            except URLError:
                if attempt < max_attempts - 1:
                    time.sleep(2)
                continue
            except Exception:
                continue
        
        # Try alternate check via container
        cmd = self._docker_compose_cmd()
        if cmd:
            result = subprocess.run(
                cmd + ["exec", "-T", "webserver", "curl", "-sf", "http://localhost:8000/api/"],
                capture_output=True, check=False
            )
            if result.returncode == 0:
                return True
        
        return False

    def check_rclone(self) -> bool:
        """Check if rclone is installed."""
        result = subprocess.run(
            ["which", "rclone"],
            capture_output=True, check=False
        )
        return result.returncode == 0

    def check_backup_remote(self) -> bool:
        """Check if backup remote is configured and accessible."""
        remote_name = self.instance.get_env_value("RCLONE_REMOTE_NAME", "pcloud")
        result = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True, text=True, check=False
        )
        return result.returncode == 0 and f"{remote_name}:" in result.stdout

    def print_report(self) -> None:
        """Print a formatted health report."""
        results = self.check_all()
        
        print()
        say(f"Health Report for {colorize(self.instance.name, Colors.BOLD)}")
        print(colorize("─" * 50, Colors.CYAN))
        
        checks = [
            ("Instance Exists", "instance_exists"),
            ("Docker Running", "docker_running"),
            ("Compose File", "compose_file"),
            ("Environment File", "env_file"),
            ("Data Directories", "data_dirs"),
            ("Containers Running", "containers_running"),
            ("Container Names", "container_names"),
            ("PostgreSQL", "database"),
            ("Redis", "redis"),
            ("Django App", "django"),
            ("HTTP Endpoint", "http_endpoint"),
            ("Rclone Installed", "rclone_installed"),
            ("Backup Remote", "backup_remote"),
        ]
        
        for label, key in checks:
            if results.get(key):
                print(f"  {colorize('✓', Colors.GREEN)} {label}")
            else:
                print(f"  {colorize('✗', Colors.RED)} {label}")
        
        print()
        
        # Summary
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        
        if passed == total:
            ok(f"All {total} checks passed!")
        else:
            warn(f"{passed}/{total} checks passed")
