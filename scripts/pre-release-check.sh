#!/usr/bin/env bash
set -euo pipefail

# Pre-Release Quality Assurance Script
# Comprehensive validation before any release

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

ERRORS=0
WARNINGS=0

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[⚠]${NC} $1"; ((WARNINGS++)); }
log_error() { echo -e "${RED}[✗]${NC} $1"; ((ERRORS++)); }

echo "🔍 Linux Speech Tools - Pre-Release Quality Check"
echo "=================================================="

# 1. Git Repository Validation
log_info "Checking Git repository status..."

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    log_error "Not in a Git repository"
    exit 1
fi

if [[ -n $(git status --porcelain) ]]; then
    log_warning "Working directory has uncommitted changes"
    git status --short
fi

current_branch=$(git branch --show-current)
if [[ "$current_branch" != "main" && "$current_branch" != "master" ]]; then
    log_warning "Not on main/master branch (current: $current_branch)"
fi

log_success "Git repository validation complete"

# 2. File Structure Validation
log_info "Validating project structure..."

required_files=(
    "say" "say-local" "say-read" "say-read-es" "talk2claude"
    "say_read.py" "installer.sh" "release.sh"
    "README.md" "requirements.txt" "VERSION"
    "tests/test_speech_tools.py"
    ".github/workflows/ci.yml"
)

for file in "${required_files[@]}"; do
    if [[ -f "$file" ]]; then
        log_success "Found: $file"
    else
        log_error "Missing: $file"
    fi
done

# 3. Executable Permissions Check
log_info "Checking executable permissions..."

executable_files=("say" "say-local" "say-read" "say-read-es" "talk2claude" "say_read.py" "release.sh")

for file in "${executable_files[@]}"; do
    if [[ -f "$file" ]]; then
        if [[ -x "$file" ]]; then
            log_success "Executable: $file"
        else
            log_error "Not executable: $file"
        fi
    fi
done

# 4. Shell Script Syntax Check
log_info "Validating shell script syntax..."

shell_scripts=("say" "say-local" "say-read" "say-read-es" "talk2claude" "installer.sh" "release.sh")

for script in "${shell_scripts[@]}"; do
    if [[ -f "$script" ]]; then
        if bash -n "$script" 2>/dev/null; then
            log_success "Valid syntax: $script"
        else
            log_error "Syntax error in: $script"
            bash -n "$script" || true
        fi
    fi
done

# 5. Python Code Validation
log_info "Validating Python code..."

if command -v python3 >/dev/null; then
    if python3 -m py_compile say_read.py 2>/dev/null; then
        log_success "Python syntax valid: say_read.py"
    else
        log_error "Python syntax error in: say_read.py"
        python3 -m py_compile say_read.py || true
    fi

    # Check for basic imports
    if python3 -c "import sys; sys.path.append('.'); import say_read" 2>/dev/null; then
        log_success "Python imports successful"
    else
        log_warning "Python import issues (may be due to missing dependencies)"
    fi
else
    log_warning "Python3 not available for validation"
fi

# 6. Version Consistency Check
log_info "Checking version consistency..."

version_file=""
if [[ -f "VERSION" ]]; then
    version_file=$(cat VERSION)
    log_info "VERSION file: $version_file"
else
    log_error "VERSION file missing"
fi

# Check installer version
if [[ -f "installer.sh" ]] && grep -q "VERSION=" installer.sh; then
    installer_version=$(grep "VERSION=" installer.sh | head -1 | cut -d'=' -f2 | tr -d '"')
    if [[ "$installer_version" == "$version_file" ]]; then
        log_success "Installer version matches: $installer_version"
    else
        log_error "Installer version mismatch: $installer_version vs $version_file"
    fi
fi

# Check Python version
if [[ -f "say_read.py" ]] && grep -q "__version__" say_read.py; then
    python_version=$(grep "__version__" say_read.py | cut -d'"' -f2)
    if [[ "$python_version" == "$version_file" ]]; then
        log_success "Python version matches: $python_version"
    else
        log_error "Python version mismatch: $python_version vs $version_file"
    fi
fi

# Check say script version
if [[ -f "say" ]] && grep -q "VERSION=" say; then
    say_version=$(grep "VERSION=" say | head -1 | cut -d'"' -f2)
    if [[ "$say_version" == "$version_file" ]]; then
        log_success "Say script version matches: $say_version"
    else
        log_error "Say script version mismatch: $say_version vs $version_file"
    fi
fi

# 7. Test Suite Execution
log_info "Running comprehensive test suite..."

if [[ -f "tests/test_speech_tools.py" ]]; then
    if python3 tests/test_speech_tools.py 2>/dev/null; then
        log_success "All tests passed"
    else
        log_error "Test suite failed"
        python3 tests/test_speech_tools.py || true
    fi
else
    log_error "Test suite missing"
fi

# 8. Documentation Check
log_info "Validating documentation..."

if [[ -f "README.md" ]]; then
    readme_size=$(wc -c < README.md)
    if [[ $readme_size -gt 100 ]]; then
        log_success "README.md exists and has content ($readme_size bytes)"
    else
        log_warning "README.md is very small ($readme_size bytes)"
    fi
else
    log_error "README.md missing"
fi

# Check for changelog
if [[ -f "CHANGELOG.md" ]]; then
    log_success "CHANGELOG.md exists"
else
    log_warning "CHANGELOG.md not found (will be generated during release)"
fi

# 9. Dependency Check
log_info "Checking dependencies..."

if [[ -f "requirements.txt" ]]; then
    log_success "requirements.txt exists"

    required_deps=("edge-tts" "pyaudio" "speechrecognition")
    for dep in "${required_deps[@]}"; do
        if grep -q "$dep" requirements.txt; then
            log_success "Dependency listed: $dep"
        else
            log_warning "Missing dependency in requirements.txt: $dep"
        fi
    done
else
    log_error "requirements.txt missing"
fi

# 10. CI/CD Configuration Check
log_info "Validating CI/CD configuration..."

ci_files=(
    ".github/workflows/ci.yml"
    ".github/workflows/release.yml"
    ".github/workflows/performance-test.yml"
    ".github/workflows/security-scan.yml"
)

for ci_file in "${ci_files[@]}"; do
    if [[ -f "$ci_file" ]]; then
        log_success "CI/CD file exists: $ci_file"
    else
        log_warning "Missing CI/CD file: $ci_file"
    fi
done

# 11. Security Check
log_info "Basic security validation..."

# Check for potential security issues
if grep -r "password\|secret\|token" . --exclude-dir=.git --exclude="*.md" --exclude="pre-release-check.sh" | grep -v "password placeholder" | grep -v "# token" >/dev/null; then
    log_warning "Potential secrets found in code"
    grep -r "password\|secret\|token" . --exclude-dir=.git --exclude="*.md" --exclude="pre-release-check.sh" | grep -v "password placeholder" | grep -v "# token" || true
fi

# Check file permissions
if find . -type f -perm -002 -not -path "./.git/*" | head -1 >/dev/null 2>&1; then
    log_warning "World-writable files found"
    find . -type f -perm -002 -not -path "./.git/*" || true
fi

# 12. Performance Basic Check
log_info "Basic performance validation..."

# Check script size (they should be reasonably sized)
for script in "${executable_files[@]}"; do
    if [[ -f "$script" ]]; then
        size=$(wc -c < "$script")
        if [[ $size -gt 100000 ]]; then  # 100KB threshold
            log_warning "$script is quite large ($size bytes)"
        else
            log_success "$script size OK ($size bytes)"
        fi
    fi
done

# Final Report
echo ""
echo "📊 Pre-Release Check Summary"
echo "=============================="

if [[ $ERRORS -eq 0 ]]; then
    log_success "No errors found! ✅"
else
    log_error "$ERRORS error(s) found! ❌"
fi

if [[ $WARNINGS -eq 0 ]]; then
    log_success "No warnings! ✅"
else
    log_warning "$WARNINGS warning(s) found! ⚠️"
fi

echo ""
if [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}🎉 READY FOR RELEASE! 🎉${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Run: ./release.sh [patch|minor|major] [--dry-run]"
    echo "2. Review the generated changelog"
    echo "3. Push the release tag to trigger automated deployment"
    echo ""
    exit 0
else
    echo -e "${RED}❌ NOT READY FOR RELEASE ❌${NC}"
    echo ""
    echo "Please fix the errors above before proceeding with the release."
    echo ""
    exit 1
fi