import datetime as dt
import json
import multiprocessing
import os
import stat
from types import SimpleNamespace
from unittest import mock

import pytest

from src.stt.insertion_metrics import (
    MAX_DAYS,
    InsertionMetricsStore,
    UnsafeMetricsPath,
    default_state_path,
    main,
    record_insertion_result,
    render_report,
    report_data,
)
from src.stt.insertion_session import InsertionSession, InsertionState, result


def _record_many(path, count):
    store = InsertionMetricsStore(path)
    for _ in range(count):
        store.record(
            backend="synthetic-paste",
            result_category="dispatched-unconfirmed",
            target_kind="codex",
        )


def _record_eligible(
    store,
    *,
    result_category="dispatched-unconfirmed",
    target_kind="codex",
    backend="ydotool",
    day=None,
):
    return store.record(
        backend=backend,
        attempted_backend="synthetic-paste",
        result_category=result_category,
        target_kind=target_kind,
        eligible_supported_gnome_direct=True,
        day=day,
    )


def test_store_records_only_closed_categories_and_private_files(tmp_path):
    state_path = tmp_path / "private" / "metrics.json"
    store = InsertionMetricsStore(state_path)
    operation = SimpleNamespace(
        state=InsertionState.AMBIGUOUS_AFTER_DISPATCH,
        backend="/secret/repository\n|window-title",
        diagnostic="private transcript and window token",
        session_id="private-session-id",
        target_token_match=False,
    )

    assert record_insertion_result(
        operation,
        target_kind="private.application.identifier",
        attempted_backend="private-attempted-backend",
        focus_drift=True,
        store=store,
    )

    raw = state_path.read_text(encoding="utf-8")
    for secret in (
        "secret/repository",
        "window-title",
        "private transcript",
        "window token",
        "private-session-id",
        "private.application.identifier",
        "private-attempted-backend",
    ):
        assert secret not in raw
    state = json.loads(raw)
    bucket = next(iter(state["days"].values()))
    assert bucket["backends"] == {"other": 1}
    assert bucket["target_kinds"] == {"unknown": 1}
    assert bucket["focus_drift"] == 1
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600

    state_path.chmod(0o644)
    store.snapshot()
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_corrupt_and_oversized_state_fail_closed_without_preserving_text(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text('{"transcript":"do not preserve this"', encoding="utf-8")
    store = InsertionMetricsStore(path)

    assert not store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    assert "do not preserve this" not in path.read_text(encoding="utf-8")
    assert report_data(store)["totals"]["sessions"] == 0
    assert store.snapshot()["enabled"] is False
    assert store.disabled_path.read_text(encoding="utf-8") == "disabled\n"
    assert stat.S_IMODE(store.disabled_path.stat().st_mode) == 0o600

    store.configure(True)
    path.write_text("private transcript" * 20000, encoding="utf-8")
    assert not store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    snapshot = store.snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["days"] == {}
    assert "private transcript" not in path.read_text(encoding="utf-8")


def test_persistent_disable_survives_corrupt_state(tmp_path):
    path = tmp_path / "metrics.json"
    store = InsertionMetricsStore(path)
    store.configure(False)
    path.write_text('{"enabled":true,"transcript":"private"', encoding="utf-8")

    assert not store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    assert store.snapshot()["enabled"] is False
    assert store.disabled_path.exists()
    assert "private" not in path.read_text(encoding="utf-8")


def test_process_concurrency_does_not_drop_updates(tmp_path):
    path = tmp_path / "metrics.json"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_record_many, args=(path, 20)) for _ in range(6)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    data = report_data(InsertionMetricsStore(path))
    assert data["totals"]["sessions"] == 120
    assert data["totals"]["backends"]["synthetic-paste"] == 120


def test_environment_and_persistent_opt_out_and_reset(tmp_path, monkeypatch):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    monkeypatch.setenv("LST_INSERTION_METRICS", "off")
    assert not store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    assert not store.effective_enabled()
    assert store.snapshot()["days"] == {}

    monkeypatch.delenv("LST_INSERTION_METRICS")
    store.configure(False)
    assert not store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    store.configure(True)
    assert store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    store.reset()
    snapshot = store.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["days"] == {}


def test_state_paths_reject_symlinks_fifos_and_symlink_parents(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("leave me alone", encoding="utf-8")
    victim.chmod(0o644)

    lock_store = InsertionMetricsStore(tmp_path / "lock-state.json")
    lock_store.lock_path.symlink_to(victim)
    with pytest.raises(UnsafeMetricsPath):
        lock_store.snapshot()
    assert victim.read_text(encoding="utf-8") == "leave me alone"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644

    state_link = tmp_path / "linked-state.json"
    state_link.symlink_to(victim)
    with pytest.raises(UnsafeMetricsPath):
        InsertionMetricsStore(state_link).snapshot()
    assert victim.read_text(encoding="utf-8") == "leave me alone"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644

    fifo_path = tmp_path / "fifo-state.json"
    os.mkfifo(fifo_path)
    with pytest.raises(UnsafeMetricsPath):
        InsertionMetricsStore(fifo_path).snapshot()

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(UnsafeMetricsPath):
        InsertionMetricsStore(linked_parent / "metrics.json").snapshot()


def test_disable_marker_symlink_fails_closed_without_following_target(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    victim = tmp_path / "victim.txt"
    victim.write_text("not a preference", encoding="utf-8")
    victim.chmod(0o644)
    store.disabled_path.symlink_to(victim)

    assert store.snapshot()["enabled"] is False
    assert not store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    assert victim.read_text(encoding="utf-8") == "not a preference"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_report_paths_reject_symlinks_and_fifos(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    victim = tmp_path / "victim.md"
    victim.write_text("keep report", encoding="utf-8")
    victim.chmod(0o644)
    report_link = tmp_path / "report-link.md"
    report_link.symlink_to(victim)

    with pytest.raises(UnsafeMetricsPath):
        main(["report", "--output", str(report_link)], store=store)
    assert victim.read_text(encoding="utf-8") == "keep report"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644

    report_fifo = tmp_path / "report.fifo"
    os.mkfifo(report_fifo)
    with pytest.raises(UnsafeMetricsPath):
        main(["report", "--output", str(report_fifo)], store=store)


@pytest.mark.parametrize("mode", (0o770, 0o777))
def test_writable_state_parent_cannot_erase_persistent_opt_out(tmp_path, mode):
    parent = tmp_path / "writable-state"
    parent.mkdir(mode=0o700)
    store = InsertionMetricsStore(parent / "metrics.json")
    store.configure(False)

    # Model a writer to the containing directory removing every protected
    # final component. The store must not recreate enabled state there.
    parent.chmod(mode)
    store.path.unlink()
    store.lock_path.unlink()
    store.disabled_path.unlink()
    with pytest.raises(UnsafeMetricsPath, match="group- or world-writable"):
        store.record(
            backend="clipboard",
            result_category="clipboard-fallback",
            target_kind="terminal",
        )

    assert stat.S_IMODE(parent.stat().st_mode) == mode
    assert not store.path.exists()
    assert not store.lock_path.exists()
    assert not store.disabled_path.exists()


@pytest.mark.parametrize("mode", (0o770, 0o777))
def test_report_rejects_writable_existing_parent_without_chmod(tmp_path, mode):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    report_parent = tmp_path / "writable-reports"
    report_parent.mkdir(mode=0o700)
    report_parent.chmod(mode)
    report_path = report_parent / "report.md"

    with pytest.raises(UnsafeMetricsPath, match="group- or world-writable"):
        main(["report", "--output", str(report_path)], store=store)

    assert stat.S_IMODE(report_parent.stat().st_mode) == mode
    assert not report_path.exists()


def test_existing_override_parent_mode_is_not_changed(tmp_path):
    parent = tmp_path / "shared-existing"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    store = InsertionMetricsStore(parent / "metrics.json")

    assert store.record(
        backend="stdout",
        result_category="non-inserting",
        target_kind="generic",
    )
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    report_path = parent / "report.md"
    assert main(["report", "--output", str(report_path)], store=store) == 0
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("reserved_name", ("path", "lock_path", "disabled_path"))
def test_report_rejects_reserved_state_path_collisions_before_snapshot(
    tmp_path, reserved_name
):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    output = getattr(store, reserved_name)

    with mock.patch("src.stt.insertion_metrics.report_data") as reporter:
        with pytest.raises(UnsafeMetricsPath, match="reserved state path"):
            main(["report", "--output", str(output)], store=store)
    reporter.assert_not_called()
    assert not store.path.exists()
    assert not store.lock_path.exists()
    assert not store.disabled_path.exists()


def test_report_collision_comparison_normalizes_relative_dot_segments(
    tmp_path, monkeypatch
):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    monkeypatch.chdir(tmp_path)

    with mock.patch("src.stt.insertion_metrics.report_data") as reporter:
        with pytest.raises(UnsafeMetricsPath, match="reserved state path"):
            main(
                ["report", "--output", "nonexistent/../metrics.json"],
                store=store,
            )
    reporter.assert_not_called()
    assert not store.path.exists()


def test_default_state_path_ignores_relative_xdg_and_rejects_relative_override(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
    monkeypatch.delenv("LST_INSERTION_METRICS_FILE", raising=False)
    assert default_state_path() == (
        fake_home
        / ".local"
        / "state"
        / "linux-speech-tools"
        / "insertion-metrics-v1.json"
    )

    monkeypatch.setenv("LST_INSERTION_METRICS_FILE", "relative-metrics.json")
    with pytest.raises(ValueError, match="absolute"):
        default_state_path()


def test_report_never_fabricates_minimum_evidence_or_manual_recovery(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    first = dt.date(2026, 8, 1)
    last = dt.date(2026, 8, 15)
    for index in range(100):
        store.record(
            backend="synthetic-paste",
            attempted_backend="synthetic-paste",
            result_category=(
                "clipboard-fallback" if index < 11 else "dispatched-unconfirmed"
            ),
            target_kind="ide",
            eligible_supported_gnome_direct=True,
            day=first if index < 50 else last,
        )

    data = report_data(store)
    assert data["evidence"] == {
        "cohort": "eligible-supported-gnome-direct-attempts",
        "ready": True,
        "minimum_eligible_attempts": 100,
        "minimum_days": 14,
        "observed_eligible_attempts": 100,
        "observed_days": 14,
        "first_eligible_attempt_on": "2026-08-01",
        "last_eligible_attempt_on": "2026-08-15",
    }
    assert data["ibus_trigger"]["automatic_rate_trigger_met"] is True
    assert data["rates"]["cohort"] == "eligible-supported-gnome-direct-attempts"
    assert data["ibus_trigger"]["cohort"] == (
        "eligible-supported-gnome-direct-attempts"
    )
    assert data["ibus_trigger"]["manual_recovery_trigger_evaluated"] is False
    report = render_report(data)
    assert "Evidence status: **READY**" in report
    assert "manual-recovery trigger is not evaluated" in report
    assert "Wrong-window insertion is a P0 safety defect" in report

    pending = InsertionMetricsStore(tmp_path / "pending.json")
    for _ in range(99):
        pending.record(
            backend="ydotool",
            attempted_backend="synthetic-paste",
            result_category="clipboard-fallback",
            target_kind="browser",
            eligible_supported_gnome_direct=True,
            day=first,
        )
    pending_data = report_data(pending)
    assert pending_data["evidence"]["ready"] is False
    assert pending_data["ibus_trigger"]["automatic_rate_trigger_met"] is False
    assert "Evidence status: **PENDING**" in render_report(pending_data)

    thirteen_days = InsertionMetricsStore(tmp_path / "thirteen-days.json")
    for index in range(100):
        thirteen_days.record(
            backend="synthetic-paste",
            attempted_backend="synthetic-paste",
            result_category=(
                "clipboard-fallback" if index < 10 else "dispatched-unconfirmed"
            ),
            target_kind="ide",
            eligible_supported_gnome_direct=True,
            day=first if index < 50 else dt.date(2026, 8, 14),
        )
    boundary = report_data(thirteen_days)
    assert boundary["evidence"]["observed_days"] == 13
    assert boundary["evidence"]["ready"] is False
    assert boundary["ibus_trigger"]["automatic_rate_trigger_met"] is False


def test_noneligible_sessions_do_not_dilute_or_trigger_ibus_gate(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    first = dt.date(2026, 8, 1)
    last = dt.date(2026, 8, 15)
    for index in range(100):
        store.record(
            backend="clipboard",
            attempted_backend="clipboard",
            result_category="clipboard-fallback",
            target_kind="browser",
            day=first if index < 50 else last,
        )
    for index in range(100):
        _record_eligible(
            store,
            result_category=(
                "clipboard-fallback" if index < 11 else "dispatched-unconfirmed"
            ),
            target_kind="terminal",
            day=first if index < 50 else last,
        )

    data = report_data(store)
    assert data["totals"]["sessions"] == 200
    assert data["evidence"]["observed_eligible_attempts"] == 100
    assert data["rates"]["clipboard_fallback"] == 0.11
    assert data["ibus_trigger"]["automatic_rate_trigger_met"] is True

    explicit_only = InsertionMetricsStore(tmp_path / "explicit-only.json")
    for index in range(100):
        explicit_only.record(
            backend="clipboard",
            attempted_backend="clipboard",
            result_category="clipboard-fallback",
            target_kind="browser",
            # Even a mistaken caller flag cannot put a non-direct backend in
            # the decision cohort.
            eligible_supported_gnome_direct=True,
            day=first if index < 50 else last,
        )
    explicit_data = report_data(explicit_only)
    assert explicit_data["evidence"]["observed_eligible_attempts"] == 0
    assert explicit_data["ibus_trigger"]["automatic_rate_trigger_met"] is False


def test_attempted_final_and_joint_coarse_attribution_are_independent(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    _record_eligible(
        store,
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="browser",
    )
    _record_eligible(store, target_kind="terminal", backend="wayland_ydotool")

    data = report_data(store)
    totals = data["totals"]
    assert totals["attempted_backends"]["synthetic-paste"] == 2
    assert totals["backends"]["clipboard"] == 1
    assert totals["backends"]["ydotool"] == 1
    assert data["eligible_targets"]["browser"]["clipboard_fallback_rate"] == 1.0
    assert data["eligible_targets"]["terminal"]["clipboard_fallback_rate"] == 0.0


def test_unicode_failure_counters_are_transcript_free(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    assert store.record(
        backend="ydotool",
        attempted_backend="synthetic-paste",
        result_category="confirmed-inserted",
        target_kind="terminal",
    )
    assert store.mark_failure(
        "unicode",
        backend="private/repository|backend",
        target_kind="private window title",
    )
    assert store.mark_failure(
        "spanish-punctuation", backend="clipboard", target_kind="codex"
    )
    data = report_data(store)
    totals = data["totals"]
    assert totals["sessions"] == 1
    assert totals["unicode_failures"] == 1
    assert totals["spanish_punctuation_failures"] == 1
    assert totals["backends"] == {
        **{name: 0 for name in totals["backends"]},
        "ydotool": 1,
    }
    assert totals["target_kinds"] == {
        **{name: 0 for name in totals["target_kinds"]},
        "terminal": 1,
    }
    assert sum(totals["backends"].values()) == totals["sessions"]
    assert sum(totals["target_kinds"].values()) == totals["sessions"]
    raw = store.path.read_text(encoding="utf-8")
    assert "private/repository" not in raw
    assert "private window title" not in raw
    report = render_report(data)
    assert "Final delivery backends: ydotool=1" in report
    assert "Coarse targets: terminal=1" in report


def test_legacy_failure_contamination_fails_closed(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    assert store.record(
        backend="ydotool",
        result_category="confirmed-inserted",
        target_kind="terminal",
    )
    state = json.loads(store.path.read_text(encoding="utf-8"))
    bucket = next(iter(state["days"].values()))
    # Older code mixed a manual failure observation into these completed-
    # session maps without incrementing sessions.
    bucket["backends"]["clipboard"] = 1
    bucket["target_kinds"]["codex"] = 1
    store.path.write_text(json.dumps(state), encoding="utf-8")

    snapshot = store.snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["days"] == {}
    assert store.disabled_path.read_text(encoding="utf-8") == "disabled\n"


@pytest.mark.parametrize("contradiction", ("result", "target"))
def test_contradictory_eligible_marginals_fail_closed(tmp_path, contradiction):
    store = InsertionMetricsStore(tmp_path / contradiction / "metrics.json")
    assert _record_eligible(
        store,
        result_category="dispatched-unconfirmed",
        target_kind="codex",
    )
    state = json.loads(store.path.read_text(encoding="utf-8"))
    bucket = next(iter(state["days"].values()))
    if contradiction == "result":
        bucket["eligible_results"] = {"clipboard-fallback": 1}
        bucket["eligible_target_results"] = {
            "codex:clipboard-fallback": 1
        }
    else:
        bucket["eligible_target_results"] = {
            "browser:dispatched-unconfirmed": 1
        }
    store.path.write_text(json.dumps(state), encoding="utf-8")

    snapshot = store.snapshot()

    assert snapshot["enabled"] is False
    assert snapshot["days"] == {}
    assert store.disabled_path.read_text(encoding="utf-8") == "disabled\n"


def test_store_keeps_a_bounded_daily_schema(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    start = dt.date(2026, 1, 1)
    for offset in range(MAX_DAYS + 5):
        store.record(
            backend="stdout",
            result_category="non-inserting",
            target_kind="generic",
            day=start + dt.timedelta(days=offset),
        )
    days = store.snapshot()["days"]
    assert len(days) == MAX_DAYS
    assert min(days) == (start + dt.timedelta(days=5)).isoformat()


def test_cli_report_is_atomic_private_and_reset_requires_confirmation(
    tmp_path, capsys
):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    store.record(
        backend="clipboard",
        result_category="clipboard-fallback",
        target_kind="terminal",
    )
    output = tmp_path / "reports" / "dated.md"

    assert main(["report", "--output", str(output)], store=store) == 0
    assert output.read_text(encoding="utf-8").startswith(
        "# Insertion reliability report"
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert main(["status"], store=store) == 0
    assert main(["reset"], store=store) == 2
    assert report_data(store)["totals"]["sessions"] == 1
    assert main(["reset", "--yes"], store=store) == 0
    assert report_data(store)["totals"]["sessions"] == 0
    captured = capsys.readouterr()
    assert "eligible attempts:" in captured.out
    assert "without --yes" in captured.err


def test_report_rejects_intermediate_symlink_alias_before_snapshot(tmp_path):
    real = tmp_path / "real"
    parent = real / "subdirectory"
    parent.mkdir(parents=True)
    parent.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    store = InsertionMetricsStore(parent / "metrics.json")
    assert store.record(
        backend="stdout",
        result_category="non-inserting",
        target_kind="generic",
    )
    original = store.path.read_bytes()

    with mock.patch("src.stt.insertion_metrics.report_data") as reporter:
        with pytest.raises(UnsafeMetricsPath, match="symlink components"):
            main(
                [
                    "report",
                    "--output",
                    str(alias / "subdirectory" / "metrics.json"),
                ],
                store=store,
            )

    reporter.assert_not_called()
    assert store.path.read_bytes() == original


def test_report_rejects_inode_equivalent_reserved_path_before_snapshot(tmp_path):
    store = InsertionMetricsStore(tmp_path / "metrics.json")
    assert store.record(
        backend="stdout",
        result_category="non-inserting",
        target_kind="generic",
    )
    alias = tmp_path / "state-hardlink.md"
    os.link(store.path, alias)

    with mock.patch("src.stt.insertion_metrics.report_data") as reporter:
        with pytest.raises(UnsafeMetricsPath, match="reserved state path"):
            main(["report", "--output", str(alias)], store=store)

    reporter.assert_not_called()


def test_prompt_dictation_records_final_result_exactly_once():
    from src.stt import prompt_dictation

    dictation = prompt_dictation.PromptDictation.__new__(
        prompt_dictation.PromptDictation
    )
    dictation.profile = "codex"
    dictation.output = "paste"
    dictation.submit = "never"
    dictation.engine = "faster-whisper"
    dictation.target = SimpleNamespace(
        kind="codex",
        confidence="high",
        source="gnome-focus",
        schema_version=1,
        window_id="private-window-token",
        shell_session_id="private-shell-token",
        window_sequence=7,
        focus_generation=11,
        locked=False,
    )
    dictation.confirmed_parts = ["private prompt"]
    dictation.revision = 0
    dictation._voice_submit_requested = False
    dictation.session = mock.Mock()
    dictation.session.run.return_value = True
    dictation.renderer = mock.Mock(mode="paste", backend="synthetic-paste")
    final_result = result(
        InsertionState.CLIPBOARD_FALLBACK,
        "clipboard",
        revision=1,
        target_token_match=False,
    )
    dictation.renderer.finalize.return_value = final_result
    dictation.renderer.focus_drift_detected = True
    recorder = mock.Mock()
    dictation._insertion_metrics_recorder = recorder

    with mock.patch.object(prompt_dictation, "notify"):
        assert dictation.run() == 0

    recorder.assert_called_once_with(
        final_result,
        target_kind="codex",
        attempted_backend="synthetic-paste",
        focus_drift=True,
        eligible_supported_gnome_direct=True,
    )
    dictation.renderer.finalize.assert_called_once_with("private prompt", 1)
    dictation.renderer.close.assert_called_once_with()


def test_eligible_direct_classifier_requires_authoritative_gnome_identity():
    from src.stt.prompt_dictation import eligible_supported_gnome_direct_attempt

    fields = {
        "kind": "codex",
        "schema_version": 1,
        "window_id": "private-window-token",
        "shell_session_id": "private-shell-token",
        "window_sequence": 7,
        "focus_generation": 11,
        "locked": False,
    }
    assert eligible_supported_gnome_direct_attempt(
        SimpleNamespace(source="gnome-focus", **fields), "synthetic-paste"
    )
    assert eligible_supported_gnome_direct_attempt(
        SimpleNamespace(source="profile", **fields), "synthetic-live-type"
    )
    assert not eligible_supported_gnome_direct_attempt(
        SimpleNamespace(source="env-json", **fields), "synthetic-paste"
    )
    assert not eligible_supported_gnome_direct_attempt(
        SimpleNamespace(source="gnome-focus", **fields), "clipboard"
    )
    assert not eligible_supported_gnome_direct_attempt(
        SimpleNamespace(source="gnome-focus", **{**fields, "locked": True}),
        "synthetic-paste",
    )


def test_metrics_failure_cannot_change_success_or_skip_close():
    from src.stt import prompt_dictation

    dictation = prompt_dictation.PromptDictation.__new__(
        prompt_dictation.PromptDictation
    )
    dictation.profile = "codex"
    dictation.output = "paste"
    dictation.submit = "never"
    dictation.engine = "faster-whisper"
    dictation.target = SimpleNamespace(
        kind="codex", confidence="high", source="test"
    )
    dictation.confirmed_parts = ["private prompt"]
    dictation.revision = 0
    dictation._voice_submit_requested = False
    dictation.session = mock.Mock()
    dictation.session.run.return_value = True
    dictation.renderer = mock.Mock(mode="paste")
    dictation.renderer.finalize.return_value = result(
        InsertionState.DISPATCHED_UNCONFIRMED, "synthetic-paste", revision=1
    )
    recorder = mock.Mock(side_effect=OSError("local metrics unavailable"))
    dictation._insertion_metrics_recorder = recorder

    with mock.patch.object(prompt_dictation, "notify"):
        assert dictation.run() == 0

    recorder.assert_called_once()
    dictation.renderer.close.assert_called_once_with()


class _DriftTransport:
    backend = "synthetic-paste"
    mode = "paste"
    supports_submit = True
    requires_target_match = True
    destructive_updates = True
    safe_fallback = True

    def update(self, _text, revision=None):
        return result(
            InsertionState.DISPATCHED_UNCONFIRMED,
            self.backend,
            revision=revision,
        )

    def finalize(self, _text, revision=None):
        return result(
            InsertionState.DISPATCHED_UNCONFIRMED,
            self.backend,
            revision=revision,
        )

    def fallback(self, _text, revision=None):
        return result(
            InsertionState.CLIPBOARD_FALLBACK,
            "clipboard",
            revision=revision,
        )

    def cancel(self, revision=None):
        return result(InsertionState.CANCELLED, self.backend, revision=revision)

    def close(self):
        return None


def test_focus_drift_property_remains_latched_after_clipboard_fallback():
    session = InsertionSession(
        _DriftTransport(), target_guard=lambda: False, target_token="token"
    )

    update = session.update("private partial", 1)
    final = session.finalize("private final", 2)

    assert update.target_token_match is False
    assert final.state == InsertionState.CLIPBOARD_FALLBACK
    assert final.target_token_match is None
    assert session.focus_drift_detected is True
    assert session.can_submit() is False
    assert session.focus_drift_detected is True
