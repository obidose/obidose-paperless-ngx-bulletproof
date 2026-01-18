#!/bin/bash
# Cleanup script to remove unnecessary files

set -e

echo "🧹 Cleaning up Paperless-NGX Bulletproof project..."

# Remove extra documentation
echo "📄 Removing redundant documentation..."
rm -f ARCHITECTURE.md
rm -f COMPLETION_CHECKLIST.md
rm -f DOCUMENTATION_INDEX.md
rm -f MENU_GUIDE.md
rm -f PROJECT_SUMMARY.md
rm -f REFACTORING_SUMMARY.md
rm -f SINGLE_COMMAND.md
rm -f OBSOLETE_FILES.txt
rm -f CLEANUP.txt

# Remove obsolete Python files
echo "🐍 Removing obsolete Python files..."
rm -f tools/bulletproof.py
rm -f tools/bulletproof_simple.py

# Check if utils/env.py is used
if [ -f utils/env.py ]; then
    echo "⚠️  Found utils/env.py - verify if used before manual deletion"
fi

# Remove empty directories
if [ -d tools ] && [ -z "$(ls -A tools)" ]; then
    echo "📁 Removing empty tools/ directory..."
    rmdir tools
fi

# Replace README
echo "📝 Updating README..."
mv README.new.md README.md

echo "✅ Cleanup complete!"
echo ""
echo "Final structure:"
find . -type f -name "*.py" -o -name "*.md" -o -name "*.env" | grep -v ".git" | grep -v "__pycache__" | sort
