"""Read-only completion receipts survive only the current authorized session."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_job_auto_apply import ACCOUNT, NOW, PROFILE, add, app, claimed, connect, ready
from worklead.models import EventOutbox, JobAutoAttempt, Lead, LeadActivity, SourceRecord


def recovery(body, receipt):
    return {key: body[key] for key in ("attempt_id", "account_hash", "profile_hash")} | {
        "receipt_token": receipt, "evidence": "cancel_button"}


def permitted(app):
    lead = add(app)
    bridge, client, connection = connect(app)
    body = claimed(client)
    receipt = ready(client, body)
    assert receipt != body["lease_token"]
    assert client.post("/permit", json=body).json()["permit"]
    return lead, bridge, client, connection, body, recovery(body, receipt)


def assert_completion_once(app, lead_id):
    with app.db.session() as s:
        assert s.get(Lead, lead_id).sales_stage == "contacted"
        assert len(list(s.scalars(select(LeadActivity).where(LeadActivity.lead_id == lead_id, LeadActivity.kind == "contact")))) == 1
        notices = s.scalars(select(EventOutbox).where(EventOutbox.type == "notification.created"))
        assert sum(event.payload.get("title") == "당근 지원 완료" for event in notices) == 1


def test_late_evidence_after_expiry_resolves_uncertain_without_reissuing_permit(app):
    lead, bridge, client, _, body, proof = permitted(app)
    try:
        app.clock = lambda: NOW + timedelta(seconds=16)
        assert bridge.status().uncertain == 1
        assert client.post("/result", json={**body, "outcome": "submitted", "reason_code": "submitted", "evidence": "cancel_button"}).status_code == 409
        assert client.post("/reconcile", json=proof).json() == {"status": "submitted"}
        assert client.post("/permit", json=body).status_code == 409
        status = bridge.status()
        assert status.today_submitted == 1 and status.today_reserved == 0 and status.uncertain == 0
        assert status.attempts[0].reason_code == "completion_reconciled"
        assert proof["receipt_token"] not in status.model_dump_json()
        assert_completion_once(app, lead)
    finally:
        client.close()


def test_lost_completion_ack_and_concurrent_recovery_finalize_only_once(app):
    lead, bridge, client, _, body, proof = permitted(app)
    try:
        result = {**body, "outcome": "submitted", "reason_code": "submitted", "evidence": "cancel_button"}
        with ThreadPoolExecutor(max_workers=3) as pool:
            replies = list(pool.map(lambda path: client.post(path, json=result if path == "/result" else proof), ["/result", "/reconcile", "/reconcile"]))
        assert all(r.status_code == 200 for r in replies[1:])
        assert replies[0].status_code in (200, 409)
        assert client.post("/reconcile", json=proof).json() == {"status": "submitted"}
        assert_completion_once(app, lead)
        assert bridge.status().today_reserved == 0
    finally:
        client.close()


def test_read_only_receipt_does_not_grant_a_submission_or_confirm_before_permit(app):
    add(app)
    bridge, client, _ = connect(app)
    try:
        body = claimed(client)
        receipt = ready(client, body)
        assert client.post("/reconcile", json=recovery(body, receipt)).status_code == 409
        assert client.post("/permit", json={**body, "lease_token": receipt}).status_code == 409
        assert bridge.status().today_reserved == 0
        assert not bridge._journal.exists()
    finally:
        client.close()


@pytest.mark.parametrize("field,value,expected", [
    ("receipt_token", "x" * 43, 409), ("receipt_token", None, 422),
    ("account_hash", "c" * 64, 409), ("profile_hash", "c" * 64, 409),
    ("attempt_id", "auto_other", 409), ("evidence", "clicked", 422),
])
def test_recovery_rejects_unbound_identity_or_nonofficial_evidence(app, field, value, expected):
    _, bridge, client, _, _, proof = permitted(app)
    try:
        assert client.post("/reconcile", json={**proof, field: value}).status_code == expected
        assert bridge.status().today_submitted == 0
    finally:
        client.close()


@pytest.mark.parametrize("boundary", ["stop_start", "restore", "expired_receipt", "journal_missing"])
def test_old_receipt_cannot_cross_a_recovery_boundary(app, boundary):
    _, bridge, client, _, _, proof = permitted(app)
    other = None
    try:
        if boundary == "stop_start":
            bridge.stop()
            bridge, other, _ = connect(app)
            assert other.post("/reconcile", json=proof, headers={"Authorization": client.headers["Authorization"]}).status_code == 401
        elif boundary == "restore":
            app.db.gate.restored()
        elif boundary == "expired_receipt":
            original = bridge.monotonic()
            bridge.monotonic = lambda: original + 901
        else:
            bridge._journal.unlink()
        assert (other or client).post("/reconcile", json=proof).status_code == 409
        assert bridge.status().today_submitted == 0
    finally:
        client.close()
        if other: other.close()


def test_past_submission_can_be_confirmed_after_score_or_recruitment_changes(app):
    lead_id, _, client, _, body, proof = permitted(app)
    try:
        assert client.post("/result", json={**body, "outcome": "uncertain", "reason_code": "submission_unconfirmed"}).status_code == 200
        with app.db.session() as s:
            lead = s.get(Lead, lead_id)
            lead.priority_total, lead.source_status = 10, "closed"
            s.get(SourceRecord, lead.primary_record_id).source_status = "closed"
        assert client.post("/reconcile", json=proof).json() == {"status": "submitted"}
        assert_completion_once(app, lead_id)
    finally:
        client.close()


def test_recovery_endpoint_preserves_auth_origin_and_exact_shape(app):
    _, _, client, _, body, proof = permitted(app)
    try:
        assert client.post("/reconcile", json=proof, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/reconcile", json=proof, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
        assert client.post("/reconcile", json={**proof, "lease_token": body["lease_token"]}).status_code == 422
    finally:
        client.close()


@pytest.mark.parametrize("reason", ["account_changed", "login_required"])
def test_observed_identity_change_revokes_all_receipts_and_new_submissions(app, reason):
    _, bridge, client, _, body, proof = permitted(app)
    try:
        assert client.post("/pause", json={"reason": reason}).json() == {"status": "paused"}
        assert bridge.status().state == "paused" and bridge.status().error_code == reason
        assert bridge.status().uncertain == 1
        assert not bridge._receipts
        assert client.post("/reconcile", json=proof).status_code == 409
        assert client.post("/permit", json=body).status_code == 409
        assert client.post("/next", json={"account_hash": ACCOUNT}).status_code == 409
        assert bridge.status().today_submitted == 0
    finally:
        client.close()
