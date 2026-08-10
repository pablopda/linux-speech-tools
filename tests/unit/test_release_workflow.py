"""Release-workflow regressions for changelog and bootstrap safety."""

import os
import hashlib
import subprocess
import tarfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
RELEASE_SCRIPT = ROOT / "scripts/release/release.sh"


def _run_changelog(tmp_path: Path, text: str):
    (tmp_path / "CHANGELOG.md").write_text(text, encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q"], cwd=tmp_path, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env = dict(os.environ)
    env["LST_RELEASE_SCRIPT_UNDER_TEST"] = str(RELEASE_SCRIPT)
    command = r'''
target_dir="$1"
set --
source "$LST_RELEASE_SCRIPT_UNDER_TEST"
cd "$target_dir"
DRY_RUN=false
generate_changelog 1.1.0 1.0.2
'''
    return subprocess.run(
        ["bash", "-c", command, "bash", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_changelog_promotes_unreleased_without_duplicate_heading(tmp_path):
    original = """# Changelog

Project history.

## [Unreleased]

### Added

- Safe insertion.

## [v1.0.2] - 2025-11-12

- Previous release.

[Unreleased]: https://github.com/pablopda/linux-speech-tools/compare/v1.0.2...HEAD
[v1.0.2]: https://github.com/pablopda/linux-speech-tools/releases/tag/v1.0.2
"""

    completed = _run_changelog(tmp_path, original)

    assert completed.returncode == 0, completed.stderr
    updated = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert updated.count("# Changelog") == 1
    assert updated.count("## [Unreleased]") == 1
    assert updated.count("## [v1.1.0]") == 1
    assert updated.index("## [Unreleased]") < updated.index("## [v1.1.0]")
    assert updated.index("## [v1.1.0]") < updated.index("### Added")
    assert "compare/v1.1.0...HEAD" in updated
    assert "compare/v1.0.2...v1.1.0" in updated

    repeated = _run_changelog(tmp_path, updated)

    assert repeated.returncode == 0, repeated.stderr
    unchanged = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert unchanged == updated
    assert unchanged.count("## [v1.1.0]") == 1
    assert "already contains the prepared v1.1.0 candidate" in repeated.stdout


def test_changelog_refuses_unstructured_input(tmp_path):
    completed = _run_changelog(tmp_path, "# Changelog\n\nNo release section.\n")

    assert completed.returncode != 0
    assert "must contain exactly one" in completed.stdout + completed.stderr


def test_version_update_regenerates_locked_project_version(tmp_path):
    project = tmp_path / "project"
    (project / "src/tts").mkdir(parents=True)
    (project / "bin").mkdir()
    (project / "VERSION").write_text("9.8.7\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "linux-speech-tools"\n'
        'version = "9.8.7"\n'
        'requires-python = ">=3.8"\n'
        "dependencies = []\n",
        encoding="utf-8",
    )
    (project / "src/tts/say_read.py").write_text(
        '__version__ = "9.8.7"\n', encoding="utf-8"
    )
    (project / "bin/say").write_text('VERSION="9.8.7"\n', encoding="utf-8")
    subprocess.run(["uv", "lock"], cwd=project, check=True, capture_output=True)

    completed = _source_release(
        project,
        "DRY_RUN=false; update_version_in_files 9.8.8",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (project / "VERSION").read_text().strip() == "9.8.8"
    assert 'version = "9.8.8"' in (project / "pyproject.toml").read_text()
    lock = (project / "uv.lock").read_text(encoding="utf-8")
    project_entry = lock.split('name = "linux-speech-tools"', 1)[1]
    assert 'version = "9.8.8"' in project_entry.split("[[package]]", 1)[0]
    subprocess.run(
        ["uv", "lock", "--check"], cwd=project, check=True, capture_output=True
    )


def test_release_uses_later_versioned_bootstrap_tag_and_post_mutation_gate():
    script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    create_release = script.index("create_release_tag()")
    source_tag = script.index("ensure_remote_annotated_tag", create_release)
    asset_phase = script.index(
        'complete_bootstrap_phase "$new_version" "$source_commit" "$branch"',
        source_tag,
    )
    generate = script.index('generate_changelog "$new_version" "$current_version"')
    mutated_gate = script.index("Validating the mutated release candidate", generate)
    tag_call = script.index('create_release_tag "$new_version"', mutated_gate)

    assert source_tag < asset_phase
    assert generate < mutated_gate < tag_call
    assert "bootstrap-v$new_version" in script
    assert "--resume-bootstrap $new_version" in script
    assert "gh release create" in script
    assert "--verify-tag --draft" in script
    assert "gh release edit" in script
    assert "smoke_test_bootstrap" in script
    assert "archive/refs/tags" not in script
    assert "raw.githubusercontent.com/pablopda/linux-speech-tools/main/installer.sh | bash" not in script


def test_readme_does_not_advertise_obsolete_v1_0_2_streamed_installer():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "v1.0.2/installer.sh | bash" not in readme
    assert "currently no supported `curl | bash` command" in readme
    assert "bootstrap-vX.Y.Z" in readme


def _init_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "VERSION").write_text("9.8.7\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [v9.8.7] - 2026-08-10\n\n- Test.\n",
        encoding="utf-8",
    )
    (repo / "installer.sh").write_text(
        "#!/usr/bin/env bash\n"
        'INSTALLER_REF="${LST_INSTALLER_REF:-v9.8.7}"\n'
        'DEFAULT_INSTALLER_REF="v9.8.7"\n'
        f'DEFAULT_TARBALL_SHA256="{"0" * 64}"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "release"], cwd=repo, check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=repo, check=True)
    return repo, remote


def _source_release(repo: Path, body: str):
    env = dict(os.environ)
    env["LST_RELEASE_SCRIPT_UNDER_TEST"] = str(RELEASE_SCRIPT)
    command = r'''
source "$LST_RELEASE_SCRIPT_UNDER_TEST"
cd "$1"
shift
''' + body
    return subprocess.run(
        ["bash", "-c", command, "bash", str(repo)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_remote_tag_creation_is_idempotent_and_rejects_moved_target(tmp_path):
    repo, remote = _init_repo(tmp_path)
    completed = _source_release(
        repo,
        r'''
commit="$(git rev-parse HEAD)"
ensure_remote_annotated_tag v9.8.7 "$commit" "Release v9.8.7"
ensure_remote_annotated_tag v9.8.7 "$commit" "Release v9.8.7"
''',
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    peeled = subprocess.run(
        ["git", "ls-remote", "--tags", str(remote), "refs/tags/v9.8.7^{}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert peeled == expected

    (repo / "VERSION").write_text("9.8.7\n# changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "VERSION"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "later"], cwd=repo, check=True)
    conflict = _source_release(
        repo,
        'ensure_remote_annotated_tag v9.8.7 "$(git rev-parse HEAD)" "moved"',
    )
    assert conflict.returncode != 0
    assert "expected" in conflict.stdout + conflict.stderr


def test_source_asset_is_versioned_and_sha_matches_exact_bytes(tmp_path):
    repo, _ = _init_repo(tmp_path)
    output = tmp_path / "assets"
    output.mkdir()
    completed = _source_release(
        repo,
        f'build_release_source_asset 9.8.7 "$(git rev-parse HEAD)" "{output}"',
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    asset = output / "linux-speech-tools-9.8.7.tar.gz"
    digest = subprocess.run(
        ["sha256sum", str(asset)], capture_output=True, text=True, check=True,
    ).stdout.split()[0]
    assert asset.is_file()
    sha_line = (output / f"{asset.name}.sha256").read_text().split()
    assert sha_line == [digest, asset.name]


def test_resume_mode_and_dry_run_preview_complete_bootstrap():
    env = dict(os.environ)
    env["LST_RELEASE_SCRIPT_UNDER_TEST"] = str(RELEASE_SCRIPT)
    resume = subprocess.run(
        ["bash", "-c", 'source "$LST_RELEASE_SCRIPT_UNDER_TEST"; parse_arguments --resume-bootstrap 9.8.7; printf "%s" "$RESUME_VERSION"'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert resume.returncode == 0
    assert resume.stdout == "9.8.7"

    preview = subprocess.run(
        ["bash", "-c", 'source "$LST_RELEASE_SCRIPT_UNDER_TEST"; DRY_RUN=true; create_release_tag 9.8.7'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert preview.returncode == 0
    assert "verified-tag draft release" in preview.stdout
    assert "bootstrap-v9.8.7" in preview.stdout
    assert "download/checksum/extraction smoke" in preview.stdout


def test_source_tag_is_persisted_before_first_branch_push():
    script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    create = script.index("create_release_tag()")
    local_tag = script.index("ensure_local_annotated_tag", create)
    branch_push = script.index('git push origin "$branch"', local_tag)
    remote_tag = script.index("ensure_remote_annotated_tag", branch_push)

    assert local_tag < branch_push < remote_tag


def test_resume_rejects_unrelated_local_commit_without_pushing(tmp_path):
    repo, remote = _init_repo(tmp_path)
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    tagged = _source_release(
        repo,
        f'ensure_remote_annotated_tag v9.8.7 "{source_commit}" "Release v9.8.7"',
    )
    assert tagged.returncode == 0, tagged.stdout + tagged.stderr
    (repo / "unrelated.txt").write_text("not release state\n", encoding="utf-8")
    subprocess.run(["git", "add", "unrelated.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated"], cwd=repo, check=True)

    completed = _source_release(
        repo,
        'complete_bootstrap_phase() { return 0; }; resume_bootstrap_release 9.8.7',
    )

    assert completed.returncode != 0
    assert "not a recognized release recovery state" in completed.stdout + completed.stderr
    remote_main = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert remote_main == source_commit


def test_resume_recovers_exact_locally_tagged_source_commit(tmp_path):
    repo, remote = _init_repo(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [v9.8.7] - 2026-08-10\n\n- Release candidate.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "CHANGELOG.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "release candidate"], cwd=repo, check=True)
    release_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "tag", "-a", "v9.8.7", release_commit, "-m", "Release v9.8.7"],
        cwd=repo, check=True,
    )

    completed = _source_release(
        repo,
        'complete_bootstrap_phase() { return 0; }; resume_bootstrap_release 9.8.7',
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    remote_main = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert remote_main == release_commit
    assert remote_main != base
    remote_tag = subprocess.run(
        ["git", "ls-remote", "--tags", str(remote), "refs/tags/v9.8.7^{}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()[0]
    assert remote_tag == release_commit


def test_bootstrap_provenance_rejects_sibling_commit_with_same_pin(tmp_path):
    repo, _ = _init_repo(tmp_path)
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    installer = repo / "installer.sh"
    installer.write_text(installer.read_text() + "# implementation A\n", encoding="utf-8")
    subprocess.run(["git", "add", "installer.sh"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "🔒 Pin installer release asset for v9.8.7"],
        cwd=repo, check=True,
    )
    first_pin = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "switch", "-q", "--detach", source_commit], cwd=repo, check=True)
    subprocess.run(["git", "switch", "-q", "-c", "sibling-pin"], cwd=repo, check=True)
    installer.write_text(installer.read_text() + "# implementation B\n", encoding="utf-8")
    subprocess.run(["git", "add", "installer.sh"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "🔒 Pin installer release asset for v9.8.7"],
        cwd=repo, check=True,
    )

    completed = _source_release(
        repo,
        f'validate_bootstrap_commit 9.8.7 "{source_commit}" "{first_pin}"',
    )

    assert completed.returncode != 0
    assert "not on the current main history" in completed.stdout + completed.stderr


def test_github_workflow_cannot_bypass_release_gate_and_ships_gnome_payload():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(workflow)
    jobs = parsed["jobs"]

    assert "workflow_dispatch:" not in workflow
    assert "gh release create" not in workflow
    assert "raw.githubusercontent.com" not in workflow
    assert "ref: ${{ github.ref }}" in workflow
    assert workflow.count("gh release upload") == 1
    assert "gnome-extension/" in workflow
    assert "--clobber" not in workflow
    assert parsed["permissions"]["contents"] == "read"
    assert jobs["validate"]["outputs"]["source_commit"] == (
        "${{ steps.version.outputs.source_commit }}"
    )
    assert jobs["upload-packages"]["permissions"]["contents"] == "write"
    assert jobs["upload-packages"]["needs"] == [
        "validate", "build-packages", "test-deb-package", "test-rpm-package"
    ]
    assert "actions/upload-artifact@v4" in workflow
    assert workflow.count("actions/download-artifact@v4") >= 4
    assert "gh release upload" not in str(jobs["build-packages"])
    for job_name, expected_ref in (
        ("validate", "${{ github.ref }}"),
        ("build-packages", "${{ needs.validate.outputs.source_commit }}"),
    ):
        checkout = next(
            step for step in jobs[job_name]["steps"]
            if step.get("uses") == "actions/checkout@v4"
        )
        assert checkout["with"]["ref"] == expected_ref
        assert checkout["with"]["persist-credentials"] is False
    build_artifact = next(
        step for step in jobs["build-packages"]["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    )
    assert build_artifact["with"]["name"] == "release-package-${{ matrix.package_type }}"
    assert build_artifact["with"]["if-no-files-found"] == "error"
    for job_name, artifact_name in (
        ("test-deb-package", "release-package-deb"),
        ("test-rpm-package", "release-package-rpm"),
    ):
        assert any(
            step.get("uses") == "actions/download-artifact@v4"
            and step["with"]["name"] == artifact_name
            for step in jobs[job_name]["steps"]
        )
        rendered_job = str(jobs[job_name])
        assert "/home/lst-release-test/.local/bin/linux-speech-tools-setup" in rendered_job
        assert "/usr/share/linux-speech-tools/bin/linux-speech-tools-setup" in rendered_job
        assert "/usr/share/linux-speech-tools/requirements-faster.txt" in rendered_job
        assert "/usr/share/linux-speech-tools/install-faster.sh" in rendered_job
    upload_job = str(jobs["upload-packages"])
    assert "release-package-deb" in upload_job
    assert "release-package-rpm" in upload_job
    assert "Refusing to replace existing release asset" in upload_job
    upload_script = next(
        step["run"] for step in jobs["upload-packages"]["steps"]
        if "run" in step
    )
    assert "linux-speech-tools-${VERSION#v}-native-packages.tar.gz" in upload_script
    assert "SHA256SUMS" in upload_script
    assert "sha256sum -c SHA256SUMS" in upload_script
    assert "--sort=name --mtime='@0'" in upload_script
    assert "--owner=0 --group=0 --numeric-owner" in upload_script
    assert "gzip -n" in upload_script
    assert 'cmp -- "${deb_packages[0]}"' in upload_script
    assert 'cmp -- "${rpm_packages[0]}"' in upload_script
    upload_lines = [
        line.strip() for line in workflow.splitlines()
        if line.strip().startswith("gh release upload")
    ]
    assert upload_lines == ['gh release upload "$VERSION" "$bundle_name"']

    build_steps = "\n".join(
        step.get("run", "") for step in jobs["build-packages"]["steps"]
    )
    assert (
        "test ! -f requirements-faster.txt || cp requirements-faster.txt "
        "%{buildroot}/usr/share/%{name}/"
    ) in build_steps
    assert (
        "test ! -f install-faster.sh || cp install-faster.sh "
        "%{buildroot}/usr/share/%{name}/"
    ) in build_steps


def test_release_native_bundle_recipe_is_deterministic_and_uploads_one_asset(
    tmp_path,
):
    parsed = yaml.safe_load(
        (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    )
    upload_script = next(
        step["run"] for step in parsed["jobs"]["upload-packages"]["steps"]
        if "run" in step
    )

    def run_recipe(run_root):
        packages = run_root / "packages"
        fake_bin = run_root / "fake-bin"
        packages.mkdir(parents=True)
        fake_bin.mkdir()
        deb = packages / "linux-speech-tools_1.1.0_all.deb"
        rpm = packages / "linux-speech-tools-1.1.0-1.noarch.rpm"
        deb.write_bytes(b"exact deb bytes\n")
        rpm.write_bytes(b"exact rpm bytes\n")
        gh_log = run_root / "gh-upload-argv"
        captured = run_root / "uploaded-bundle.tar.gz"
        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "release view" ]]; then
    exit 0
fi
if [[ "$1 $2" == "release upload" ]]; then
    printf '%s\\n' "$@" > "$GH_LOG"
    cp -- "$4" "$GH_CAPTURE"
    exit 0
fi
exit 99
""",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "GH_CAPTURE": str(captured),
                "GH_LOG": str(gh_log),
                "PATH": "{}:{}".format(fake_bin, env["PATH"]),
                "VERSION": "v1.1.0",
            }
        )

        completed = subprocess.run(
            ["bash", "-c", upload_script],
            cwd=run_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert gh_log.read_text(encoding="utf-8").splitlines() == [
            "release",
            "upload",
            "v1.1.0",
            "linux-speech-tools-1.1.0-native-packages.tar.gz",
        ]
        with tarfile.open(captured, "r:gz") as archive:
            files = {}
            for member in archive.getmembers():
                if member.isfile():
                    name = member.name[2:] if member.name.startswith("./") else member.name
                    files[name] = archive.extractfile(member).read()
        assert set(files) == {deb.name, rpm.name, "SHA256SUMS"}
        expected_sums = (
            "{}  {}\n{}  {}\n".format(
                hashlib.sha256(deb.read_bytes()).hexdigest(),
                deb.name,
                hashlib.sha256(rpm.read_bytes()).hexdigest(),
                rpm.name,
            )
        ).encode("ascii")
        assert files["SHA256SUMS"] == expected_sums
        return captured.read_bytes()

    first = run_recipe(tmp_path / "first")
    second = run_recipe(tmp_path / "second")
    assert first == second


def test_release_gate_tracks_lock_and_native_packages_test_non_root_runtime():
    release_script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    checker = (ROOT / "scripts/release/pre-release-check.sh").read_text(
        encoding="utf-8"
    )
    release_workflow = (ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    package_test = (ROOT / ".github/workflows/package-test.yml").read_text(
        encoding="utf-8"
    )

    assert "pyproject.toml uv.lock installer.sh" in release_script
    assert "uv lock" in release_script
    assert "uv lock --check" in checker
    assert "uv.lock project version mismatch" in checker
    assert "curl, ca-certificates" in release_workflow
    assert "/usr/share/linux-speech-tools/installer.sh" in release_workflow
    assert "never writes under /usr/share" in release_workflow
    assert "cp -r bin/ scripts/ src/ gnome-extension/" in release_workflow
    assert "cp -r bin scripts src gnome-extension" in release_workflow
    assert "runuser -u lst-package-test" in package_test
    assert "test ! -e /usr/share/linux-speech-tools/.venv" in package_test
    assert "UV_PROJECT_ENVIRONMENT=/home/lst-package-test/" in package_test
    assert "/home/lst-package-test/.local/bin/linux-speech-tools-setup --help" in package_test
    assert "test -f /usr/share/linux-speech-tools/uv.lock" in package_test
    assert package_test.count(
        "test -f /usr/share/linux-speech-tools/requirements-faster.txt"
    ) == 2
    assert package_test.count(
        "test -f /usr/share/linux-speech-tools/install-faster.sh"
    ) == 2
    assert "cp -r bin/" in package_test
    assert '|| rpm -i "./$RPM_FILE"' not in package_test
    for trigger in (
        "'src/**'", "'gnome-extension/**'", "'scripts/**'", "'VERSION'",
        "'uv.lock'", "'install-faster.sh'", "'requirements-faster.txt'",
        "'README.md'", "'README_FASTER.md'", "'docs/**'",
    ):
        assert trigger in package_test
    assert "dependencies may need manual setup" not in package_test

    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "types: [ published ]" not in ci_workflow
    assert "actions/upload-release-asset" not in ci_workflow

    for workflow_text in (package_test, ci_workflow):
        parsed_workflow = yaml.safe_load(workflow_text)
        assert parsed_workflow["permissions"]["contents"] == "read"
        checkouts = [
            step
            for job in parsed_workflow["jobs"].values()
            for step in job.get("steps", [])
            if step.get("uses") == "actions/checkout@v4"
        ]
        assert checkouts
        assert all(
            step.get("with", {}).get("persist-credentials") is False
            for step in checkouts
        )


def test_release_test_workflow_uses_locked_uv_and_exact_version_sources():
    workflow_path = ROOT / ".github/workflows/release-test.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)

    assert workflow["jobs"]["test-release-script"]["permissions"]["contents"] == "read"
    assert workflow["jobs"]["test-version-consistency"]["permissions"]["contents"] == "read"
    assert workflow_text.count("uses: astral-sh/setup-uv@v6") == 2
    assert "uv sync --locked --extra dev" in workflow_text
    assert "uv lock --check" in workflow_text
    assert "grep \"VERSION=\" installer.sh | head -1" not in workflow_text
    assert "^DEFAULT_INSTALLER_REF=" in workflow_text
    for trigger in (
        "'installer.sh'",
        "'pyproject.toml'",
        "'uv.lock'",
        "'bin/**'",
        "'src/**'",
        "'tests/**'",
    ):
        assert trigger in workflow_text

    checkouts = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses") == "actions/checkout@v4"
    ]
    assert checkouts
    assert all(
        step.get("with", {}).get("persist-credentials") is False
        for step in checkouts
    )


def test_installer_defaults_to_versioned_release_asset():
    installer = (ROOT / "installer.sh").read_text(encoding="utf-8")

    assert "releases/download/${DEFAULT_INSTALLER_REF}" in installer
    assert "releases/download/${INSTALLER_REF}" in installer
    assert "archive/refs/tags" not in installer


def test_bootstrap_check_downloads_verifies_and_extracts_without_installing(tmp_path):
    package = tmp_path / "linux-speech-tools-9.8.7"
    uv_installer = package / "scripts/install/install-with-uv.sh"
    uv_installer.parent.mkdir(parents=True)
    (package / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (package / "installer.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    uv_installer.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    uv_installer.chmod(0o755)
    asset = tmp_path / "linux-speech-tools-9.8.7.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        archive.add(package, arcname=package.name)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    source_dir = tmp_path / "source-target/source"
    env = dict(os.environ)
    env.update(
        {
            "LST_INSTALLER_REF": "v9.8.7",
            "LST_INSTALLER_TARBALL_URL": asset.resolve().as_uri(),
            "LST_INSTALLER_SHA256": digest,
            "LST_SOURCE_DIR": str(source_dir),
        }
    )

    completed = subprocess.run(
        ["bash", str(ROOT / "installer.sh"), "--bootstrap-check"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "download, SHA256, extraction, and layout check passed" in completed.stdout
    assert not source_dir.exists()


def test_existing_bootstrap_uses_downloaded_canonical_asset_not_local_rebuild(tmp_path):
    repo, _ = _init_repo(tmp_path)
    canonical = tmp_path / "canonical.tar.gz"
    rebuilt = tmp_path / "rebuilt.tar.gz"
    notes = tmp_path / "notes.md"
    staged = tmp_path / "staged-canonical.tar.gz"
    uploads = tmp_path / "uploads.log"
    canonical.write_bytes(b"canonical release bytes")
    rebuilt.write_bytes(b"different cross-toolchain rebuild")
    notes.write_text("notes\n", encoding="utf-8")
    digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
    env = dict(os.environ)
    env.update(
        {
            "LST_RELEASE_SCRIPT_UNDER_TEST": str(RELEASE_SCRIPT),
            "CANONICAL_ASSET": str(canonical),
            "UPLOAD_LOG": str(uploads),
        }
    )
    command = r'''
source "$LST_RELEASE_SCRIPT_UNDER_TEST"
cd "$1"
gh() {
    if [ "$1 $2" = "release view" ]; then
        case "$*" in
            *tagName*) printf 'v9.8.7\n' ;;
            *isDraft*) printf 'true\n' ;;
        esac
        return 0
    fi
    if [ "$1 $2" = "release download" ]; then
        shift 3
        output=""
        while [ "$#" -gt 0 ]; do
            if [ "$1" = "--output" ]; then output="$2"; shift 2; else shift; fi
        done
        cp "$CANONICAL_ASSET" "$output"
        return 0
    fi
    if [ "$1 $2" = "release upload" ]; then
        printf 'unexpected upload\n' >> "$UPLOAD_LOG"
        return 1
    fi
    return 1
}
ensure_release_asset 9.8.7 "$2" "$3" "$4" "$5"
printf '%s\n%s\n' "$VERIFIED_ASSET_SHA" "$VERIFIED_ASSET_FILE"
'''
    completed = subprocess.run(
        [
            "bash", "-c", command, "bash", str(repo), str(rebuilt),
            str(notes), digest, str(staged),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert staged.read_bytes() == canonical.read_bytes()
    assert rebuilt.read_bytes() != staged.read_bytes()
    assert digest in completed.stdout
    assert str(staged) in completed.stdout
    assert not uploads.exists()
