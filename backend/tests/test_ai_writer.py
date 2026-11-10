from dataclasses import replace
from datetime import timedelta

import pytest

from tests.exaone_fixtures import ex_ctx, exaone_json
from tests.test_ai_runtime_quality import seed
from tests.test_source_fields import NOW
from worklead.analysis.assist import take_call
from worklead.analysis.provider import AIFailure
from worklead.analysis.types import Profile
from worklead.analysis import writer
from worklead.analysis.writer import (MAX_ATTEMPTS, application_draft, application_step, draft_key, text_hash,
                                      validate_application)
from worklead.engine.pipeline import generate_draft
from worklead.models import AiDraft, EventOutbox, Lead, SourceRecord, UserProfile
from worklead.services.settings import get_profile, update_profile

P = Profile(skills="React와 Python으로 홈페이지와 자동화를 2년간 만들었습니다.", business_registered=True, tax_invoice=True)
SRC = "홈페이지 제작 의뢰\n홈페이지 제작 의뢰합니다. 원격으로 진행합니다. 건당 100만원"
GOOD = ("홈페이지 제작을 원격으로 맡기실 분을 찾고 계신 것으로 이해했습니다. React와 Python으로 홈페이지와 자동화를 만들어 왔고, "
        "필요한 화면과 기능부터 같이 정리해 드리겠습니다. 지금은 어떤 방식으로 소개 페이지를 운영하고 계신지, "
        "가장 불편한 점이 무엇인지 알려 주시면 범위를 잡아 보겠습니다.")


def test_portfolio_is_appended_and_hash_matches_js_norm():
    text = validate_application(GOOD, source=SRC, profile=P)
    assert text.endswith("포트폴리오: https://www.seyj.kr/")
    assert text_hash("a \n\n b") == text_hash("a b")


def test_invisible_characters_are_removed():
    text = validate_application("\ufeff" + GOOD.replace(" ", "\u200b ", 1) + "\r\n", source=SRC, profile=P)
    assert "\ufeff" not in text and "\u200b" not in text and "\r" not in text


@pytest.mark.parametrize("bad", [
    GOOD + " 결과는 100% 보장합니다.", GOOD + " 3일이면 끝납니다.", GOOD + " 010-1234-5678로 연락 주세요.",
    GOOD + " 참고: https://example.com", "Short english text only",
    GOOD + " www.example.com", GOOD + " open.kakao.com/o/abcdef 로 문의 주세요",
    "가 I guarantee the best result for your homepage project, delivered fast and perfectly every single time.",
    *(f"{GOOD} {d} 참고해 주세요." for d in ("linktr.ee/abc", "forms.gle/abc", "example.ai", "example.shop", "example.xyz",
                                         "example.info", "example。com", "example[.]com", "example dot com", "문의example.com",
                                         "example [.] com", "example (.) com", "example[dot]com", "example(dot)com",
                                         "example｡com", "ｅｘａｍｐｌｅ．ｃｏｍ",
                                         "example．com", "example(.)com", "ｈｔｔｐｓ://evil.com"))])
def test_unsafe_text_is_rejected(bad):
    with pytest.raises(AIFailure):
        validate_application(bad, source=SRC, profile=P)


@pytest.mark.parametrize("claim", ["사업자등록이 되어 있습니다.", "개인사업자입니다.", "세금계산서 발행이 가능합니다.",
                                   "세금계산서 발급 가능합니다.", "사업자 등록은 아직 하지 않았습니다.", "세금 계산 서 드립니다."])
def test_model_written_business_claims_are_rejected(claim):
    for profile in (P, replace(P, business_registered=False, tax_invoice=False)):
        with pytest.raises(AIFailure):
            validate_application(f"{GOOD} {claim}", source=SRC, profile=profile)


@pytest.mark.parametrize("registered,tax,sentence", [
    (True, True, "사업자 등록을 마쳤고 세금계산서 발행도 가능합니다."), (True, False, "사업자 등록을 마쳤습니다."),
    (False, True, None), (False, False, None)])
def test_canonical_business_sentence_follows_profile(registered, tax, sentence):
    text = validate_application(GOOD, source=SRC, profile=replace(P, business_registered=registered, tax_invoice=tax))
    expected = GOOD + (f"\n\n{sentence}" if sentence else "") + "\n\n포트폴리오: https://www.seyj.kr/"
    assert text == expected


@pytest.mark.parametrize("mention", ["작업 사례는 https://www.seyj.kr/에서 보실 수 있습니다.",
                                     "작업 사례는 www.seyj.kr 에 정리해 두었습니다."])
def test_portfolio_mentions_are_allowed(mention):
    text = validate_application(f"{GOOD} {mention}", source=SRC, profile=P)
    assert mention in text


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


def test_internal_error_counts_as_attempt_and_does_not_block_queue(tmp_path, monkeypatch):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"text": GOOD}))
    broken = make_draft(ctx, seed(ctx, "1").lead_id)
    healthy_lead = seed(ctx, "2").lead_id
    healthy = make_draft(ctx, healthy_lead)
    real = writer.generate_application

    def produce(app, lead_id):
        if lead_id != healthy_lead:
            raise RuntimeError("boom")
        return real(app, lead_id)

    monkeypatch.setattr(writer, "generate_application", produce)
    clock = [NOW]
    ctx.clock = lambda: clock[0]
    for minutes in (0, 2, 5, 10):
        clock[0] += timedelta(minutes=minutes)
        application_step(ctx)
    with ctx.db.session() as s:
        row = s.get(AiDraft, broken)
        assert row.status == "failed" and row.attempts == MAX_ATTEMPTS and row.failure_code == "internal_error"
        assert s.get(AiDraft, healthy).status == "ready"


def test_mostly_english_body_is_rejected_before_korean_lines_are_appended():
    import re
    ko = "홈페이지 제작을 원격으로 맡기실 분을 찾고 계신 것으로 이해했습니다. 필요한 화면과 기능부터 같이 정리해 드리겠습니다."
    body = ko + " I build websites and automation with React and Python for you every day today"
    hangul, latin = len(re.findall("[가-힣]", body)), len(re.findall("[A-Za-z]", body))
    assert 0.40 < hangul / (hangul + latin) < 0.50
    with pytest.raises(AIFailure, match="한국어"):
        validate_application(body, source=SRC, profile=P)


@pytest.mark.parametrize("contact", ["abc＠gmail.com 으로 연락 주세요.", "example . com 참고해 주세요.",
                                     "０１０-１２３４-５６７８로 연락 주세요."])
def test_normalized_contacts_and_spaced_dots_are_rejected(contact):
    with pytest.raises(AIFailure):
        validate_application(f"{GOOD} {contact}", source=SRC, profile=P)


def test_korean_sentences_with_periods_still_pass():
    text = validate_application(GOOD + " 확인 부탁드립니다. 감사합니다. React 경험이 있습니다. Python도 씁니다.", source=SRC, profile=P)
    assert text.startswith(GOOD)
