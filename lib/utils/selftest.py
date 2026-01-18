#!/usr/bin/env python3
"""
Stack health checks for Paperless-ngx instances.

Provides comprehensive validation of running stacks including:
- Container status and name verification
- Database and Redis connectivity
- Django application health
- HTTP endpoint accessibility
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.error
from typing import Optional

from lib.utils.common import load_env, Colors


def _docker_compose_cmd(project_name: str, env_file: Path, compose_file: Path) -> list[str]:
    """Build the base docker compose command with project name."""
    return [
        "docker", "compose",
        "--project-name", project_name,
        "--env-file", str(env_file),
        "-f", str(compose_file),
    ]


def run_stack_tests(compose_file: Path, env_file: Path, project_name: Optional[str] = None, verbose: bool = True) -> bool:
    """Run comprehensive health checks against the Paperless stack.

    If project_name is not provided, tries to read INSTANCE_NAME from env_file.
    Returns True if all checks pass, False otherwise.
    
    Checks performed:
    1. Container status - all expected containers running
    2. Container name verification - correct project is running
    3. Django check - application is healthy
    4. Database connectivity - PostgreSQL responding
    5. Redis connectivity - broker is responding  
    6. HTTP endpoint - web interface accessible
    """
    env = load_env(env_file)
    instance = env.get("INSTANCE_NAME", "paperless")
    http_port = env.get("HTTP_PORT", "8000")
    
    if project_name is None:
        project_name = f"paperless-{instance}"
    
    base_cmd = _docker_compose_cmd(project_name, env_file, compose_file)
    all_passed = True
    warnings = []
    
    def log(msg: str, success: bool = True) -> None:
        if verbose:
            symbol = "✓" if success else "✗"
            color = Colors.GREEN if success else Colors.RED
            print(f"  {color}{symbol}{Colors.OFF} {msg}")
    
    if verbose:
        print(f"\n  Running health checks for {project_name}...")
    
    # Check 1: Verify containers are running and match expected project
    try:
        result = subprocess.run(
            base_cmd + ["ps", "--format", "{{.Name}} {{.State}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        running_containers = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                running_containers[parts[0]] = parts[1]
        
        # Verify the containers match the expected project name
        expected_prefix = f"{project_name}-"
        for container, state in running_containers.items():
            if not container.startswith(expected_prefix):
                warnings.append(f"Container mismatch: {container} doesn't match project {project_name}")
                all_passed = False
            elif state != "running":
                warnings.append(f"Container {container} is {state}, not running")
                all_passed = False
        
        # Verify required containers exist
        # Service names in compose are: paperless, db, redis, gotenberg, tika
        paperless_container = f"{project_name}-paperless-1"
        db_container = f"{project_name}-db-1"
        broker_container = f"{project_name}-redis-1"
        
        if paperless_container not in running_containers:
            warnings.append(f"Missing paperless container: {paperless_container}")
            all_passed = False
        if db_container not in running_containers:
            warnings.append(f"Missing database container: {db_container}")
            all_passed = False
        if broker_container not in running_containers:
            warnings.append(f"Missing redis container: {broker_container}")
            all_passed = False
            
        log("Containers running", all_passed)
            
    except subprocess.CalledProcessError as e:
        warnings.append(f"Failed to list containers: {e}")
        all_passed = False
        log("Containers running", False)
    except Exception as e:
        warnings.append(f"Container check error: {e}")
        all_passed = False
        log("Containers running", False)
    
    # Check 2: Django application check
    django_ok = True
    try:
        subprocess.run(
            base_cmd + ["exec", "-T", "paperless", "python", "manage.py", "check"],
            check=True,
            capture_output=True,
        )
        log("Django check passed", True)
    except subprocess.CalledProcessError:
        warnings.append("Django check failed")
        django_ok = False
        all_passed = False
        log("Django check passed", False)
    except Exception as e:
        warnings.append(f"Django check error: {e}")
        django_ok = False
        all_passed = False
        log("Django check passed", False)
    
    # Check 3: Database connectivity
    db_ok = True
    try:
        result = subprocess.run(
            base_cmd + ["exec", "-T", "db", "pg_isready", "-U", "paperless"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            warnings.append("PostgreSQL not accepting connections")
            db_ok = False
            all_passed = False
        log("Database connectivity", db_ok)
    except subprocess.TimeoutExpired:
        warnings.append("Database check timed out")
        db_ok = False
        all_passed = False
        log("Database connectivity", False)
    except Exception as e:
        warnings.append(f"Database check error: {e}")
        db_ok = False
        all_passed = False
        log("Database connectivity", False)
    
    # Check 4: Redis connectivity
    redis_ok = True
    try:
        result = subprocess.run(
            base_cmd + ["exec", "-T", "redis", "redis-cli", "ping"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "PONG" not in result.stdout:
            warnings.append("Redis not responding to ping")
            redis_ok = False
            all_passed = False
        log("Redis connectivity", redis_ok)
    except subprocess.TimeoutExpired:
        warnings.append("Redis check timed out")
        redis_ok = False
        all_passed = False
        log("Redis connectivity", False)
    except Exception as e:
        warnings.append(f"Redis check error: {e}")
        redis_ok = False
        all_passed = False
        log("Redis connectivity", False)
    
    # Check 5: HTTP endpoint (with retry for slow startup)
    http_ok = False
    max_retries = 12  # 12 retries * 5 seconds = 60 seconds max wait
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            url = f"http://localhost:{http_port}/"
            req = urllib.request.Request(url, method='HEAD')
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status < 500:
                    http_ok = True
                    break
        except urllib.error.HTTPError as e:
            # 401/403 is fine - means the app is running but needs auth
            if e.code < 500:
                http_ok = True
                break
        except (urllib.error.URLError, ConnectionResetError, ConnectionRefusedError):
            # Connection not ready yet, retry
            pass
        except Exception:
            pass
        
        # Only sleep and retry if not the last attempt
        if attempt < max_retries - 1:
            if verbose and attempt == 0:
                print(f"  ... waiting for HTTP endpoint (up to 60s)")
            time.sleep(retry_delay)
    
    if http_ok:
        log("HTTP endpoint responding", True)
    else:
        warnings.append("HTTP endpoint not responding after 60 seconds")
        all_passed = False
        log("HTTP endpoint responding", False)
    
    # Print warnings if verbose
    if verbose and warnings:
        print()
        for w in warnings:
            print(f"  {Colors.YELLOW}[!]{Colors.OFF} {w}")
    
    return all_passed


def quick_container_check(compose_file: Path, env_file: Path, project_name: Optional[str] = None) -> bool:
    """Quick check that containers are running (no app-level checks).
    
    Useful for fast validation without waiting for HTTP endpoints.
    """
    env = load_env(env_file)
    instance = env.get("INSTANCE_NAME", "paperless")
    
    if project_name is None:
        project_name = f"paperless-{instance}"
    
    try:
        result = subprocess.run(
            _docker_compose_cmd(project_name, env_file, compose_file) + 
            ["ps", "--format", "{{.State}}", "--filter", "status=running"],
            capture_output=True,
            text=True,
            check=True,
        )
        # Should have at least 3 running containers (paperless, db, broker)
        running_count = len([s for s in result.stdout.strip().splitlines() if s == "running"])
        return running_count >= 3
    except Exception:
        return False
