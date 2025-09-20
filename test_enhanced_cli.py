#!/usr/bin/env python3
"""Test script to verify the enhanced bulletproof CLI functionality."""

import sys
import subprocess
from pathlib import Path

def test_bulletproof_help():
    """Test that bulletproof shows enhanced help with new commands."""
    try:
        result = subprocess.run([
            sys.executable, 
            "/workspaces/obidose-paperless-ngx-bulletproof/tools/bulletproof.py", 
            "--help"
        ], capture_output=True, text=True, check=True)
        
        print("✓ Bulletproof CLI Help Output:")
        print("=" * 50)
        print(result.stdout)
        print("=" * 50)
        
        # Check for new commands
        expected_commands = ["setup-pcloud", "create"]
        found_commands = []
        
        for cmd in expected_commands:
            if cmd in result.stdout:
                found_commands.append(cmd)
                print(f"✓ Found command: {cmd}")
            else:
                print(f"✗ Missing command: {cmd}")
        
        return len(found_commands) == len(expected_commands)
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Error running bulletproof --help: {e}")
        print(f"stderr: {e.stderr}")
        return False

def test_simple_installer():
    """Test that the simple installer script is valid Python."""
    installer_path = Path("/workspaces/obidose-paperless-ngx-bulletproof/install_simple.py")
    
    if not installer_path.exists():
        print("✗ Simple installer not found")
        return False
    
    try:
        # Just check syntax
        with open(installer_path) as f:
            code = f.read()
        compile(code, str(installer_path), 'exec')
        print("✓ Simple installer syntax is valid")
        return True
    except SyntaxError as e:
        print(f"✗ Simple installer syntax error: {e}")
        return False

def main():
    print("Testing Enhanced Bulletproof CLI\n")
    
    tests = [
        ("Bulletproof Help", test_bulletproof_help),
        ("Simple Installer", test_simple_installer),
    ]
    
    passed = 0
    total = len(tests)
    
    for name, test_func in tests:
        print(f"\nRunning test: {name}")
        print("-" * 30)
        if test_func():
            passed += 1
            print(f"✓ {name} PASSED")
        else:
            print(f"✗ {name} FAILED")
    
    print(f"\n{'='*50}")
    print(f"Test Results: {passed}/{total} passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())