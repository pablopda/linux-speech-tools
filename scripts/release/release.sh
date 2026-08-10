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
RESUME_VERSION=""

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
    --resume-bootstrap X.Y.Z
                    Resume the asset/hash/bootstrap phase for an existing
                    source release without regenerating the release commit
    --help          Show this help message

Examples:
    $0 patch                    # Create patch release
    $0 minor --dry-run         # Preview minor release
    $0 1.2.3                   # Release specific version 1.2.3
    $0 major --force           # Force major release
    $0 --resume-bootstrap 1.2.3

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

    # The root project version is part of uv.lock. Regenerate it immediately so
    # the exact tagged snapshot remains usable with `uv sync --locked` in CI and
    # by the immutable bootstrap installer.
    if [[ -f "pyproject.toml" && -f "uv.lock" ]]; then
        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would regenerate uv.lock for v$new_version"
        elif ! command -v uv >/dev/null 2>&1; then
            log_error "uv is required to regenerate uv.lock for the release"
            return 1
        elif ! uv lock; then
            log_error "Could not regenerate uv.lock for v$new_version"
            return 1
        else
            log_success "Regenerated uv.lock"
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
    local temp_changelog release_date
    temp_changelog="$(mktemp)"
    release_date="$(date +%Y-%m-%d)"

    if [[ ! -f "$changelog_file" ]] || \
       [[ "$(grep -xc '## \[Unreleased\]' "$changelog_file" || true)" -ne 1 ]]; then
        rm -f "$temp_changelog"
        log_error "CHANGELOG.md must contain exactly one ## [Unreleased] section"
        return 1
    fi

    # A release candidate may be curated and validated locally before the
    # separately approved tag/publish action. Accept that exact prepared state
    # without promoting Unreleased a second time, but fail closed if new notes
    # have accumulated above the existing release section.
    local existing_release_count unreleased_content
    existing_release_count="$(
        grep -Ec "^## \[v${new_version//./\\.}\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" \
            "$changelog_file" || true
    )"
    if [[ "$existing_release_count" -gt 0 ]]; then
        if [[ "$existing_release_count" -ne 1 ]]; then
            rm -f "$temp_changelog"
            log_error "CHANGELOG.md contains duplicate v$new_version sections"
            return 1
        fi
        unreleased_content="$(
            awk '
                $0 == "## [Unreleased]" { capture = 1; next }
                capture && $0 ~ /^## \[/ { exit }
                capture && NF { print }
            ' "$changelog_file"
        )"
        if [[ "$unreleased_content" != "No changes yet." ]] \
            || ! grep -Fqx \
                "[Unreleased]: https://github.com/pablopda/linux-speech-tools/compare/v$new_version...HEAD" \
                "$changelog_file" \
            || ! grep -Fq "[v$new_version]: " "$changelog_file"; then
            rm -f "$temp_changelog"
            log_error "Existing v$new_version candidate has inconsistent release notes"
            return 1
        fi
        rm -f "$temp_changelog"
        log_info "CHANGELOG.md already contains the prepared v$new_version candidate"
        log_success "Changelog generated"
        return 0
    fi

    # Promote the curated Unreleased content in place. Prepending a generated
    # changelog used to duplicate the document heading and bury the maintained
    # release notes under a raw commit list.
    awk -v version="$new_version" -v release_date="$release_date" \
        -v current_version="$current_version" '
        $0 == "## [Unreleased]" && !promoted {
            print "## [Unreleased]"
            print ""
            print "No changes yet."
            print ""
            print "## [v" version "] - " release_date
            promoted = 1
            next
        }
        $0 ~ /^\[Unreleased\]:/ {
            print "[Unreleased]: https://github.com/pablopda/linux-speech-tools/compare/v" version "...HEAD"
            print "[v" version "]: https://github.com/pablopda/linux-speech-tools/compare/v" current_version "...v" version
            links = 1
            next
        }
        { print }
        END {
            if (!promoted || !links)
                exit 42
        }
    ' "$changelog_file" > "$temp_changelog" || {
        rm -f "$temp_changelog"
        log_error "Could not promote the Unreleased changelog section"
        return 1
    }

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

# GitHub's generated source archives are not a durable checksum boundary. The
# release bootstrap instead pins a versioned asset uploaded to the GitHub
# Release. The source tag is created first; the asset hash and bootstrap tag are
# necessarily a second, resumable phase.

remote_tag_commit() {
    local tag="$1"
    local listing peeled direct
    listing="$(git ls-remote --tags origin "refs/tags/$tag" "refs/tags/$tag^{}")" || return 1
    peeled="$(printf '%s\n' "$listing" | awk '$2 ~ /\^\{\}$/ {print $1; exit}')"
    direct="$(printf '%s\n' "$listing" | awk '$2 !~ /\^\{\}$/ {print $1; exit}')"
    printf '%s\n' "${peeled:-$direct}"
}

ensure_remote_annotated_tag() {
    local tag="$1"
    local commit="$2"
    local message="$3"
    local remote_commit local_commit

    remote_commit="$(remote_tag_commit "$tag")" || return 1
    if [[ -n "$remote_commit" ]]; then
        if [[ "$remote_commit" != "$commit" ]]; then
            log_error "Remote tag $tag points to $remote_commit, expected $commit"
            return 1
        fi
        log_info "Remote tag $tag already points to the expected commit"
        return 0
    fi

    if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
        local_commit="$(git rev-list -n 1 "$tag")"
        if [[ "$local_commit" != "$commit" ]]; then
            log_error "Local tag $tag points to $local_commit, expected $commit"
            return 1
        fi
    else
        git tag -a "$tag" "$commit" -m "$message"
    fi

    git push origin "refs/tags/$tag" || return 1
    remote_commit="$(remote_tag_commit "$tag")" || return 1
    if [[ "$remote_commit" != "$commit" ]]; then
        log_error "Remote tag verification failed for $tag"
        return 1
    fi
}

ensure_local_annotated_tag() {
    local tag="$1"
    local commit="$2"
    local message="$3"
    local local_commit
    if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
        local_commit="$(git rev-list -n 1 "$tag")"
        if [[ "$local_commit" != "$commit" ]]; then
            log_error "Local tag $tag points to $local_commit, expected $commit"
            return 1
        fi
    else
        git tag -a "$tag" "$commit" -m "$message" || return 1
    fi
}

build_release_source_asset() {
    local version="$1"
    local source_commit="$2"
    local output_dir="$3"
    local package_name="linux-speech-tools-$version"
    local asset="$output_dir/$package_name.tar.gz"

    git archive --format=tar --prefix="$package_name/" "$source_commit" \
        | gzip -n > "$asset" || return 1
    (cd "$output_dir" && sha256sum "$package_name.tar.gz" > "$package_name.tar.gz.sha256") \
        || return 1
    printf '%s\n' "$asset"
}

extract_release_notes() {
    local version="$1"
    local source_commit="$2"
    local output_file="$3"
    local changelog
    changelog="$(mktemp)"
    git show "$source_commit:CHANGELOG.md" > "$changelog" || {
        rm -f "$changelog"
        return 1
    }
    awk -v heading="## [v$version]" '
        index($0, heading) == 1 { capture = 1 }
        capture && $0 ~ /^## \[/ && index($0, heading) != 1 { exit }
        capture { print }
        END { if (!capture) exit 42 }
    ' "$changelog" > "$output_file" || {
        rm -f "$changelog"
        log_error "CHANGELOG.md has no release section for v$version"
        return 1
    }
    rm -f "$changelog"
}

ensure_release_asset() {
    local version="$1"
    local asset="$2"
    local notes_file="$3"
    local locked_sha="${4:-}"
    local canonical_output="${5:-}"
    local ref="v$version"
    local asset_name expected_sha release_tag is_draft download_dir downloaded actual_sha
    asset_name="$(basename "$asset")"
    expected_sha="$(sha256sum "$asset" | awk '{print $1}')"
    release_tag="$(gh release view "$ref" --json tagName --jq .tagName 2>/dev/null || true)"

    if [[ -z "$release_tag" ]]; then
        gh release create "$ref" "$asset" "$asset.sha256" \
            --verify-tag --draft \
            --title "Linux Speech Tools $ref" \
            --notes-file "$notes_file" || return 1
        release_tag="$(gh release view "$ref" --json tagName --jq .tagName)" || return 1
    fi

    if [[ "$release_tag" != "$ref" ]]; then
        log_error "GitHub Release tag does not match $ref"
        return 1
    fi
    is_draft="$(gh release view "$ref" --json isDraft --jq .isDraft)" || return 1

    download_dir="$(mktemp -d)"
    downloaded="$download_dir/$asset_name"
    if ! gh release download "$ref" --pattern "$asset_name" --output "$downloaded" 2>/dev/null; then
        rm -f "$downloaded"
        if [[ "$is_draft" != "true" || -n "$locked_sha" ]]; then
            rm -rf "$download_dir"
            log_error "Release $ref is missing the canonical asset bound to its bootstrap"
            return 1
        fi
        gh release upload "$ref" "$asset" "$asset.sha256" || {
            rm -rf "$download_dir"
            return 1
        }
        gh release download "$ref" --pattern "$asset_name" --output "$downloaded" || {
            rm -rf "$download_dir"
            return 1
        }
    fi

    actual_sha="$(sha256sum "$downloaded" | awk '{print $1}')"
    if [[ -n "$locked_sha" ]]; then
        if [[ "$actual_sha" != "$locked_sha" ]]; then
            rm -rf "$download_dir"
            log_error "Existing bootstrap pins $locked_sha but release asset is $actual_sha"
            return 1
        fi
        if [[ -n "$canonical_output" ]]; then
            cp -- "$downloaded" "$canonical_output" || {
                rm -rf "$download_dir"
                return 1
            }
            VERIFIED_ASSET_FILE="$canonical_output"
        fi
        rm -rf "$download_dir"
        VERIFIED_ASSET_SHA="$actual_sha"
        log_success "Verified existing bootstrap-bound release asset SHA256: $actual_sha"
        return 0
    fi
    if [[ "$actual_sha" != "$expected_sha" && "$is_draft" == "true" ]]; then
        log_warning "Replacing mismatched asset on draft release $ref"
        gh release upload "$ref" "$asset" "$asset.sha256" --clobber || {
            rm -rf "$download_dir"
            return 1
        }
        gh release download "$ref" --pattern "$asset_name" --output "$downloaded" --clobber || {
            rm -rf "$download_dir"
            return 1
        }
        actual_sha="$(sha256sum "$downloaded" | awk '{print $1}')"
    fi
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        rm -rf "$download_dir"
        log_error "Release asset digest mismatch for $asset_name"
        log_error "Expected $expected_sha, downloaded $actual_sha"
        return 1
    fi
    if [[ -n "$canonical_output" ]]; then
        cp -- "$downloaded" "$canonical_output" || {
            rm -rf "$download_dir"
            return 1
        }
        VERIFIED_ASSET_FILE="$canonical_output"
    fi
    rm -rf "$download_dir"
    VERIFIED_ASSET_SHA="$actual_sha"
    log_success "Verified uploaded release asset SHA256: $actual_sha"
}

pin_installer_release_asset() {
    local version="$1"
    local asset_sha="$2"
    local branch="$3"
    local ref="v$version"

    if [[ ! "$asset_sha" =~ ^[0-9a-f]{64}$ ]]; then
        log_error "Invalid release asset SHA256: $asset_sha"
        return 1
    fi

    sed -i.bak \
        -e "s|^INSTALLER_REF=\"\${LST_INSTALLER_REF:-[^\"}]*}\"|INSTALLER_REF=\"\${LST_INSTALLER_REF:-$ref}\"|" \
        -e "s|^DEFAULT_INSTALLER_REF=\".*\"|DEFAULT_INSTALLER_REF=\"$ref\"|" \
        -e "s|^DEFAULT_TARBALL_SHA256=\".*\"|DEFAULT_TARBALL_SHA256=\"$asset_sha\"|" \
        installer.sh || return 1
    rm -f installer.sh.bak
    if ! grep -Fqx "INSTALLER_REF=\"\${LST_INSTALLER_REF:-$ref}\"" installer.sh \
        || ! grep -Fqx "DEFAULT_INSTALLER_REF=\"$ref\"" installer.sh \
        || ! grep -Fqx "DEFAULT_TARBALL_SHA256=\"$asset_sha\"" installer.sh; then
        log_error "installer.sh release-managed constants were not updated exactly"
        return 1
    fi

    if [[ -n $(git status --porcelain -- installer.sh) ]]; then
        git add -- installer.sh || return 1
        git commit -m "🔒 Pin installer release asset for $ref" || return 1
        git push origin "$branch" || return 1
        log_success "Pushed installer asset pin for $ref"
    else
        log_info "installer.sh already pins the verified $ref asset"
    fi
}

validate_bootstrap_commit() {
    local version="$1"
    local source_commit="$2"
    local commit="$3"
    local expected_sha tagged_installer changed_files commit_count subject
    if ! git merge-base --is-ancestor "$source_commit" "$commit"; then
        log_error "Bootstrap commit is not descended from v$version"
        return 1
    fi
    if ! git merge-base --is-ancestor "$commit" HEAD; then
        log_error "Bootstrap commit is not on the current main history"
        return 1
    fi
    commit_count="$(git rev-list --count "$source_commit..$commit")"
    changed_files="$(git diff --name-only "$source_commit" "$commit")"
    subject="$(git show -s --format=%s "$commit")"
    if [[ "$commit_count" != "1" \
        || "$changed_files" != "installer.sh" \
        || "$subject" != "🔒 Pin installer release asset for v$version" ]]; then
        log_error "Bootstrap commit must be the single installer-pin child of v$version"
        return 1
    fi
    expected_sha="$(grep -E '^DEFAULT_TARBALL_SHA256=' installer.sh | cut -d'"' -f2)"
    tagged_installer="$(git show "$commit:installer.sh" 2>/dev/null)" || return 1
    if ! grep -Fqx "DEFAULT_INSTALLER_REF=\"v$version\"" <<< "$tagged_installer" \
        || ! grep -Fqx "DEFAULT_TARBALL_SHA256=\"$expected_sha\"" <<< "$tagged_installer"; then
        log_error "Bootstrap commit does not contain the verified installer pin"
        return 1
    fi
    if ! git show "$commit:installer.sh" | cmp -s - installer.sh; then
        log_error "Bootstrap tag installer blob differs from current verified installer"
        return 1
    fi
}

create_bootstrap_tag() {
    local version="$1"
    local source_commit="$2"
    local bootstrap_ref="bootstrap-v$version"
    local remote_commit bootstrap_commit
    remote_commit="$(remote_tag_commit "$bootstrap_ref")" || return 1
    if [[ -n "$remote_commit" ]]; then
        validate_bootstrap_commit "$version" "$source_commit" "$remote_commit" || return 1
        log_info "Existing bootstrap tag $bootstrap_ref has verified provenance"
        return 0
    fi
    bootstrap_commit="$(git rev-parse HEAD)"
    validate_bootstrap_commit "$version" "$source_commit" "$bootstrap_commit" || return 1
    ensure_remote_annotated_tag \
        "$bootstrap_ref" "$bootstrap_commit" \
        "Verified installer bootstrap for v$version" || return 1
    log_success "Bootstrap tag $bootstrap_ref is verified"
}

smoke_test_bootstrap() {
    local version="$1"
    local verified_asset="$2"
    local verified_sha="$3"
    local bootstrap_ref="bootstrap-v$version"
    local bootstrap_url="https://raw.githubusercontent.com/pablopda/linux-speech-tools/$bootstrap_ref/installer.sh"
    local smoke_dir installer_file attempt
    smoke_dir="$(mktemp -d)"
    installer_file="$smoke_dir/installer.sh"

    for attempt in $(seq 1 12); do
        if curl -fsSL "$bootstrap_url" -o "$installer_file"; then
            break
        fi
        log_warning "Bootstrap tag not readable yet (attempt $attempt/12)"
        sleep 5
    done
    if [[ ! -s "$installer_file" ]]; then
        rm -rf "$smoke_dir"
        log_error "Could not download $bootstrap_url"
        return 1
    fi

    # Draft release assets are intentionally not public. Verify that the exact
    # remote bootstrap tag contains the public post-publication defaults, then
    # exercise its real download/checksum/extraction path against the local
    # bytes whose digest was independently re-downloaded from the draft via gh.
    if ! grep -Fqx "INSTALLER_REF=\"\${LST_INSTALLER_REF:-v$version}\"" "$installer_file" \
        || ! grep -Fqx "DEFAULT_INSTALLER_REF=\"v$version\"" "$installer_file" \
        || ! grep -Fqx "DEFAULT_TARBALL_SHA256=\"$verified_sha\"" "$installer_file" \
        || ! grep -Fqx 'DEFAULT_TARBALL_URL="https://github.com/pablopda/linux-speech-tools/releases/download/${DEFAULT_INSTALLER_REF}/linux-speech-tools-${DEFAULT_INSTALLER_VERSION}.tar.gz"' "$installer_file"; then
        rm -rf "$smoke_dir"
        log_error "Remote bootstrap tag does not contain the expected asset URL/ref/SHA"
        return 1
    fi
    if ! LST_INSTALLER_REF="v$version" \
        LST_INSTALLER_TARBALL_URL="file://$(readlink -f "$verified_asset")" \
        LST_INSTALLER_SHA256="$verified_sha" \
        LST_SOURCE_DIR="$smoke_dir/source" \
        bash "$installer_file" --bootstrap-check; then
        rm -rf "$smoke_dir"
        log_error "Bootstrap end-to-end dry run failed for $bootstrap_ref"
        return 1
    fi
    rm -rf "$smoke_dir"
    log_success "Bootstrap asset download, checksum, extraction, and layout smoke passed"
}

publish_release() {
    local version="$1"
    local ref="v$version"
    local is_draft
    is_draft="$(gh release view "$ref" --json isDraft --jq .isDraft)" || return 1
    if [[ "$is_draft" == "true" ]]; then
        gh release edit "$ref" --verify-tag --draft=false || return 1
        log_success "Published GitHub Release $ref"
    else
        log_info "GitHub Release $ref is already published"
    fi
}

complete_bootstrap_phase() {
    local version="$1"
    local source_commit="$2"
    local branch="$3"
    local work_dir asset canonical_asset notes_file existing_bootstrap bootstrap_sha
    work_dir="$(mktemp -d)"
    notes_file="$work_dir/release-notes.md"
    canonical_asset="$work_dir/verified-release-asset.tar.gz"

    existing_bootstrap="$(remote_tag_commit "bootstrap-v$version")" || {
        rm -rf "$work_dir"
        return 1
    }
    bootstrap_sha=""
    if [[ -n "$existing_bootstrap" ]]; then
        bootstrap_sha="$(git show "$existing_bootstrap:installer.sh" 2>/dev/null \
            | sed -n 's/^DEFAULT_TARBALL_SHA256="\([0-9a-f]\{64\}\)"/\1/p')"
        if [[ -z "$bootstrap_sha" ]]; then
            rm -rf "$work_dir"
            log_error "Existing bootstrap tag has no valid pinned asset SHA256"
            return 1
        fi
        if ! validate_bootstrap_commit "$version" "$source_commit" "$existing_bootstrap"; then
            rm -rf "$work_dir"
            return 1
        fi
    fi

    if ! asset="$(build_release_source_asset "$version" "$source_commit" "$work_dir")" \
        || ! extract_release_notes "$version" "$source_commit" "$notes_file" \
        || ! ensure_release_asset \
            "$version" "$asset" "$notes_file" "$bootstrap_sha" "$canonical_asset"; then
        rm -rf "$work_dir"
        return 1
    fi

    if ! pin_installer_release_asset "$version" "$VERIFIED_ASSET_SHA" "$branch"; then
        rm -rf "$work_dir"
        return 1
    fi
    if ! create_bootstrap_tag "$version" "$source_commit" \
        || ! smoke_test_bootstrap "$version" "$VERIFIED_ASSET_FILE" "$VERIFIED_ASSET_SHA" \
        || ! publish_release "$version"; then
        rm -rf "$work_dir"
        return 1
    fi
    rm -rf "$work_dir"
}

create_release_tag() {
    local new_version="$1"

    log_info "Creating release tag v$new_version..."

    if [[ "$DRY_RUN" == true ]]; then
        log_info "Would create and push tag: v$new_version"
        log_info "Would build linux-speech-tools-$new_version.tar.gz from that exact tag"
        log_info "Would create a verified-tag draft release and verify its uploaded SHA256"
        log_info "Would pin the versioned release asset in installer.sh and push that commit"
        log_info "Would create bootstrap-v$new_version, run its download/checksum/extraction smoke, then publish"
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
    local candidate_files=(VERSION CHANGELOG.md pyproject.toml uv.lock installer.sh src/tts/say_read.py bin/say)
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

    local source_commit
    source_commit="$(git rev-parse HEAD)"
    ensure_local_annotated_tag \
        "v$new_version" "$source_commit" "Release v$new_version"
    if ! git push origin "$branch" \
        || ! ensure_remote_annotated_tag \
            "v$new_version" "$source_commit" "Release v$new_version"; then
        log_error "Source release push is incomplete. The local tag was preserved."
        log_error "Resume safely with: $0 --resume-bootstrap $new_version"
        return 1
    fi
    log_success "Source tag v$new_version is verified"

    if ! complete_bootstrap_phase "$new_version" "$source_commit" "$branch"; then
        log_error "Release v$new_version stopped before safe publication."
        log_error "After resolving the cause, resume with:"
        log_error "  $0 --resume-bootstrap $new_version"
        return 1
    fi
}

resume_bootstrap_release() {
    local version="$1"
    local branch source_ref source_commit remote_source local_source remote_main
    source_ref="v$version"
    branch="$(git branch --show-current)"

    if [[ "$branch" != "main" ]]; then
        log_error "Bootstrap resume must run from main (current: $branch)"
        return 1
    fi
    git fetch origin main
    remote_main="$(git rev-parse origin/main)"
    remote_source="$(remote_tag_commit "$source_ref")" || return 1
    local_source=""
    if git rev-parse -q --verify "refs/tags/$source_ref" >/dev/null; then
        local_source="$(git rev-list -n 1 "$source_ref")"
    fi
    if [[ -n "$remote_source" && -n "$local_source" \
        && "$remote_source" != "$local_source" ]]; then
        log_error "Local and remote $source_ref tags disagree"
        return 1
    fi
    source_commit="${remote_source:-$local_source}"
    if [[ -z "$source_commit" ]]; then
        log_error "No persisted local or remote source tag exists for $source_ref"
        return 1
    fi

    if [[ "$(git show "$source_commit:VERSION" 2>/dev/null || true)" != "$version" ]]; then
        log_error "$source_ref does not identify a release whose VERSION is $version"
        return 1
    fi
    if ! git merge-base --is-ancestor "$source_commit" HEAD; then
        log_error "$source_ref is not an ancestor of current main"
        return 1
    fi

    if [[ "$(git rev-parse HEAD)" != "$remote_main" ]]; then
        # Only two local-ahead recovery states are valid:
        # 1. the exact source-tag commit whose first branch push failed; or
        # 2. the single installer-pin child whose follow-up push failed.
        if ! git merge-base --is-ancestor "$remote_main" HEAD; then
            log_error "Local main has diverged from origin/main; refusing to resume"
            return 1
        fi
        if [[ -z "$remote_source" && "$(git rev-parse HEAD)" == "$source_commit" ]]; then
            log_info "Recovering the exact locally tagged source release commit"
        elif [[ -n "$remote_source" && "$remote_main" == "$source_commit" ]] \
            && validate_bootstrap_commit "$version" "$source_commit" "$(git rev-parse HEAD)"; then
            log_info "Recovering the exact installer-pin commit"
        else
            log_error "Local commits ahead of origin/main are not a recognized release recovery state"
            return 1
        fi
        git push origin "$branch"
        git fetch origin main
        remote_main="$(git rev-parse origin/main)"
        if [[ "$(git rev-parse HEAD)" != "$remote_main" ]]; then
            log_error "Could not fast-forward origin/main to the validated release state"
            return 1
        fi
    fi

    if [[ -z "$remote_source" ]]; then
        ensure_remote_annotated_tag "$source_ref" "$source_commit" "Release $source_ref"
    fi

    if ! complete_bootstrap_phase "$version" "$source_commit" "$branch"; then
        log_error "Bootstrap resume remains incomplete; no release was newly published."
        return 1
    fi
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
    echo "3. The versioned bootstrap was smoke-tested before publication:"
    echo "   https://raw.githubusercontent.com/pablopda/linux-speech-tools/bootstrap-v$version/installer.sh"
}

parse_arguments() {
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
            --resume-bootstrap)
                if [[ $# -lt 2 ]]; then
                    log_error "--resume-bootstrap requires X.Y.Z"
                    exit 1
                fi
                RESUME_VERSION="$2"
                validate_version "$RESUME_VERSION"
                shift 2
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

    if [[ -n "$RESUME_VERSION" ]]; then
        if [[ -n "$VERSION_TYPE" || -n "$SPECIFIC_VERSION" ]]; then
            log_error "--resume-bootstrap cannot be combined with a new version"
            exit 1
        fi
        if [[ "$DRY_RUN" == true ]]; then
            log_error "--resume-bootstrap cannot be combined with --dry-run"
            exit 1
        fi
        return
    fi

    if [[ -z "$VERSION_TYPE" && -z "$SPECIFIC_VERSION" ]]; then
        log_error "Version type or specific version required"
        show_usage
        exit 1
    fi

    if [[ -n "$VERSION_TYPE" && -n "$SPECIFIC_VERSION" ]]; then
        log_error "Cannot specify both version type and specific version"
        exit 1
    fi
}

# Main release process
main() {
    local current_version new_version

    parse_arguments "$@"

    echo "🚀 Linux Speech Tools Release Automation"
    echo "========================================"
    echo ""

    if [[ -n "$RESUME_VERSION" ]]; then
        log_info "Resuming release bootstrap for v$RESUME_VERSION"
        check_git_status
        run_tests
        if ! bash scripts/release/pre-release-check.sh; then
            log_error "Pre-release validation failed; refusing to resume publication"
            exit 1
        fi
        resume_bootstrap_release "$RESUME_VERSION"
        monitor_release "$RESUME_VERSION"
        return
    fi

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

    # Validate the actual release candidate after version and changelog
    # mutation, before any commit or tag is created.
    if [[ "$DRY_RUN" != true ]]; then
        log_info "Validating the mutated release candidate..."
        if ! bash "$pre_release_checker"; then
            log_error "Mutated release candidate validation failed"
            log_info "No release tag has been created. Fix the candidate and retry."
            exit 1
        fi
    fi
    create_release_tag "$new_version"

    if [[ "$DRY_RUN" != true ]]; then
        monitor_release "$new_version"
    else
        echo ""
        log_success "Dry run completed successfully"
        log_info "Run without --dry-run to perform actual release"
    fi
}

# Execute main only when invoked, allowing focused tests to source helpers.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
