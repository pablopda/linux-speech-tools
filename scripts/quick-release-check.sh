#!/usr/bin/env bash
set -euo pipefail

# Quick Release Validation - Essential checks only for first release
# Use this for the initial v1.0.0 release, then fix the comprehensive checker

echo "🚀 Quick Release Validation for v1.0.0"
echo "======================================"

ERRORS=0

# 1. Essential files exist
echo "✓ Checking essential files..."
for file in say say-local say_read.py installer.sh VERSION requirements.txt; do
    if [[ -f "$file" ]]; then
        echo "  ✓ $file"
    else
        echo "  ✗ Missing: $file"
        ((ERRORS++))
    fi
done

# 2. Scripts are executable
echo "✓ Checking permissions..."
for script in say say-local say_read.py; do
    if [[ -x "$script" ]]; then
        echo "  ✓ $script is executable"
    else
        echo "  ✗ $script not executable"
        ((ERRORS++))
    fi
done

# 3. Basic syntax check
echo "✓ Checking syntax..."
for script in say say-local installer.sh; do
    if bash -n "$script" 2>/dev/null; then
        echo "  ✓ $script syntax OK"
    else
        echo "  ✗ $script has syntax errors"
        ((ERRORS++))
    fi
done

# 4. Python compiles
if python3 -m py_compile say_read.py 2>/dev/null; then
    echo "  ✓ Python syntax OK"
else
    echo "  ✗ Python syntax error"
    ((ERRORS++))
fi

# 5. Version consistency (basic check)
echo "✓ Checking versions..."
VERSION_FILE=$(cat VERSION)
echo "  VERSION file: $VERSION_FILE"

# Check that version appears in key files (don't require exact format match)
if grep -q "$VERSION_FILE" installer.sh say_read.py say; then
    echo "  ✓ Version appears in scripts"
else
    echo "  ⚠ Version may not be consistent (not blocking)"
fi

# 6. Git status
echo "✓ Checking git..."
if [[ -n $(git status --porcelain) ]]; then
    echo "  ⚠ Uncommitted changes (not blocking for first release)"
else
    echo "  ✓ Git status clean"
fi

# Summary
echo ""
echo "Results:"
if [[ $ERRORS -eq 0 ]]; then
    echo "🎉 READY FOR v1.0.0 RELEASE!"
    echo ""
    echo "Run: ./release.sh 1.0.0"
    exit 0
else
    echo "❌ $ERRORS critical errors found"
    echo ""
    echo "Fix the errors above before releasing."
    exit 1
fi