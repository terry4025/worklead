from dataclasses import replace
from datetime import timedelta

import pytest

from tests.exaone_fixtures import ex_ctx, exaone_json
from tests.test_ai_runtime_quality import seed
from tests.test_source_fields import NOW
from worklead.analysis.assist import take_call
from worklead.analysis.provider import AIFailure
from worklead.analysis.types import Profile
from worklead.analysis.writer import (MAX_ATTEMPTS, application_draft, application_step, draft_key, text_hash,
                                      validate_application)
from worklead.engine.pipeline import generate_draft
from worklead.models import AiDraft, EventOutbox, Lead, SourceRecord, UserProfile
from worklead.services.settings import get_profile, update_profile

P = Profile(skills="React와 Python으로 홈페이지와 자동화를 2년간 만들었습니다.", business_registered=True, tax_invoice=True)
SRC = "홈페이지 제작 의뢰\n홈페이지 제작 의뢰합니다. 원격으로 진행합니다. 건당 100만원"
GOOD = ("홈페이지 제작을 원격으로 맡기실 분을 찾고 계신 것으로 이해했습니다. React와 Python으로 홈페이지와 자동화를 만들어 왔고, "
        "필요한 화면과 기능부터 같이 정리해 드리겠습니다. 지금은 어떤 방식으로 소개 페이지를 운영하고 계신지, "
        "가장 불편한 점이 무엇인지 알려 주시면 범위를 잡아 보겠습니다. 사업자 등록을 마쳤고 세금계산서 발행도 가능합니다.")


def test_portfolio_is_appended_and_hash_matches_js_norm():
    text = validate_application(GOOD, source=SRC, profile=P)
    assert text.endswith("포트폴리오: https://www.seyj.kr/")
    assert text_hash("a \n\n b") == text_hash("a b")


def test_invisible_characters_are_removed():
    text = validate_application("\ufeff" + GOOD.replace(" ", "\u200b ", 1) + "\r\n", source=SRC, profile=P)
    assert "\ufeff" not in text and "\u200b" not in text and "\r" not in text


@pytest.mark.parametrize("bad", [
    GOOD + " 결과는 100% 보장합니다.", GOOD + " 3일이면 끝납니다.", GOOD + " 010-1234-5678로 연락 주세요.",
    GOOD + " 참고: https://example.com", "Short english text only"])
def test_unsafe_text_is_rejected(bad):
    with pytest.raises(AIFailure):
        validate_application(bad, source=SRC, profile=P)


def test_business_claims_must_match_profile():
    unregistered = replace(P, business_registered=False, tax_invoice=False)
    with pytest.raises(AIFailure):
        validate_application(GOOD, source=SRC, profile=unregistered)
    with pytest.raises(AIFailure):
        validate_application(GOOD.replace("사업자 등록을 마쳤고", "사업자등록 전이라"), source=SRC, profile=P)


def test_profile_change_invalidates_draft_key(tmp_path):
    ctx = ex_ctx(tmp_path)
    lead_id = seed(ctx).lead_id
    with ctx.db.session() as s:
        rec = s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id)
        assert draft_key("application", rec, P) != draft_key("application", rec, replace(P, version=2))


def make_draft(ctx, lead_id):
    with ctx.db.session() as s:
        lead = s.get(Lead, lead_id)
        return application_draft(s, lead, s.get(SourceRecord, lead.primary_record_id), get_profile(s)).id


def registered(ctx):
    with ctx.db.session() as s:
        update_profile(s, {**(s.get(UserProfile, 1).data or {}), "skills": P.skills,
                           "business_registered": True, "tax_invoice": True})


def test_step_generates_ready_text(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"text": GOOD}))
    registered(ctx)
    lead_id = seed(ctx).lead_id
    draft_id = make_draft(ctx, lead_id)
    application_step(ctx)
    with ctx.db.session() as s:
        row = s.get(AiDraft, draft_id)
        assert row.status == "ready" and row.text.endswith("https://www.seyj.kr/")


def test_failures_retry_then_hold_with_notification(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"text": "Short english text only that fails the checks entirely"}))
    lead_id = seed(ctx).lead_id
    draft_id = make_draft(ctx, lead_id)
    clock = [NOW]
    ctx.clock = lambda: clock[0]
    for minutes in (0, 2, 5, 10):
        clock[0] += timedelta(minutes=minutes)
        application_step(ctx)
    with ctx.db.session() as s:
        row = s.get(AiDraft, draft_id)
        assert row.status == "failed" and row.attempts == MAX_ATTEMPTS
        assert s.query(EventOutbox).filter(EventOutbox.dedupe_key == f"notify:ai_text:{draft_id}").count() == 1


def test_call_cap_pauses_without_spending_attempts(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"text": GOOD}), daily_call_cap=1)
    lead_id = seed(ctx).lead_id
    draft_id = make_draft(ctx, lead_id)
    with ctx.db.session() as s:
        take_call(s, 1, NOW)
    application_step(ctx)
    with ctx.db.session() as s:
        row = s.get(AiDraft, draft_id)
        assert row.status == "pending" and row.attempts == 0
        assert s.query(EventOutbox).filter(EventOutbox.dedupe_key.like("notify:exaone:call_cap_reached:%")).count() == 1


def test_bad_key_pauses_with_one_notice(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({}, status=401))
    draft_id = make_draft(ctx, seed(ctx).lead_id)
    application_step(ctx)
    application_step(ctx)
    with ctx.db.session() as s:
        assert s.get(AiDraft, draft_id).attempts == 0
        assert s.query(EventOutbox).filter(EventOutbox.dedupe_key.like("notify:exaone:authentication_failed:%")).count() == 1


def test_inquiry_draft_uses_ready_ai_text(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"text": GOOD}))
    registered(ctx)
    lead_id = seed(ctx).lead_id
    with ctx.db.session() as s:
        template = generate_draft(s, s.get(Lead, lead_id), NOW)
    application_step(ctx)
    with ctx.db.session() as s:
        ai_text = generate_draft(s, s.get(Lead, lead_id), NOW)
    assert ai_text != template and ai_text.endswith("https://www.seyj.kr/")


def test_portfolio_change_invalidates_draft_key(tmp_path):
    ctx = ex_ctx(tmp_path)
    lead_id = seed(ctx).lead_id
    with ctx.db.session() as s:
        rec = s.get(SourceRecord, s.get(Lead, lead_id).primary_record_id)
        assert draft_key("application", rec, P) != draft_key("application", rec, replace(P, portfolio_url="https://a.example/"))
        assert draft_key("application", rec, P) != draft_key("application", rec, replace(P, price_anchor_note="건당 50만원"))
