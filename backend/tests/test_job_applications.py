"""Draft-only application queue: current eligibility, preservation and zero sends."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from tests.conftest import client_for
from worklead.analysis.types import DateInfo, PayInfo
from worklead.engine.pipeline import ingest
from worklead.jobs import applications as svc
from worklead.models import Lead, LeadActivity, SourcePolicy, SourceRecord
from worklead.services.settings import update_profile
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 10, 16, 3, tzinfo=UTC)


def post(**changes):
    return replace(ParsedPost(
        title="온라인 상세페이지 만들어주실수 있는분이요",
        body="매니저 소개 페이지 제작이 필요합니다. 관련 작업이 가능하신 분이면 좋겠습니다.",
        original_url="https://jobs.daangn.com/job-posts/application1",
        canonical_url="https://jobs.daangn.com/job-posts/application1", source_post_id="application1",
        published=DateInfo(NOW - timedelta(hours=1), "exact"), source_status="open",
        pay=PayInfo(raw="건당 150,000원", unit="project", min=150_000, max=150_000),
    ), **changes)


@pytest.fixture
def app(ctx):
    ctx.clock = lambda: NOW
    with ctx.db.session() as s:
        s.get(SourcePolicy, "daangn-alba").status = "allowed"
        update_profile(s, {"skills": "저장한 기술 소개: Python, React. 경력·포트폴리오는 미작성."})
    def deny_network(request):
        pytest.fail("초안 API가 외부로 요청하면 안 됩니다")
    ctx.transport = httpx.MockTransport(deny_network)
    return ctx


def add(app, p=None, *, source_id="daangn-alba"):
    with app.db.session() as s:
        result = ingest(s, source_id, p or post(), parser_version="test", now=NOW)
        return result.lead_id


def codes(body):
    return {item["code"] for item in body["hold_reasons"]}


def test_review_candidate_can_prepare_but_never_submit_and_preserves_current_classification(app):
    id_ = add(app)
    c = client_for(app)
    listing = c.get("/v1/jobs/applications").json()
    assert listing["mode"] == "draft_only" and listing["submission_supported"] is False
    assert listing["total"] == 1
    entry = listing["items"][0]
    assert entry["lead_id"] == id_ and entry["candidate"] and entry["can_prepare"]
    assert entry["recommendation"] == "needs_review" and entry["can_submit"] is False
    assert {"draft_only", "profile_not_approved", "submission_path_unverified", "remote_unconfirmed", "questions_unanswered"} <= codes(entry)
    prepared = c.post(f"/v1/jobs/applications/{id_}/prepare")
    assert prepared.status_code == 200, prepared.text
    draft = prepared.json()["draft"]
    assert "Python, React" in draft["text"] and "경력·포트폴리오는 미작성" in draft["text"]
    assert "seyj.kr" not in draft["text"] and "전문가" not in draft["text"]
    assert draft["edited_by_user"] is False and draft["generated_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert c.post(f"/v1/jobs/applications/{id_}/prepare").json()["draft"] == draft
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        assert lead.recommendation == "needs_review" and lead.sales_stage == "new"
        assert s.scalar(select(func.count()).select_from(LeadActivity).where(LeadActivity.lead_id == id_, LeadActivity.kind == "draft")) == 1
    result = c.post(f"/v1/jobs/applications/{id_}/submit")
    assert result.status_code == 409
    error = result.json()["error"]
    assert error["code"] == "application_submission_unavailable"
    assert error["details"]["sent"] == 0 and error["details"]["mode"] == "draft_only"


@pytest.mark.parametrize("change", ["excluded", "dismissed", "contacted", "closed", "access", "employee", "wrong_source", "record_closed", "current_rules_exclude"])
def test_ineligible_leads_cannot_prepare_and_never_send(app, change):
    id_ = add(app)
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        rec = s.get(SourceRecord, lead.primary_record_id)
        if change == "excluded": lead.recommendation = "excluded"
        if change == "dismissed": lead.user_mark = "dismissed"
        if change == "contacted": lead.sales_stage = "contacted"
        if change == "closed": lead.source_status = "closed"
        if change == "access": lead.access_status = "login_required"
        if change == "employee": lead.engagement_type = "part_time"
        if change == "wrong_source": lead.source_id = "manual"
        if change == "record_closed": rec.source_status = "closed"
        if change == "current_rules_exclude":
            rec.body_text = "매일 출근 필수. 시급 15000원. 쇼핑몰 CS 직원 구합니다."
    c = client_for(app)
    entry = c.get(f"/v1/jobs/applications/{id_}").json()
    assert not entry["candidate"] and not entry["can_prepare"]
    assert c.post(f"/v1/jobs/applications/{id_}/prepare").status_code == 409
    assert c.post(f"/v1/jobs/applications/{id_}/submit").json()["error"]["details"]["sent"] == 0
    with app.db.session() as s:
        assert s.get(Lead, id_).draft_text is None


@pytest.mark.parametrize("case,expected", [
    ("old", "publication_not_recent"), ("day", "publication_not_recent"),
    ("future", "publication_not_recent"), ("stale", "recheck_required"),
    ("lowpay", "low_pay_review"), ("profile_empty", "profile_incomplete"),
    ("permission", "source_permission_unverified"), ("url", "source_url_unverified"),
    ("missing_pay", "budget_unconfirmed"),
])
def test_uncertainty_is_explicit_hold_but_draft_candidate_remains(app, case, expected):
    p = post()
    if case == "old": p.published = DateInfo(NOW - timedelta(days=2), "exact")
    if case == "day": p.published = DateInfo(NOW, "day")
    if case == "future": p.published = DateInfo(NOW + timedelta(seconds=1), "exact")
    if case == "lowpay": p.pay = PayInfo(raw="건당 10000원", unit="project", min=10_000, max=10_000)
    if case == "missing_pay": p.pay = PayInfo(raw="건당 협의", unit="project")
    if case == "url": p.original_url = "https://evil.example/job-posts/application1"
    id_ = add(app, p)
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        if case == "stale": s.get(SourceRecord, lead.primary_record_id).last_checked_at = NOW - timedelta(days=2)
        if case == "profile_empty": update_profile(s, {"skills": ""})
        if case == "permission": s.get(SourcePolicy, "daangn-alba").status = "restricted"
    c = client_for(app)
    entry = c.get(f"/v1/jobs/applications/{id_}").json()
    assert entry["candidate"] and entry["can_prepare"] and not entry["can_submit"]
    assert expected in codes(entry)
    assert c.post(f"/v1/jobs/applications/{id_}/prepare").status_code == 200


def test_prepare_preserves_user_draft_and_save_compare_and_swap(app):
    id_ = add(app)
    c = client_for(app)
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        lead.draft_text, lead.draft_edited = "사용자 작성·가격 37만원", True
        lead.memo, lead.user_mark = "중요 메모", "interested"
    body = c.post(f"/v1/jobs/applications/{id_}/prepare").json()
    assert body["draft"]["text"] == "사용자 작성·가격 37만원"
    assert body["draft"]["edited_by_user"]
    saved = c.patch(f"/v1/jobs/applications/{id_}/draft", json={"text": "직접 고친 최종 초안", "expected_text": "사용자 작성·가격 37만원"})
    assert saved.status_code == 200 and saved.json()["draft"]["edited_by_user"]
    stale = c.patch(f"/v1/jobs/applications/{id_}/draft", json={"text": "늦은 덮어쓰기", "expected_text": "사용자 작성·가격 37만원"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "draft_changed"
    assert c.post(f"/v1/jobs/applications/{id_}/prepare").json()["draft"]["text"] == "직접 고친 최종 초안"
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        assert lead.memo == "중요 메모" and lead.user_mark == "interested" and lead.sales_stage == "new"


def test_concurrent_prepare_is_idempotent_and_profile_free_text_is_only_copied(app):
    id_ = add(app)
    with app.db.session() as s:
        update_profile(s, {"skills": "경력/가격/실적은 아직 입력하지 않았습니다."})
    def prepare(_):
        with app.db.session() as s:
            return svc.prepare(s, id_, NOW).draft.text
    with ThreadPoolExecutor(max_workers=4) as pool:
        texts = list(pool.map(prepare, range(4)))
    assert len(set(texts)) == 1 and "경력/가격/실적은 아직 입력하지 않았습니다." in texts[0]
    with app.db.session() as s:
        assert s.scalar(select(func.count()).select_from(LeadActivity).where(LeadActivity.lead_id == id_, LeadActivity.kind == "draft")) == 1


def test_auth_missing_id_validation_and_mode_cannot_be_overridden(app):
    id_ = add(app)
    c = client_for(app)
    assert c.get("/v1/jobs/applications", headers={"Authorization": ""}).status_code == 401
    assert c.post(f"/v1/jobs/applications/{id_}/prepare", headers={"Authorization": ""}).status_code == 401
    assert c.get("/v1/jobs/applications/missing").status_code == 404
    assert c.get("/v1/jobs/applications?limit=101").status_code == 422
    assert c.patch(f"/v1/jobs/applications/{id_}/draft", json={"text": "초안"}).status_code == 422
    result = c.post(f"/v1/jobs/applications/{id_}/submit", json={"mode": "auto", "approved": True})
    assert result.status_code == 409 and result.json()["error"]["details"]["sent"] == 0


def test_recommended_candidate_still_requires_approved_profile_and_real_submission_path(app):
    id_ = add(app, post(title="홈페이지 제작 의뢰", body="홈페이지 제작 의뢰합니다. 원격으로 진행합니다.",
                       pay=PayInfo(raw="건당 100만원", unit="project", min=1_000_000, max=1_000_000)))
    c = client_for(app)
    entry = c.get(f"/v1/jobs/applications/{id_}").json()
    assert entry["recommendation"] == "recommended" and entry["can_prepare"]
    assert {"profile_not_approved", "submission_path_unverified", "draft_only", "profitability_assumptions"} <= codes(entry)
    assert c.post(f"/v1/jobs/applications/{id_}/submit").status_code == 409


@pytest.mark.parametrize("draft_text", ["", None])
def test_intentionally_cleared_user_draft_is_not_regenerated(app, draft_text):
    id_ = add(app)
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        lead.draft_text, lead.draft_edited = draft_text, True
    response = client_for(app).post(f"/v1/jobs/applications/{id_}/prepare")
    assert response.status_code == 200 and response.json()["draft"] is None
    with app.db.session() as s:
        assert s.get(Lead, id_).draft_text == draft_text
        assert s.scalar(select(func.count()).select_from(LeadActivity).where(LeadActivity.lead_id == id_, LeadActivity.kind == "draft")) == 0


def test_missing_source_body_is_not_a_workable_draft_and_invalid_source_url_is_a_hold(app):
    id_ = add(app)
    c = client_for(app)
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        s.get(SourceRecord, lead.primary_record_id).original_url = "https://[malformed"
    assert "source_url_unverified" in codes(c.get(f"/v1/jobs/applications/{id_}").json())
    with app.db.session() as s:
        lead = s.get(Lead, id_)
        s.get(SourceRecord, lead.primary_record_id).body_text = None
    entry = c.get(f"/v1/jobs/applications/{id_}").json()
    assert "source_body_missing" in codes(entry) and not entry["can_prepare"]
    assert c.post(f"/v1/jobs/applications/{id_}/prepare").status_code == 409
