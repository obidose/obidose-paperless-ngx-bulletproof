#!/usr/bin/env python3
"""
Health checking for Paperless-NGX Bulletproof.

Provides comprehensive health checks for Paperless-NGX instances.
This module consolidates all health check logic to avoid duplication.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from lib.ui import Colors, colorize, say, ok, warn
from lib.instance import Instance
from lib.utils.selftest import run_stack_tests
from lib.utils.common import load_env

if TYPE_CHECKING:
    pass


class HealthChecker:
    """Performs comprehensive health checks on a Paperless-NGX instance.
    
    This class wraps the proven health check logic from selftest.py
    to provide a consistent API for the TUI.
    """
    
    def __init__(self, instance: Instance):
        self.instance = instance
        self.compose_file = instance.stack_dir / "docker-compose.yml"
        self.env_file = instance.stack_dir / ".env"

    def _docker_compose_cmd(self) -> list[str]:
        """Get the docker compose command for this instance."""
        if not self.compose_file.exists():
            return []
        project_name = f"paperless-{self.instance.name}"
        return [
            "docker", "compose",
            "--project-name", project_name,
            "--env-file", str(self.env_file),
            "-f", str(self.compose_file)
        ]

    def check_all(self) -> dict[str, bool]:
        """Run all health checks and return results.
        
        Uses the consolidated health check logic from selftest.py
        for consistency across the application.
        """
        results = {
            "instance_exists": self.check_instance_exists(),
            "docker_running": self.check_docker(),
            "compose_file": self.check_compose_file(),
            "env_file": self.check_env_file(),
            "data_dirs": self.check_data_dirs(),
            "containers_running": False,  # Will be set by detailed check
            "container_names": False,     # Will be set by detailed check
            "database": False,            # Will be set by detailed check
            "redis": False,               # Will be set by detailed check
            "django": False,              # Will be set by detailed check
            "http_endpoint": False,       # Will be set by detailed check
            "rclone_installed": self.check_rclone(),
            "backup_remote": self.check_backup_remote(),
        }
        
        # Run the comprehensive stack tests (reuses proven logic)
        if results["compose_file"] and results["env_file"]:
            project_name = f"paperless-{self.instance.name}"
            try:
                # Run detailed checks using selftest.py logic
                detailed_passed = run_stack_tests(
                    self.compose_file,
                    self.env_file,
                    project_name,
                    verbose=False  # We'll display results ourselves
                )
                
                # Update specific check results
                # Note: run_stack_tests does all container/service checks internally
                # We set these to True if overall test passed, but could parse detailed results
                if detailed_passed:
                    results.update({
                        "containers_running": True,
                        "container_names": True,
                        "database": True,
                        "redis": True,
                        "django": True,
                        "http_endpoint": True,
                    })
                else:
                    # Run individual checks to get granular results
                    results["containers_running"] = self.check_containers()
                    results["container_names"] = self.check_container_names()
                    results["database"] = self.check_database()
                    results["redis"] = self.check_redis()
                    results["django"] = self.check_django()
                    results["http_endpoint"] = self.check_http_endpoint()
            except Exception:
                # Fallback to individual checks on error
                results["containers_running"] = self.check_containers()
                results["container_names"] = self.check_container_names()
                results["database"] = self.check_database()
                results["redis"] = self.check_redis()
                results["django"] = self.check_django()
                results["http_endpoint"] = self.check_http_endpoint()
        
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
        """Check if all required containers are running.
        
        Checks that paperless, db, and redis containers are running.
        Uses docker compose ps to verify container state.
        """
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        try:
            result = subprocess.run(
                cmd + ["ps", "--format", "{{.Name}} {{.State}}"],
                capture_output=True, text=True, check=True, timeout=10
            )
            
            # Parse running containers
            running_containers = {}
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    running_containers[parts[0]] = parts[1]
            
            # Check for required containers (service names match compose files)
            # Service names: paperless, db, redis, gotenberg, tika
            required = ["paperless", "db", "redis"]
            project_name = f"paperless-{self.instance.name}"
            
            for req in required:
                expected_name = f"{project_name}-{req}-1"
                if expected_name not in running_containers:
                    return False
                if running_containers[expected_name] != "running":
                    return False
            
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def check_container_names(self) -> bool:
        """Verify container names match the expected pattern for this instance.
        
        Ensures all containers have the correct project name prefix.
        """
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        try:
            result = subprocess.run(
                cmd + ["ps", "--format", "{{.Name}}"],
                capture_output=True, text=True, check=True, timeout=10
            )
            
            container_names = [n for n in result.stdout.strip().split('\n') if n]
            expected_prefix = f"paperless-{self.instance.name}-"
            
            return all(name.startswith(expected_prefix) for name in container_names)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def check_database(self) -> bool:
        """Check if PostgreSQL is responding.
        
        Uses pg_isready to verify database is accepting connections.
        """
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        try:
            result = subprocess.run(
                cmd + ["exec", "-T", "db", "pg_isready", "-U", "paperless"],
                capture_output=True, check=False, timeout=10
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def check_redis(self) -> bool:
        """Check if Redis is responding.
        
        Uses redis-cli ping to verify Redis broker is accessible.
        """
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        try:
            result = subprocess.run(
                cmd + ["exec", "-T", "redis", "redis-cli", "ping"],
                capture_output=True, text=True, check=False, timeout=10
            )
            return result.returncode == 0 and "PONG" in result.stdout
        except subprocess.TimeoutExpired:
            return False

    def check_django(self) -> bool:
        """Check if Django app is responding.
        
        Runs Django's built-in system check framework to verify app health.
        """
        cmd = self._docker_compose_cmd()
        if not cmd:
            return False
        
        try:
            result = subprocess.run(
                cmd + ["exec", "-T", "paperless", "python", "manage.py", "check"],
                capture_output=True, check=False, timeout=10
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def check_http_endpoint(self, retry: bool = False) -> bool:
        """Check if HTTP endpoint is responding.
        
        Tests the web interface on the configured HTTP port.
        Accepts 200, 401, 403 as valid responses (app is running).
        """
        try:
            port = self.instance.get_env_value("HTTP_PORT", "8000")
        except:
            port = "8000"
            
        # Use the proven logic from selftest.py with urllib
        import urllib.request
        import urllib.error
        import time
        
        url = f"http://localhost:{port}/"
        max_attempts = 3 if retry else 1
        
        for attempt in range(max_attempts):
            try:
                req = urllib.request.Request(url, method='HEAD')
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status < 500:
                        return True
            except urllib.error.HTTPError as e:
                # 401/403 is fine - app is running but needs auth
                if e.code < 500:
                    return True
            except (urllib.error.URLError, ConnectionResetError, ConnectionRefusedError):
                # Not ready yet
                if attempt < max_attempts - 1:
                    time.sleep(2)
                continue
            except Exception:
                pass
        
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
