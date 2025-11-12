#!/usr/bin/env bash
set -euo pipefail

# Linux Speech Tools - Release Automation Script
# Usage: ./release.sh [patch|minor|major|X.Y.Z] [--dry-run] [--force]

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Default values
DRY_RUN=false
FORCE=false
VERSION_TYPE=""
SPECIFIC_VERSION=""

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_usage() {
    cat << EOF
Linux Speech Tools Release Script

Usage: $0 [VERSION_TYPE] [OPTIONS]

VERSION_TYPE:
    patch           Increment patch version (1.0.0 -> 1.0.1)
    minor           Increment minor version (1.0.0 -> 1.1.0)
    major           Increment major version (1.0.0 -> 2.0.0)
    X.Y.Z           Specific version number (e.g., 2.1.3)

OPTIONS:
    --dry-run       Show what would be done without making changes
    --force         Skip some safety checks (use with caution)
    --help          Show this help message

Examples:
    $0 patch                    # Create patch release
    $0 minor --dry-run         # Preview minor release
    $0 1.2.3                   # Release specific version 1.2.3
    $0 major --force           # Force major release

EOF
}

get_current_version() {
    # Try to get version from VERSION file first
    if [[ -f "VERSION" ]]; then
        cat VERSION
    # Fall back to git tags
    elif git tag -l | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' >/dev/null; then
        git tag -l | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 | sed 's/^v//'
    else
        echo "0.0.0"
    fi
}

increment_version() {
    local version="$1"
    local type="$2"

    IFS='.' read -ra parts <<< "$version"
    local major="${parts[0]}"
    local minor="${parts[1]:-0}"
    local patch="${parts[2]:-0}"

    case "$type" in
        patch)
            echo "$major.$minor.$((patch + 1))"
            ;;
        minor)
            echo "$major.$((minor + 1)).0"
            ;;
        major)
            echo "$((major + 1)).0.0"
            ;;
        *)
            log_error "Invalid version type: $type"
            exit 1
            ;;
    esac
}

validate_version() {
    local version="$1"
    if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        log_error "Invalid version format: $version (expected X.Y.Z)"
        exit 1
    fi
}

check_git_status() {
    log_info "Checking git repository status..."

    # Check if we're in a git repository
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
        log_error "Not in a git repository"
        exit 1
    fi

    # Check for uncommitted changes
    if [[ -n $(git status --porcelain) ]] && [[ "$FORCE" != true ]]; then
        log_error "Working directory is not clean. Commit or stash changes first."
        log_info "Use --force to override this check"
        exit 1
    fi

    # Check if we're on main/master branch
    local current_branch
    current_branch=$(git branch --show-current)
    if [[ "$current_branch" != "main" && "$current_branch" != "master" ]] && [[ "$FORCE" != true ]]; then
        log_warning "Not on main/master branch (current: $current_branch)"
        log_info "Use --force to release from current branch"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    log_success "Git status check passed"
}

run_tests() {
    log_info "Running test suite..."

    # Check if Python tests exist and run them
    if [[ -f "tests/test_speech_tools.py" ]]; then
        if command -v python3 >/dev/null; then
            python3 tests/test_speech_tools.py || {
                log_error "Tests failed"
                exit 1
            }
        else
            log_warning "Python3 not available, skipping Python tests"
        fi
    fi

    # Test basic script syntax
    for script in say say-local say-read say-read-es talk2claude; do
        if [[ -f "$script" ]]; then
            bash -n "$script" || {
                log_error "Syntax error in $script"
                exit 1
            }
        fi
    done

    # Test installer script
    if [[ -f "installer.sh" ]]; then
        bash -n installer.sh || {
            log_error "Syntax error in installer.sh"
            exit 1
        }
    fi

    log_success "All tests passed"
}

update_version_in_files() {
    local new_version="$1"

    log_info "Updating version in files to v$new_version..."

    # Update VERSION file
    if [[ "$DRY_RUN" == true ]]; then
        log_info "Would update VERSION file to $new_version"
    else
        echo "$new_version" > VERSION
        log_success "Updated VERSION file"
    fi

    # Update installer.sh if it has version references
    if [[ -f "installer.sh" ]] && grep -q "VERSION=" installer.sh; then
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would update version in installer.sh"
        else
            sed -i.bak "s/VERSION=.*/VERSION=$new_version/" installer.sh
            rm -f installer.sh.bak
            log_success "Updated installer.sh"
        fi
    fi

    # Update say_read.py if it has version info
    if [[ -f "say_read.py" ]] && grep -q "__version__\|version" say_read.py; then
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would update version in say_read.py"
        else
            sed -i.bak "s/__version__ = .*/__version__ = \"$new_version\"/" say_read.py
            sed -i.bak "s/version = .*/version = \"$new_version\"/" say_read.py
            rm -f say_read.py.bak
            log_success "Updated say_read.py"
        fi
    fi

    # Update say script version
    if [[ -f "say" ]] && grep -q "VERSION=" say; then
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would update version in say script"
        else
            sed -i.bak "s/VERSION=.*/VERSION=\"$new_version\"/" say
            rm -f say.bak
            log_success "Updated say script"
        fi
    fi

    log_success "All version updates completed"
}

generate_changelog() {
    local new_version="$1"
    local current_version="$2"

    log_info "Generating changelog for v$new_version..."

    local changelog_file="CHANGELOG.md"
    local temp_changelog=$(mktemp)

    # Create changelog header
    cat > "$temp_changelog" << EOF
# Changelog

All notable changes to Linux Speech Tools will be documented in this file.

## [v$new_version] - $(date +%Y-%m-%d)

### Added
EOF

    # Get commits since last version
    if [[ "$current_version" != "0.0.0" ]]; then
        echo "### Changes since v$current_version" >> "$temp_changelog"
        echo "" >> "$temp_changelog"

        # Get commit messages since last tag
        if git tag -l | grep -q "v$current_version"; then
            git log "v$current_version"..HEAD --oneline --no-merges | sed 's/^/- /' >> "$temp_changelog" || true
        fi
    else
        echo "- Initial release" >> "$temp_changelog"
        echo "- Multi-engine TTS support (Edge TTS, Kokoro, Festival)" >> "$temp_changelog"
        echo "- Voice input with background recording" >> "$temp_changelog"
        echo "- Cross-distribution Linux support" >> "$temp_changelog"
        echo "- LATAM regional voice support (22 countries)" >> "$temp_changelog"
    fi

    echo "" >> "$temp_changelog"

    # Append existing changelog if it exists
    if [[ -f "$changelog_file" ]]; then
        echo "" >> "$temp_changelog"
        cat "$changelog_file" >> "$temp_changelog"
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log_info "Would create/update $changelog_file"
        echo "--- Changelog preview ---"
        head -20 "$temp_changelog"
        echo "--- End preview ---"
    else
        mv "$temp_changelog" "$changelog_file"
        git add "$changelog_file"
    fi

    rm -f "$temp_changelog"
    log_success "Changelog generated"
}

create_release_tag() {
    local new_version="$1"

    log_info "Creating release tag v$new_version..."

    if [[ "$DRY_RUN" == true ]]; then
        log_info "Would create and push tag: v$new_version"
        return
    fi

    # Commit version changes
    if [[ -n $(git status --porcelain) ]]; then
        git add .
        git commit -m "🚀 Release v$new_version

- Version bump to $new_version
- Updated changelog and version files

Release automated by release.sh script"
    fi

    # Create annotated tag
    git tag -a "v$new_version" -m "Release v$new_version

This release was created automatically by the release.sh script.

Key features:
- Multi-engine TTS support
- Cross-platform Linux compatibility
- Voice input and recording
- LATAM regional voice support

Installation:
curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/master/installer.sh | bash"

    # Push commit and tag
    git push origin "$(git branch --show-current)"
    git push origin "v$new_version"

    log_success "Tag v$new_version created and pushed"
}

monitor_release() {
    local version="$1"

    log_info "Monitoring GitHub Actions release workflow..."
    log_info "Visit: https://github.com/pablopda/linux-speech-tools/actions"
    log_info "Tag: v$version should trigger the automated release process"

    echo ""
    log_success "Release v$version initiated successfully!"
    echo ""
    echo "Next steps:"
    echo "1. Monitor GitHub Actions workflows at:"
    echo "   https://github.com/pablopda/linux-speech-tools/actions"
    echo "2. Once complete, the release will be available at:"
    echo "   https://github.com/pablopda/linux-speech-tools/releases/tag/v$version"
    echo "3. Installation command will be:"
    echo "   curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/master/installer.sh | bash"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        patch|minor|major)
            VERSION_TYPE="$1"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        [0-9]*.[0-9]*.[0-9]*)
            SPECIFIC_VERSION="$1"
            validate_version "$1"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Validate arguments
if [[ -z "$VERSION_TYPE" && -z "$SPECIFIC_VERSION" ]]; then
    log_error "Version type or specific version required"
    show_usage
    exit 1
fi

if [[ -n "$VERSION_TYPE" && -n "$SPECIFIC_VERSION" ]]; then
    log_error "Cannot specify both version type and specific version"
    exit 1
fi

# Main release process
main() {
    local current_version new_version

    echo "🚀 Linux Speech Tools Release Automation"
    echo "========================================"
    echo ""

    if [[ "$DRY_RUN" == true ]]; then
        log_warning "DRY RUN MODE - No changes will be made"
        echo ""
    fi

    # Get current version
    current_version=$(get_current_version)
    log_info "Current version: $current_version"

    # Calculate new version
    if [[ -n "$SPECIFIC_VERSION" ]]; then
        new_version="$SPECIFIC_VERSION"
    else
        new_version=$(increment_version "$current_version" "$VERSION_TYPE")
    fi

    log_info "New version: $new_version"
    echo ""

    # Pre-release checks
    check_git_status
    run_tests

    # Run pre-release validation (use quick check for now)
    if [[ -f "scripts/quick-release-check.sh" ]] && [[ "$DRY_RUN" != true ]]; then
        log_info "Running essential pre-release validation..."
        if ! bash scripts/quick-release-check.sh; then
            log_error "Essential pre-release validation failed"
            log_info "Fix critical issues above or use --force to skip validation"
            if [[ "$FORCE" != true ]]; then
                exit 1
            else
                log_warning "Skipping pre-release validation (--force used)"
            fi
        fi
        log_success "Pre-release validation passed"
    fi

    # Confirmation
    if [[ "$DRY_RUN" != true && "$FORCE" != true ]]; then
        echo ""
        log_warning "Ready to release v$new_version"
        read -p "Continue? (y/N): " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Release cancelled"
            exit 0
        fi
    fi

    # Release process
    echo ""
    log_info "Starting release process..."

    update_version_in_files "$new_version"
    generate_changelog "$new_version" "$current_version"
    create_release_tag "$new_version"

    if [[ "$DRY_RUN" != true ]]; then
        monitor_release "$new_version"
    else
        echo ""
        log_success "Dry run completed successfully"
        log_info "Run without --dry-run to perform actual release"
    fi
}

# Execute main function
main "$@"