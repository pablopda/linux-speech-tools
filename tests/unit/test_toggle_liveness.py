"""Hermetic behavioral checks for toggle teardown and completion evidence."""

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _function_prefix(path, marker):
    source = path.read_text(encoding="utf-8")
    return source.split(marker, 1)[0]


def _run_functions(prefix, body, tmp_path):
    calls = tmp_path / "calls"
    harness_path = tmp_path / "harness.sh"
    harness = f"""
sleep() {{ return 0; }}
kill() {{ printf 'kill %s\\n' "$*" >> "$CALLS_FILE"; return 0; }}
notify-send() {{ printf 'notify %s\\n' "$*" >> "$CALLS_FILE"; return 0; }}
{prefix}
{body}
"""
    harness_path.write_text(harness, encoding="utf-8")
    env = {
        **os.environ,
        "LST_PROJECT_ROOT": str(ROOT),
        "XDG_RUNTIME_DIR": str(tmp_path),
        "CALLS_FILE": str(calls),
    }
    result = subprocess.run(
        ["bash", str(harness_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, calls.read_text(encoding="utf-8") if calls.exists() else ""


FAKE_THREE_LEVEL_TREE = r"""
TOKEN_MODE=live
pid_is_ours() { return 0; }
pid_owned_by_current_user() { return 0; }
process_start_token() {
    if [ "$TOKEN_MODE" = "reused" ] && [ "$1" = "2" ]; then
        printf 'reused-2\n'
    else
        printf 'start-%s\n' "$1"
    fi
}
process_parent_pid() {
    if [ "$TOKEN_MODE" = "reused" ]; then
        printf '0\n'
        return
    fi
    case "$1" in
        2) printf '1\n' ;;
        3) printf '2\n' ;;
        *) printf '0\n' ;;
    esac
}
children_of() {
    case "$1" in
        1) printf '2\n' ;;
        2) printf '3\n' ;;
    esac
}
finalization_wait_polls() { printf '1\n'; }
"""


@pytest.mark.parametrize(
    ("launcher", "marker"),
    [
        ("talk2claude-faster-toggle", "# Parse side-effect-free commands"),
        ("lst-dictate", "validate_prompt_args()"),
    ],
)
def test_three_level_tree_is_signaled_deepest_first_and_forced_stop_fails(
    launcher, marker, tmp_path
):
    prefix = _function_prefix(ROOT / "bin" / launcher, marker)
    body = FAKE_THREE_LEVEL_TREE + r"""
stop_target="$(begin_stop 1)"
set +e
wait_for_finalize "$stop_target"
forced_status=$?
set -e
printf 'forced-status %s\n' "$forced_status" >> "$CALLS_FILE"
"""
    result, calls = _run_functions(prefix, body, tmp_path)
    assert result.returncode == 0, result.stderr
    for signal_name in ("INT", "TERM", "KILL"):
        deepest = calls.index(f"kill -{signal_name} 3")
        middle = calls.index(f"kill -{signal_name} 2")
        root = calls.index(f"kill -{signal_name} 1")
        assert deepest < middle < root
    assert "forced-status 1" in calls


@pytest.mark.parametrize(
    ("launcher", "marker"),
    [
        ("talk2claude-faster-toggle", "# Parse side-effect-free commands"),
        ("lst-dictate", "validate_prompt_args()"),
    ],
)
def test_pid_reuse_identity_is_never_signaled(launcher, marker, tmp_path):
    prefix = _function_prefix(ROOT / "bin" / launcher, marker)
    body = FAKE_THREE_LEVEL_TREE + r"""
stop_target="$(begin_stop 1)"
: > "$CALLS_FILE"
TOKEN_MODE=reused
set +e
wait_for_finalize "$stop_target"
forced_status=$?
set -e
printf 'forced-status %s\n' "$forced_status" >> "$CALLS_FILE"
"""
    result, calls = _run_functions(prefix, body, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "kill -TERM 2" not in calls
    assert "kill -KILL 2" not in calls
    assert "kill -TERM 3" in calls
    assert "kill -KILL 3" in calls
    assert "forced-status 1" in calls


@pytest.mark.parametrize(
    ("launcher", "marker"),
    [
        ("talk2claude-faster-toggle", "# Parse side-effect-free commands"),
        ("lst-dictate", "validate_prompt_args()"),
    ],
)
def test_term_only_forced_stop_still_returns_failure(launcher, marker, tmp_path):
    prefix = _function_prefix(ROOT / "bin" / launcher, marker)
    body = FAKE_THREE_LEVEL_TREE + r"""
ALIVE=1
process_start_token() {
    [ "$ALIVE" = "1" ] || return 1
    printf 'start-%s\n' "$1"
}
kill() {
    printf 'kill %s\n' "$*" >> "$CALLS_FILE"
    [ "$1" = "-TERM" ] && ALIVE=0
    return 0
}
stop_target="$(begin_stop 1)"
set +e
wait_for_finalize "$stop_target"
forced_status=$?
set -e
printf 'forced-status %s\n' "$forced_status" >> "$CALLS_FILE"
"""
    result, calls = _run_functions(prefix, body, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "kill -TERM" in calls
    assert "kill -KILL" not in calls
    assert "forced-status 1" in calls


@pytest.mark.parametrize(
    ("exit_code", "state"),
    [("1", "idle"), ("0", "error")],
)
def test_lst_dictate_error_evidence_is_not_announced_as_finalized(
    exit_code, state, tmp_path
):
    state_dir = tmp_path / "linux-speech-tools"
    state_dir.mkdir()
    (state_dir / "lst-dictate.exit").write_text(exit_code + "\n", encoding="utf-8")
    (state_dir / "lst-dictate.status.json").write_text(
        '{"state": "' + state + '"}\n', encoding="utf-8"
    )
    prefix = _function_prefix(ROOT / "bin" / "lst-dictate", "validate_prompt_args()")
    dead_records = r"""
pid_owned_by_current_user() { return 0; }
process_start_token() { printf 'reused-%s\n' "$1"; }
wait_for_finalize $'root 4242:start-4242\nproc 4242:start-4242' || true
"""
    result, calls = _run_functions(
        prefix,
        dead_records,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Developer Dictation Failed" in calls
    assert "Developer Dictation Finished" not in calls


def test_lst_dictate_success_evidence_is_announced_as_finalized(tmp_path):
    state_dir = tmp_path / "linux-speech-tools"
    state_dir.mkdir()
    (state_dir / "lst-dictate.exit").write_text("0\n", encoding="utf-8")
    (state_dir / "lst-dictate.status.json").write_text(
        '{"state": "idle"}\n', encoding="utf-8"
    )
    prefix = _function_prefix(ROOT / "bin" / "lst-dictate", "validate_prompt_args()")
    dead_records = r"""
pid_owned_by_current_user() { return 0; }
process_start_token() { printf 'reused-%s\n' "$1"; }
wait_for_finalize $'root 4242:start-4242\nproc 4242:start-4242'
"""
    result, calls = _run_functions(
        prefix,
        dead_records,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Developer Dictation Finished" in calls


def test_classic_error_status_is_not_announced_as_finalized(tmp_path):
    state_dir = tmp_path / "linux-speech-tools"
    state_dir.mkdir()
    (state_dir / "talk2claude-faster.status.json").write_text(
        '{"state": "error"}\n', encoding="utf-8"
    )
    prefix = _function_prefix(
        ROOT / "bin" / "talk2claude-faster-toggle",
        "# Parse side-effect-free commands",
    )
    body = r"""
pid_owned_by_current_user() { return 0; }
process_start_token() { printf 'reused-%s\n' "$1"; }
wait_for_finalize $'root 4242:start-4242\nproc 4242:start-4242' || true
"""
    result, calls = _run_functions(prefix, body, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Dictation Failed" in calls
    assert "Dictation Finished" not in calls


@pytest.mark.parametrize(
    ("launcher", "marker"),
    [
        ("talk2claude-faster-toggle", "# Parse side-effect-free commands"),
        ("lst-dictate", "validate_prompt_args()"),
    ],
)
def test_wait_budget_uses_clamped_session_timeouts(launcher, marker, tmp_path):
    prefix = _function_prefix(ROOT / "bin" / launcher, marker)
    body = r"""
STT_FINALIZE_LOCK_SECONDS=999
STT_TRANSCRIBE_TIMEOUT_SECONDS=999
printf 'polls %s\n' "$(finalization_wait_polls)" >> "$CALLS_FILE"
STT_FINALIZE_LOCK_SECONDS=nan
STT_TRANSCRIBE_TIMEOUT_SECONDS=-1
printf 'fallback-polls %s\n' "$(finalization_wait_polls)" >> "$CALLS_FILE"
"""
    result, calls = _run_functions(prefix, body, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "polls 1460" in calls
    assert "fallback-polls 180" in calls
