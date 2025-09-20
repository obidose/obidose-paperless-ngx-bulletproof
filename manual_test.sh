#!/bin/bash
# Manual pCloud OAuth Token Test Script
# This script manually tests pCloud OAuth tokens step by step

echo "🔍 Manual pCloud OAuth Token Test"
echo "================================="

# Check if rclone is available
echo "1. Testing rclone availability..."
if ! command -v rclone &> /dev/null; then
    echo "❌ rclone not found in PATH"
    exit 1
fi

echo "✅ rclone is available"
rclone version | head -1

echo ""
echo "2. Testing token format..."
echo "Please paste your OAuth token JSON:"
read -r TOKEN

# Basic JSON validation
if echo "$TOKEN" | jq . > /dev/null 2>&1; then
    echo "✅ Token is valid JSON"
else
    echo "❌ Token is not valid JSON"
    exit 1
fi

# Check for required fields
if echo "$TOKEN" | jq -e '.access_token' > /dev/null 2>&1; then
    echo "✅ Token has access_token field"
else
    echo "❌ Token missing access_token field"
    exit 1
fi

echo ""
echo "3. Testing Global/US region (api.pcloud.com)..."

# Clean up any existing test config
rclone config delete pcloud_test_global > /dev/null 2>&1

# Create config for Global region
if rclone config create pcloud_test_global pcloud token "$TOKEN" hostname api.pcloud.com --non-interactive; then
    echo "✅ Config created for Global region"
    
    # Test connection
    echo "   Testing connection..."
    if timeout 30 rclone about pcloud_test_global: > /dev/null 2>&1; then
        echo "✅ Connection successful to Global region!"
        GLOBAL_WORKS=true
    else
        echo "❌ Connection failed to Global region"
        echo "   Error details:"
        timeout 30 rclone about pcloud_test_global: 2>&1 | head -3
        GLOBAL_WORKS=false
    fi
else
    echo "❌ Failed to create config for Global region"
    GLOBAL_WORKS=false
fi

# Cleanup
rclone config delete pcloud_test_global > /dev/null 2>&1

echo ""
echo "4. Testing Europe region (eapi.pcloud.com)..."

# Clean up any existing test config
rclone config delete pcloud_test_europe > /dev/null 2>&1

# Create config for Europe region
if rclone config create pcloud_test_europe pcloud token "$TOKEN" hostname eapi.pcloud.com --non-interactive; then
    echo "✅ Config created for Europe region"
    
    # Test connection
    echo "   Testing connection..."
    if timeout 30 rclone about pcloud_test_europe: > /dev/null 2>&1; then
        echo "✅ Connection successful to Europe region!"
        EUROPE_WORKS=true
    else
        echo "❌ Connection failed to Europe region"
        echo "   Error details:"
        timeout 30 rclone about pcloud_test_europe: 2>&1 | head -3
        EUROPE_WORKS=false
    fi
else
    echo "❌ Failed to create config for Europe region"
    EUROPE_WORKS=false
fi

# Cleanup
rclone config delete pcloud_test_europe > /dev/null 2>&1

echo ""
echo "================================="
echo "📊 RESULTS:"

if [ "$GLOBAL_WORKS" = true ]; then
    echo "✅ Token works with Global/US region"
fi

if [ "$EUROPE_WORKS" = true ]; then
    echo "✅ Token works with Europe region"
fi

if [ "$GLOBAL_WORKS" = true ] || [ "$EUROPE_WORKS" = true ]; then
    echo ""
    echo "✨ Your token is VALID! The issue is in the bulletproof code."
    echo "💡 The problem is likely in how bulletproof calls rclone."
else
    echo ""
    echo "❌ Token failed with both regions"
    echo ""
    echo "💡 Troubleshooting suggestions:"
    echo "1. Generate a fresh token: rclone authorize \"pcloud\""
    echo "2. Check your pCloud account at pcloud.com"
    echo "3. Try WebDAV authentication instead"
fi