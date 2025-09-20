#!/usr/bin/env python3
"""
pCloud Region Diagnostic Tool

This script helps diagnose pCloud OAuth token issues by testing both regions
and providing detailed feedback about what's failing.
"""

import subprocess
import sys
import json

def test_token_with_region(token_json, host, region_name):
    """Test an OAuth token with a specific pCloud region."""
    print(f"\n=== Testing {region_name} region ({host}) ===")
    
    try:
        # Parse token
        token_data = json.loads(token_json)
        if "access_token" not in token_data:
            print("❌ Token missing access_token field")
            return False
            
        print(f"✓ Token is valid JSON with access_token")
        
        # Create rclone remote
        print("🔧 Creating rclone remote...")
        subprocess.run(["rclone", "config", "delete", "pcloud_test"], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        result = subprocess.run([
            "rclone", "config", "create", "pcloud_test", "pcloud",
            "token", token_json, "hostname", host, "--non-interactive"
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"❌ Failed to create remote: {result.stderr}")
            return False
            
        print("✓ Remote created successfully")
        
        # Test connection
        print("🌐 Testing connection...")
        result = subprocess.run(
            ["rclone", "about", "pcloud_test:"],
            timeout=10, capture_output=True, text=True
        )
        
        if result.returncode == 0:
            print(f"✅ SUCCESS! Token works with {region_name} region")
            print(f"📊 Storage info: {result.stdout.strip().split(chr(10))[0]}")
            return True
        else:
            print(f"❌ Connection failed: {result.stderr.strip()}")
            if "unauthorized" in result.stderr.lower():
                print(f"   → Likely cause: Token not valid for {region_name} region")
            elif "timeout" in result.stderr.lower():
                print(f"   → Likely cause: Network timeout to {region_name}")
            return False
            
    except json.JSONDecodeError:
        print("❌ Invalid JSON format")
        return False
    except subprocess.TimeoutExpired:
        print(f"❌ Connection timeout to {region_name}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def main():
    print("🔍 pCloud OAuth Token Diagnostic Tool")
    print("=" * 50)
    
    if len(sys.argv) > 1:
        token_json = sys.argv[1]
    else:
        print("Paste your OAuth token JSON:")
        token_json = input().strip()
    
    if not token_json:
        print("❌ No token provided")
        return
    
    regions = [
        ("api.pcloud.com", "Global/US"),
        ("eapi.pcloud.com", "Europe")
    ]
    
    success_count = 0
    for host, region_name in regions:
        if test_token_with_region(token_json, host, region_name):
            success_count += 1
    
    print(f"\n{'='*50}")
    if success_count == 0:
        print("❌ Token failed for both regions")
        print("\n💡 Suggestions:")
        print("1. Generate a new token with: rclone authorize \"pcloud\"")
        print("2. Check your pCloud account region at pcloud.com")
        print("3. Try WebDAV authentication instead")
        print("4. Check network connectivity to pCloud")
    elif success_count == 1:
        print("✅ Token works with one region - this is normal!")
    else:
        print("🎉 Token works with both regions - very rare!")
    
    # Cleanup
    subprocess.run(["rclone", "config", "delete", "pcloud_test"], 
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == "__main__":
    main()