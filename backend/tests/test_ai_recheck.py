import json
from datetime import timedelta

from tests.exaone_fixtures import ex_ctx, exaone_json
from tests.test_source_fields import NOW, sample
from worklead.analysis.recheck import RecheckOut, candidates, hard_excluded, judge, recheck_lead, recheckable
from worklead.engine.pipeline import apply_analysis, ingest
from worklead.models import EventOutbox, Lead, SourceRecord

UNCLEAR = [{"tone": "negative", "text": "프리랜서 제작 의뢰 미확인"}, {"tone": "negative", "text": "시급 지급 — 건당 프리랜서 의뢰 아님"}]
SOURCE = "C샵 자동매매 프로그램 오류 수정\n재택 가능하고 오류 수정까지 총 30만원입니다. 도와주실 분을 찾고 있어요."


def verdict(**over):
    base = {"is_dev_project": True, "dev_evidence": ["자동매매 프로그램 오류 수정"], "intent": "buyer_project",
            "intent_evidence": ["도와주실 분을 찾고 있어요"], "remote": "stated", "remote_evidence": ["재택 가능"],
            "pay_basis": "per_project"}
    return RecheckOut(**{**base, **over})


def test_only_soft_exclusions_are_rechecked():
    assert recheckable(UNCLEAR)
    assert not recheckable(UNCLEAR + [{"tone": "negative", "text": "출근 필수"}])
    assert hard_excluded([{"tone": "negative", "text": "모집 마감"}])
    assert not recheckable([])


def test_promotion_and_auto_eligibility_need_quotes():
    v = judge(verdict(), SOURCE, "project")
    assert v["promoted"] and v["auto_eligible"]
    assert not judge(verdict(remote="not_stated", remote_evidence=[]), SOURCE, "project")["auto_eligible"]
    assert not judge(verdict(), SOURCE, "hour")["auto_eligible"]
    forged = judge(verdict(dev_evidence=["원문에 없는 구절"]), SOURCE, "project")
    assert not forged["promoted"] and forged["rejected"] == "grounding"


def seed_excluded(ctx, body=SOURCE):
    from worklead.analysis.types import DateInfo
    post = sample()
    post.title, post.body = "C샵 자동매매 프로그램 오류 수정", body
    post.published = DateInfo(NOW - timedelta(hours=1), "exact", "합성 게시 시각")
    with ctx.db.session() as s:
        item = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
        lead = s.get(Lead, item.lead_id)
        lead.recommendation, lead.reasons = "excluded", UNCLEAR
        return item.lead_id


def test_recheck_promotes_notifies_and_survives_reanalysis(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json(verdict().model_dump()))
    lead_id = seed_excluded(ctx)
    with ctx.db.session() as s:
        assert candidates(s, NOW) == [lead_id]
    assert recheck_lead(ctx, lead_id)["promoted"]
    with ctx.db.session() as s:
        lead = s.get(Lead, lead_id)
        assert lead.recommendation == "needs_review" and lead.reasons[0]["text"].startswith("AI 재검토로 올림")
        assert s.query(EventOutbox).filter(EventOutbox.dedupe_key.like("notify:ai_recheck:%")).count() == 1
        assert candidates(s, NOW) == []
        rec = s.get(SourceRecord, lead.primary_record_id)
        apply_analysis(s, lead, rec, now=NOW)
        if lead.recommendation == "excluded":
            raise AssertionError("valid AI verdict must survive rule reanalysis")


def test_changed_text_invalidates_verdict(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json(verdict().model_dump()))
    lead_id = seed_excluded(ctx)
    recheck_lead(ctx, lead_id)
    with ctx.db.session() as s:
        lead = s.get(Lead, lead_id)
        s.get(SourceRecord, lead.primary_record_id).content_hash = "changed"
        lead.recommendation, lead.reasons = "excluded", UNCLEAR
        assert candidates(s, NOW) == [lead_id]


def test_recheck_switch_off_sends_nothing(tmp_path):
    sent = []
    ctx = ex_ctx(tmp_path, lambda r: sent.append(1) or exaone_json(verdict().model_dump()), assist_recheck=False)
    lead_id = seed_excluded(ctx)
    assert recheck_lead(ctx, lead_id) is None and not sent


def test_hard_reason_added_during_call_blocks_promotion(tmp_path):
    box = {}

    def handler(request):
        with box["ctx"].db.session() as s:
            s.get(Lead, box["lead_id"]).reasons = UNCLEAR + [{"tone": "negative", "text": "모집 마감"}]
        return exaone_json(verdict().model_dump())

    ctx = box["ctx"] = ex_ctx(tmp_path, handler)
    lead_id = box["lead_id"] = seed_excluded(ctx)
    assert recheck_lead(ctx, lead_id)["promoted"]
    with ctx.db.session() as s:
        lead = s.get(Lead, lead_id)
        assert lead.recommendation == "excluded"
        assert lead.ai_recheck["promoted"] and lead.ai_recheck["content_hash"]
        assert s.query(EventOutbox).filter(EventOutbox.dedupe_key.like("notify:ai_recheck:%")).count() == 0


def test_one_character_quote_fails_grounding():
    for field in ("dev_evidence", "intent_evidence", "remote_evidence"):
        out = judge(verdict(**{field: ["재"]}), SOURCE, "project")
        assert not out["promoted"] and out["rejected"] == "grounding"
