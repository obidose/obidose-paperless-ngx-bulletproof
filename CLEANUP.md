# Cleanup Instructions

This document lists the files and changes that should be made to clean up the repository.

## Files to Remove

### Test Files (obsolete)
- `test_enhanced_cli.py` - Development test script no longer needed
- `test_token.py` - OAuth token testing script, obsolete
- `test_tty.py` - TTY detection test, obsolete  
- `test_visual.py` - Visual CLI test, obsolete
- `manual_test.sh` - Manual testing script, obsolete

### Redundant Installers
- `install_simple.py` - Redundant installer, `install.py` is the main one

### Legacy Code
- `tools/bulletproof_old.py` - Old monolithic version (2004 lines), completely replaced by modular version

### Keep for Now
- `tools/pcloud_diagnostic.py` - May be useful for debugging cloud storage issues

## README Updates

The README.md has been started but needs complete replacement with the content from `README_NEW.md`:

1. Current README has formatting issues and duplicate content
2. New README is clean, well-structured, and documents all new features
3. Should replace entire content of README.md with README_NEW.md
4. Then remove README_NEW.md

## Code is Clean

The main codebase in `tools/` is well-structured:
- All imports are used
- Modular architecture is clean
- No unused functions detected
- Menu standardization completed (all numbered menus with 0 for quit)

## Summary of Improvements Made

1. ✅ **Fixed missing modules** - New instances now include gotenberg, tika, and all essential services
2. ✅ **Auto-start instances** - New instances can be automatically started after creation
3. ✅ **Enhanced diagnostics** - Comprehensive system health monitoring with `cmd_doctor`
4. ✅ **Backup integrity checks** - Advanced verification in backup explorer
5. ✅ **Standardized menus** - All menus use numbers with 0 for quit/back

The bulletproof system is now significantly more robust and user-friendly.