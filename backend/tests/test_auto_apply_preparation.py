"""Remembered automation prepares public records before Chrome connects."""
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_job_auto_apply import NOW, app, old_record
from worklead.db import MaintenanceBusy
from worklead.engine.executors import _ref_key
from worklead.jobs.auto_apply import PREPARE_SECONDS, runtime
from worklead.jobs.auto_apply_models import AutoApplyStartIn
from worklead.models import DetailFetchState, JobAutoAttempt, Lead, Run, Source, SourcePolicy, SourceRecord
from worklead.sources.base import PostRef


def prepared_runtime(app):
    bridge = runtime(app)
    bridge._status.enabled = bridge._status.remembers_connection = True
    bridge._stop.clear()
    bridge.monotonic = lambda: 100
    with app.db.session() as s:
        s.get(Source, "daangn-alba").auto_collect_enabled = True
    return bridge


def refreshes(app):
    with app.db.session() as s:
        return list(s.scalars(select(Run).where(Run.idempotency_key.like("auto-refresh:%"))))


def test_enabled_unpaired_session_prepares_without_claiming_or_permitting(app):
    lead_id = old_record(app)
    bridge = prepared_runtime(app)
    assert bridge._account is bridge._profile is bridge._connected is None
    assert bridge.prepare_candidates() == 1
    assert [(r.lead_id, r.kind, r.state) for r in refreshes(app)] == [(lead_id, "recheck_stale", "queued")]
    with app.db.session() as s:
        assert not list(s.scalars(select(JobAutoAttempt)))
    assert not bridge._leases and not bridge._receipts and not bridge._journal.exists()
    assert bridge._connected is None  # Preparation cannot impersonate a browser heartbeat.


def test_running_bridge_prepares_before_any_extension_request(app):
    lead_id = old_record(app)
    bridge = prepared_runtime(app)
    bridge._start_session(AutoApplyStartIn(confirm=True), pair_state={"account": None, "profile": None})
    deadline = time.monotonic() + 3
    while not refreshes(app) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert [r.lead_id for r in refreshes(app)] == [lead_id]
    assert bridge.status().last_connected_at is None
    assert not bridge.status().attempts


@pytest.mark.parametrize("boundary", ["disabled", "paused", "unremembered", "stopped", "restricted", "collection_off", "source_stopped"])
def test_preparation_respects_active_grant_and_source_controls(app, boundary):
    old_record(app)
    bridge = prepared_runtime(app)
    if boundary == "disabled": bridge._status.enabled = False
    elif boundary == "paused": bridge._status.state = "paused"
    elif boundary == "unremembered": bridge._status.remembers_connection = False
    elif boundary == "stopped": bridge._stop.set()
    else:
        with app.db.session() as s:
            if boundary == "restricted": s.get(SourcePolicy, "daangn-alba").status = "restricted"
            if boundary == "collection_off": s.get(Source, "daangn-alba").auto_collect_enabled = False
            if boundary == "source_stopped": s.get(Source, "daangn-alba").stopped_reason = "user_stop"
    assert bridge.prepare_candidates() == 0
    assert not refreshes(app)


@pytest.mark.parametrize("boundary", ["fresh", "low_score", "dismissed", "closed", "retained_body_missing", "condition_conflict"])
def test_only_stale_eligible_records_are_prepared(app, boundary):
    lead_id = old_record(app)
    bridge = prepared_runtime(app)
    with app.db.session() as s:
        lead = s.get(Lead, lead_id)
        rec = s.get(SourceRecord, lead.primary_record_id)
        if boundary == "fresh": rec.last_checked_at = NOW
        if boundary == "low_score": lead.priority_total = 49
        if boundary == "dismissed": lead.user_mark = "dismissed"
        if boundary == "closed": lead.source_status = rec.source_status = "closed"
        if boundary == "retained_body_missing": rec.body_text = None
        if boundary == "condition_conflict": rec.body_text += " 사업자등록증 필수"
    assert bridge.prepare_candidates() == 0
    assert not refreshes(app)


@pytest.mark.parametrize("outcome,backoff", [("http_404", True), ("collection_disallowed", False), ("collection_permission_unknown", False)])
def test_preparation_obeys_detail_permission_and_retry_backoff(app, outcome, backoff):
    lead_id = old_record(app)
    bridge = prepared_runtime(app)
    with app.db.session() as s:
        rec = s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id)
        s.add(DetailFetchState(source_id=rec.source_id, ref_key=_ref_key(PostRef(rec.original_url, rec.source_post_id)),
            url=rec.original_url, outcome=outcome, attempted_at=NOW,
            retry_at=NOW + timedelta(hours=1) if backoff else None))
    assert bridge.prepare_candidates() == 0
    assert not refreshes(app)


@pytest.mark.parametrize("state,reason,permitted,delay,retries,allowed", [
    ("uncertain", "submission_unconfirmed", True, None, 0, False),
    ("submitted", "submitted", True, None, 0, False),
    ("already_applied", "already_applied", False, None, 0, False),
    ("held", "questions_required", False, -1, 0, False),
    ("held", "navigation_failed", True, -1, 0, False),
    ("held", "navigation_failed", False, 60, 0, False),
    ("held", "navigation_failed", False, -1, 3, False),
    ("held", "navigation_failed", False, -1, 1, True),
])
def test_preparation_never_revives_a_completed_or_uncertain_send(app, state, reason, permitted, delay, retries, allowed):
    from worklead.jobs.auto_apply import job_identity
    lead_id = old_record(app)
    bridge = prepared_runtime(app)
    with app.db.session() as s:
        rec = s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id)
        s.add(JobAutoAttempt(id="auto_previous", job_key=job_identity(rec), lead_id=lead_id,
            state=state, reason_code=reason, created_at=NOW, retry_count=retries,
            permitted_at=NOW if permitted else None,
            retry_after=NOW + timedelta(seconds=delay) if delay is not None else None))
    assert bool(bridge.prepare_candidates()) is allowed
    assert bool(refreshes(app)) is allowed
    with app.db.session() as s:
        previous = s.get(JobAutoAttempt, "auto_previous")
        assert (previous.state, previous.reason_code, previous.retry_count) == (state, reason, retries)


def test_journal_blocks_recheck_even_if_restored_database_lost_the_attempt(app):
    from worklead.jobs.auto_apply import job_identity
    lead_id = old_record(app)
    bridge = prepared_runtime(app)
    with app.db.session() as s:
        key = job_identity(s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id))
    bridge._remember_permit(key)
    assert bridge.prepare_candidates() == 0
    assert not refreshes(app)


def test_preparation_is_rate_limited_and_restart_deduplicates_existing_work(app):
    first = old_record(app)
    second = old_record(app, key="ABCDEFGHIJK2")
    bridge = prepared_runtime(app)
    assert bridge.prepare_candidates() == 1
    assert bridge.prepare_candidates() == 0
    bridge.monotonic = lambda: 100 + PREPARE_SECONDS
    assert bridge.prepare_candidates() == 0  # Existing 60-second creation limit.
    with app.db.session() as s:
        s.get(Run, refreshes(app)[0].id).state = "succeeded"
    bridge.monotonic = lambda: 160
    assert bridge.prepare_candidates() == 1
    assert {r.lead_id for r in refreshes(app)} == {first, second}
    bridge._prepare_after = bridge._refresh_after = 0  # Restart loses only in-memory pacing.
    assert bridge.prepare_candidates() == 0
    assert len(refreshes(app)) == 2


def test_bounded_scan_rotates_past_conflicting_newer_records(app, monkeypatch):
    monkeypatch.setattr("worklead.jobs.auto_apply.PREPARE_LIMIT", 1)
    conflict = old_record(app, age=2)
    eligible = old_record(app, key="ABCDEFGHIJK2", age=3)
    bridge = prepared_runtime(app)
    with app.db.session() as s:
        rec = s.get(SourceRecord, s.get(Lead, conflict).primary_record_id)
        rec.body_text += " 사업자등록증 필수"
    assert bridge.prepare_candidates() == 0
    bridge.monotonic = lambda: 100 + PREPARE_SECONDS
    assert bridge.prepare_candidates() == 1
    assert [r.lead_id for r in refreshes(app)] == [eligible]


def test_restore_generation_and_maintenance_gate_prevent_preparation(app):
    old_record(app)
    bridge = prepared_runtime(app)
    app.db.gate._exclusive = True
    try:
        with pytest.raises(MaintenanceBusy): bridge.prepare_candidates()
    finally:
        app.db.gate._exclusive = False
    assert not refreshes(app)
    bridge.monotonic = lambda: 100 + PREPARE_SECONDS
    app.db.gate.restored()
    assert bridge.prepare_candidates() == 0
    assert bridge.status().state == "paused" and bridge.status().error_code == "database_restored"
    assert not refreshes(app)
