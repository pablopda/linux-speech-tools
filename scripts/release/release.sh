#!/usr/bin/env bash
set -euo pipefail

# Linux Speech Tools - Release Automation Script
# Usage: ./scripts/release/release.sh [patch|minor|major|X.Y.Z] [--dry-run] [--force]

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

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

    # Check if we're on main branch. Releasing off main is a hard error unless
    # explicitly forced, so we never tag/publish from a feature branch by
    # accident.
    local current_branch
    current_branch=$(git branch --show-current)
    if [[ "$current_branch" != "main" ]] && [[ "$FORCE" != true ]]; then
        log_error "Not on main branch (current: $current_branch)"
        log_info "Switch to main, or pass --force to release from this branch."
        exit 1
    fi

    log_success "Git status check passed"
}

run_tests() {
    log_info "Running test suite..."

    if command -v uv >/dev/null; then
        uv run pytest tests/ -v || {
            log_error "Tests failed"
            exit 1
        }
    elif command -v python3 >/dev/null && [[ -f "tests/test_speech_tools.py" ]]; then
        python3 tests/test_speech_tools.py || {
            log_error "Tests failed"
            exit 1
        }
    else
        log_warning "No Python test runner available, skipping Python tests"
    fi

    # Test basic script syntax
    for script in bin/say bin/say-local bin/say-read bin/say-read-es bin/talk2claude; do
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

    # Update installer.sh's pinned release ref. installer.sh has no "VERSION="
    # line; the real pinned constants are INSTALLER_REF and DEFAULT_INSTALLER_REF
    # (the tarball URL is derived from DEFAULT_INSTALLER_REF, and the tarball
    # SHA256 is rewritten later by update_installer_tarball_sha256 once the tag
    # tarball exists).
    if [[ -f "installer.sh" ]]; then
        local new_ref="v$new_version"
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would set INSTALLER_REF/DEFAULT_INSTALLER_REF to $new_ref in installer.sh"
        else
            if ! grep -q '^DEFAULT_INSTALLER_REF=' installer.sh; then
                log_error "installer.sh is missing DEFAULT_INSTALLER_REF; cannot pin release ref"
                exit 1
            fi
            sed -i.bak \
                -e "s|^INSTALLER_REF=\"\${LST_INSTALLER_REF:-[^\"}]*}\"|INSTALLER_REF=\"\${LST_INSTALLER_REF:-$new_ref}\"|" \
                -e "s|^DEFAULT_INSTALLER_REF=\".*\"|DEFAULT_INSTALLER_REF=\"$new_ref\"|" \
                installer.sh
            rm -f installer.sh.bak
            log_success "Updated installer.sh pinned ref to $new_ref"
        fi
    fi

    # Update say_read.py if it has version info
    if [[ -f "src/tts/say_read.py" ]] && grep -q "__version__\|version" src/tts/say_read.py; then
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would update version in src/tts/say_read.py"
        else
            sed -i.bak "s/__version__ = .*/__version__ = \"$new_version\"/" src/tts/say_read.py
            sed -i.bak "s/version = .*/version = \"$new_version\"/" src/tts/say_read.py
            rm -f src/tts/say_read.py.bak
            log_success "Updated src/tts/say_read.py"
        fi
    fi

    if [[ -f "pyproject.toml" ]] && grep -q '^version = ' pyproject.toml; then
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would update version in pyproject.toml"
        else
            sed -i.bak "s/^version = .*/version = \"$new_version\"/" pyproject.toml
            rm -f pyproject.toml.bak
            log_success "Updated pyproject.toml"
        fi
    fi

    # Update say script version
    if [[ -f "bin/say" ]] && grep -q "VERSION=" bin/say; then
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would update version in bin/say script"
        else
            sed -i.bak "s/^VERSION=.*/VERSION=\"$new_version\"/" bin/say
            rm -f bin/say.bak
            log_success "Updated bin/say script"
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
            if changelog_entries=$(git log "v$current_version"..HEAD --oneline --no-merges | sed 's/^/- /'); then
                printf '%s\n' "$changelog_entries" >> "$temp_changelog"
            else
                log_warning "Could not read commits since v$current_version"
            fi
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

# H4: Compute the SHA256 of the just-published tag tarball and write it into
# installer.sh's DEFAULT_TARBALL_SHA256, then commit + push the fix.
#
# This is unavoidably a post-tag step: GitHub's auto-generated archive at
#   .../archive/refs/tags/vX.Y.Z.tar.gz
# does not exist until the tag is pushed, so its hash cannot be known when
# installer.sh is committed for the release. If this step fails (e.g. the
# archive is not available yet, or no network), installer.sh keeps the previous
# hash and the next streamed install will fail-closed with a checksum mismatch
# rather than install unverified code — so we surface a loud error here.
update_installer_tarball_sha256() {
    local new_version="$1"
    local branch="$2"
    local ref="v$new_version"
    local tarball_url="https://github.com/pablopda/linux-speech-tools/archive/refs/tags/${ref}.tar.gz"

    log_info "Pinning installer.sh tarball SHA256 for $ref..."

    if [[ ! -f "installer.sh" ]] || ! grep -q '^DEFAULT_TARBALL_SHA256=' installer.sh; then
        log_error "installer.sh is missing DEFAULT_TARBALL_SHA256; cannot pin tarball hash"
        return 1
    fi

    if ! command -v sha256sum >/dev/null 2>&1; then
        log_error "sha256sum is required to pin the installer tarball hash"
        return 1
    fi

    local tmp_archive new_sha
    tmp_archive="$(mktemp)"
    # GitHub may take a moment to materialize the archive after the tag push.
    local attempt
    for attempt in 1 2 3 4 5; do
        if curl -fsSL "$tarball_url" -o "$tmp_archive"; then
            break
        fi
        log_warning "Tag archive not ready yet (attempt $attempt); retrying..."
        sleep 5
    done

    if [[ ! -s "$tmp_archive" ]]; then
        rm -f "$tmp_archive"
        log_error "Could not download tag archive: $tarball_url"
        log_error "installer.sh DEFAULT_TARBALL_SHA256 is now STALE for $ref."
        log_error "Fix manually: curl -fsSL '$tarball_url' | sha256sum, then update"
        log_error "DEFAULT_TARBALL_SHA256 in installer.sh and commit/push."
        return 1
    fi

    new_sha="$(sha256sum "$tmp_archive" | awk '{print $1}')"
    rm -f "$tmp_archive"

    sed -i.bak "s/^DEFAULT_TARBALL_SHA256=\".*\"/DEFAULT_TARBALL_SHA256=\"$new_sha\"/" installer.sh
    rm -f installer.sh.bak
    log_success "installer.sh DEFAULT_TARBALL_SHA256 set to $new_sha"

    if [[ -n $(git status --porcelain -- installer.sh) ]]; then
        git add -- installer.sh
        git commit -m "🔒 Pin installer tarball SHA256 for $ref"
        git push origin "$branch"
        log_success "Pushed tarball SHA256 pin for $ref"
    else
        log_info "installer.sh tarball SHA256 already current; nothing to commit"
    fi
}

create_release_tag() {
    local new_version="$1"

    log_info "Creating release tag v$new_version..."

    if [[ "$DRY_RUN" == true ]]; then
        log_info "Would create and push tag: v$new_version"
        return
    fi

    local branch
    branch="$(git branch --show-current)"

    # Refuse to push a release from a non-main branch unless explicitly forced,
    # so we cannot accidentally tag/publish off a feature branch.
    if [[ "$branch" != "main" && "$FORCE" != true ]]; then
        log_error "Refusing to create a release from non-main branch '$branch'."
        log_info "Re-run from main, or pass --force to release from this branch."
        exit 1
    fi

    # Stage only the release files that actually changed (avoid a blind add of a
    # fixed list, which could sweep in unrelated edits or fail on missing paths).
    local candidate_files=(VERSION CHANGELOG.md pyproject.toml installer.sh src/tts/say_read.py bin/say)
    local staged=()
    local f
    for f in "${candidate_files[@]}"; do
        if [[ -n $(git status --porcelain -- "$f") ]]; then
            git add -- "$f"
            staged+=("$f")
        fi
    done
    if [[ ${#staged[@]} -gt 0 ]]; then
        log_info "Staging changed release files: ${staged[*]}"
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
curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/main/installer.sh | bash"

    # Push commit and tag. Pushing the tag first makes the GitHub auto-generated
    # source tarball exist, which is a prerequisite for computing its SHA256.
    git push origin "$branch"
    git push origin "v$new_version"

    log_success "Tag v$new_version created and pushed"

    # H4: now that the tag tarball exists, pin its real SHA256 into installer.sh
    # so the next streamed `curl | bash` install verifies correctly.
    update_installer_tarball_sha256 "$new_version" "$branch"
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
    echo "   curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/main/installer.sh | bash"
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

    # Run pre-release validation. The checker is REQUIRED: a missing checker is
    # a hard error (never silently skip the QA gate).
    local pre_release_checker="scripts/release/pre-release-check.sh"
    if [[ "$DRY_RUN" != true ]]; then
        if [[ ! -f "$pre_release_checker" ]]; then
            log_error "Pre-release checker not found: $pre_release_checker"
            log_error "Refusing to release without the QA gate."
            exit 1
        fi
        log_info "Running pre-release validation ($pre_release_checker)..."
        if ! bash "$pre_release_checker"; then
            log_error "Pre-release validation failed"
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
