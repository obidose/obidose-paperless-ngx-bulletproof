#!/usr/bin/env python3
"""
Independent pCloud OAuth Token Validator

This script tests pCloud OAuth tokens independently of the bulletproof system
to help isolate configuration issues.
"""

import subprocess
import sys
import json
import tempfile
import os

def test_rclone_basic():
    """Test that rclone is available and working."""
    print("🔧 Testing rclone availability...")
    try:
        result = subprocess.run(["rclone", "version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version = result.stdout.split('\n')[0]
            print(f"✅ rclone available: {version}")
            return True
        else:
            print(f"❌ rclone version failed: {result.stderr}")
            return False
    except FileNotFoundError:
        print("❌ rclone not found in PATH")
        return False
    except Exception as e:
        print(f"❌ rclone test error: {e}")
        return False

def test_token_format(token_json):
    """Test if the token is valid JSON with required fields."""
    print("📝 Testing token format...")
    try:
        token_data = json.loads(token_json)
        print("✅ Token is valid JSON")
        
        required_fields = ["access_token", "token_type"]
        missing_fields = [field for field in required_fields if field not in token_data]
        
        if missing_fields:
            print(f"❌ Token missing required fields: {missing_fields}")
            return False
        else:
            print("✅ Token has required fields")
            print(f"   • access_token: {token_data['access_token'][:20]}...")
            print(f"   • token_type: {token_data['token_type']}")
            return True
            
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON: {e}")
        return False

def test_rclone_config_create(token_json, hostname, test_name):
    """Test creating an rclone config with the token."""
    print(f"🔧 Testing rclone config creation for {test_name}...")
    
    # Clean up any existing test config
    subprocess.run(["rclone", "config", "delete", "test_pcloud"], 
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Try to create the config
    cmd = [
        "rclone", "config", "create", "test_pcloud", "pcloud",
        "token", token_json,
        "hostname", hostname,
        "--non-interactive"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print(f"✅ Config created successfully for {test_name}")
            return True
        else:
            print(f"❌ Config creation failed for {test_name}")
            print(f"   stdout: {result.stdout}")
            print(f"   stderr: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"❌ Config creation timed out for {test_name}")
        return False
    except Exception as e:
        print(f"❌ Config creation error for {test_name}: {e}")
        return False

def test_rclone_connection(test_name):
    """Test the actual connection to pCloud."""
    print(f"🌐 Testing connection for {test_name}...")
    
    try:
        result = subprocess.run(
            ["rclone", "about", "test_pcloud:"],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode == 0:
            print(f"✅ Connection successful for {test_name}!")
            print(f"   📊 {result.stdout.strip().split(chr(10))[0]}")
            return True
        else:
            print(f"❌ Connection failed for {test_name}")
            print(f"   Error: {result.stderr.strip()}")
            
            # Analyze the error
            error = result.stderr.lower()
            if "401" in error or "unauthorized" in error:
                print(f"   → Authentication issue (wrong region or invalid token)")
            elif "timeout" in error:
                print(f"   → Network timeout")
            elif "403" in error or "forbidden" in error:
                print(f"   → Access forbidden (account restrictions?)")
            else:
                print(f"   → Unknown error type")
            
            return False
            
    except subprocess.TimeoutExpired:
        print(f"❌ Connection test timed out for {test_name}")
        return False
    except Exception as e:
        print(f"❌ Connection test error for {test_name}: {e}")
        return False

def cleanup():
    """Clean up test configuration."""
    print("🧹 Cleaning up...")
    subprocess.run(["rclone", "config", "delete", "test_pcloud"], 
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    print("🔍 Independent pCloud OAuth Token Validator")
    print("=" * 60)
    
    # Get token
    if len(sys.argv) > 1:
        token_json = sys.argv[1]
    else:
        print("Paste your OAuth token JSON:")
        token_json = input().strip()
    
    if not token_json:
        print("❌ No token provided")
        return 1
    
    # Test sequence
    success_count = 0
    total_tests = 0
    
    # 1. Test rclone availability
    total_tests += 1
    if test_rclone_basic():
        success_count += 1
    else:
        print("❌ Cannot continue without rclone")
        return 1
    
    print()
    
    # 2. Test token format
    total_tests += 1
    if test_token_format(token_json):
        success_count += 1
    else:
        print("❌ Cannot continue with invalid token format")
        return 1
    
    print()
    
    # 3. Test both regions
    regions = [
        ("api.pcloud.com", "Global/US"),
        ("eapi.pcloud.com", "Europe")
    ]
    
    working_regions = []
    
    for hostname, region_name in regions:
        print(f"--- Testing {region_name} region ({hostname}) ---")
        
        # Test config creation
        total_tests += 1
        config_ok = test_rclone_config_create(token_json, hostname, region_name)
        if config_ok:
            success_count += 1
            
            # Test connection
            total_tests += 1
            if test_rclone_connection(region_name):
                success_count += 1
                working_regions.append(region_name)
        
        cleanup()
        print()
    
    # Summary
    print("=" * 60)
    print(f"📊 Results: {success_count}/{total_tests} tests passed")
    
    if working_regions:
        print(f"✅ Token works with: {', '.join(working_regions)}")
        print("✨ Your token is valid! The issue is likely in the bulletproof code.")
    else:
        print("❌ Token failed with both regions")
        print("\n💡 Troubleshooting suggestions:")
        print("1. Generate a fresh token: rclone authorize \"pcloud\"")
        print("2. Check your pCloud account region at pcloud.com")
        print("3. Verify your pCloud account is active and not restricted")
        print("4. Try WebDAV authentication instead")
    
    return 0 if working_regions else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⚠️ Test interrupted by user")
        cleanup()
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        cleanup()
        sys.exit(1)