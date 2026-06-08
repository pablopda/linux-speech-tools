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
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

ERRORS=0
WARNINGS=0

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[⚠]${NC} $1"; ((WARNINGS+=1)); }
log_error() { echo -e "${RED}[✗]${NC} $1"; ((ERRORS+=1)); }

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
if [[ "$current_branch" != "main" ]]; then
    log_warning "Not on main branch (current: $current_branch)"
fi

log_success "Git repository validation complete"

# 2. File Structure Validation
log_info "Validating project structure..."

required_files=(
    "bin/say" "bin/say-local" "bin/say-read" "bin/say-read-es" "bin/talk2claude"
    "src/tts/say_read.py" "installer.sh" "scripts/release/release.sh"
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

executable_files=("bin/say" "bin/say-local" "bin/say-read" "bin/say-read-es" "bin/talk2claude" "src/tts/say_read.py" "scripts/release/release.sh")

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

shell_scripts=("bin/say" "bin/say-local" "bin/say-read" "bin/say-read-es" "bin/talk2claude" "installer.sh" "scripts/release/release.sh")

for script in "${shell_scripts[@]}"; do
    if [[ -f "$script" ]]; then
        if syntax_output=$(bash -n "$script" 2>&1); then
            log_success "Valid syntax: $script"
        else
            log_error "Syntax error in: $script"
            printf '%s\n' "$syntax_output"
        fi
    fi
done

# 5. Python Code Validation
log_info "Validating Python code..."

if command -v python3 >/dev/null; then
    if compile_output=$(python3 -m py_compile src/tts/say_read.py 2>&1); then
        log_success "Python syntax valid: src/tts/say_read.py"
    else
        log_error "Python syntax error in: src/tts/say_read.py"
        printf '%s\n' "$compile_output"
    fi

    # Check for basic imports
    if python3 -c "import sys; sys.path.append('src/tts'); import say_read" 2>/dev/null; then
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
if [[ -f "src/tts/say_read.py" ]] && grep -q "__version__" src/tts/say_read.py; then
    python_version=$(grep "__version__" src/tts/say_read.py | cut -d'"' -f2)
    if [[ "$python_version" == "$version_file" ]]; then
        log_success "Python version matches: $python_version"
    else
        log_error "Python version mismatch: $python_version vs $version_file"
    fi
fi

# Check say script version
if [[ -f "bin/say" ]] && grep -q "VERSION=" bin/say; then
    say_version=$(grep "VERSION=" bin/say | head -1 | cut -d'"' -f2)
    if [[ "$say_version" == "$version_file" ]]; then
        log_success "Say script version matches: $say_version"
    else
        log_error "Say script version mismatch: $say_version vs $version_file"
    fi
fi

# 7. Test Suite Execution
log_info "Running comprehensive test suite..."

if command -v uv >/dev/null 2>&1; then
    if test_output=$(uv run pytest tests/ -v 2>&1); then
        log_success "All tests passed"
    else
        log_error "Test suite failed"
        printf '%s\n' "$test_output"
    fi
elif [[ -f "tests/test_speech_tools.py" ]]; then
    if test_output=$(python3 tests/test_speech_tools.py 2>&1); then
        log_success "All tests passed"
    else
        log_error "Test suite failed"
        printf '%s\n' "$test_output"
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

if [[ -f "pyproject.toml" ]]; then
    log_success "pyproject.toml exists"
    required_deps=("edge-tts" "speechrecognition" "faster-whisper")
    for dep in "${required_deps[@]}"; do
        if grep -q "$dep" pyproject.toml; then
            log_success "Dependency listed: $dep"
        else
            log_warning "Missing dependency in pyproject.toml: $dep"
        fi
    done
else
    log_error "pyproject.toml missing"
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
if secret_matches=$(grep -r "password\|secret\|token" . --exclude-dir=.git --exclude="*.md" --exclude="pre-release-check.sh" | grep -v "password placeholder" | grep -v "# token"); then
    log_warning "Potential secrets found in code"
    printf '%s\n' "$secret_matches"
fi

# Check file permissions
world_writable=$(find . -type f -perm -002 -not -path "./.git/*")
if [[ -n "$world_writable" ]]; then
    log_warning "World-writable files found"
    printf '%s\n' "$world_writable"
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
    echo "1. Run: ./scripts/release/release.sh [patch|minor|major] [--dry-run]"
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
