from tests.test_job_auto_apply import NOW, app, add, claimed, connect  # noqa: F401
from worklead.jobs.auto_apply import condition
from worklead.models import Lead, SourceRecord, UserFeedback, new_id


def mark_ai(app, lead_id, **over):
    with app.db.session() as s:
        lead = s.get(Lead, lead_id)
        rec = s.get(SourceRecord, lead.primary_record_id)
        lead.priority_total, lead.demand_intent, lead.recommendation = 20, "unknown", "needs_review"
        lead.ai_recheck = {"promoted": True, "auto_eligible": True, "content_hash": rec.content_hash, **over}


def check(app, lead_id):
    with app.db.session() as s:
        return condition(s, s.get(Lead, lead_id), NOW, 50)


def test_ai_eligible_lead_passes_despite_low_rule_score(app):
    lead_id = add(app)
    mark_ai(app, lead_id)
    assert check(app, lead_id) is None


def test_stale_verdict_or_not_eligible_is_blocked(app):
    lead_id = add(app)
    mark_ai(app, lead_id, content_hash="old")
    assert check(app, lead_id) == "condition_changed"
    mark_ai(app, lead_id, auto_eligible=False)
    assert check(app, lead_id) == "condition_changed"


def test_new_hard_exclusion_blocks_ai_path(app):
    lead_id = add(app)
    mark_ai(app, lead_id)
    with app.db.session() as s:
        rec = s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id)
        rec.body_text = (rec.body_text or "") + " 매일 사무실 출근 필수입니다. 모집 마감되었습니다."
    assert check(app, lead_id) == "condition_changed"


def test_low_score_ai_lead_is_offered_by_next(app):
    lead_id = add(app)
    mark_ai(app, lead_id)
    bridge, client, _ = connect(app)
    assert claimed(client) is not None


def test_time_wage_in_current_pay_blocks_ai_path(app):
    # A wage in the body already reads as staff hiring (hard exclusion). A wage in
    # the stored pay text keeps pay.unit "project" and yields only the time_pay factor.
    lead_id = add(app)
    mark_ai(app, lead_id)
    with app.db.session() as s:
        rec = s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id)
        rec.pay_raw = (rec.pay_raw or "") + " (시급 15,000원)"
    assert check(app, lead_id) == "condition_changed"


def test_user_feedback_not_real_request_blocks_ai_path(app):
    lead_id = add(app)
    mark_ai(app, lead_id)
    with app.db.session() as s:
        s.add(UserFeedback(id=new_id("fb"), lead_id=lead_id, kind="real_request", value="no", created_at=NOW))
    assert check(app, lead_id) == "condition_changed"
