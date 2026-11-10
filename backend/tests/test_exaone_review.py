import json

from tests.exaone_fixtures import ex_ctx, exaone_json
from tests.test_ai_runtime_quality import seed
from worklead.models import AnalysisResult, Lead
from worklead.services.settings import stop_pending_ai


def grounded(request):
    data = json.loads(json.loads(request.content)["messages"][1]["content"])
    return exaone_json({"summary": data["title"], "deliverables": [], "tech": [], "questions": ["범위를 확인할 수 있을까요?"],
                        "uncertain": [], "suggested_intent": "buyer_project", "evidence_quotes": [data["title"]]})


def test_review_analysis_runs_on_exaone_and_applies(tmp_path):
    ctx = ex_ctx(tmp_path, grounded)
    lead_id = seed(ctx).lead_id
    assert ctx.ai_coordinator.analyze_lead(lead_id)
    with ctx.db.session() as s:
        lead = s.get(Lead, lead_id)
        row = s.get(AnalysisResult, lead.analysis_id)
        assert row.engine == "ai:exaone" and row.status == "complete" and lead.analysis_status == "complete"


def test_review_switch_off_keeps_rules_only(tmp_path):
    sent = []
    ctx = ex_ctx(tmp_path, lambda r: sent.append(1) or grounded(r), assist_review=False)
    lead_id = seed(ctx).lead_id
    assert not ctx.ai_coordinator.analyze_lead(lead_id)
    assert not sent


def test_ungrounded_exaone_answer_fails_closed(tmp_path):
    def invented(request):
        return exaone_json({"summary": "원문에 없는 요약", "deliverables": [], "tech": [], "questions": [], "uncertain": [],
                            "suggested_intent": "unknown", "evidence_quotes": []})
    ctx = ex_ctx(tmp_path, invented)
    lead_id = seed(ctx).lead_id
    ctx.ai_coordinator.analyze_lead(lead_id)
    with ctx.db.session() as s:
        assert s.get(AnalysisResult, s.get(Lead, lead_id).analysis_id).failure_code == "grounding_error"


def test_stop_pending_ai_covers_exaone(tmp_path):
    ctx = ex_ctx(tmp_path, grounded)
    lead_id = seed(ctx).lead_id
    with ctx.db.session() as s:
        lead = s.get(Lead, lead_id)
        assert s.get(AnalysisResult, lead.analysis_id).engine == "ai:exaone" and lead.analysis_status == "pending"
        assert stop_pending_ai(s) == [lead_id]
    with ctx.db.session() as s:
        assert s.get(Lead, lead_id).analysis_status == "rules_only"


def test_review_request_masks_account_numbers(tmp_path):
    sent = []

    def capture(request):
        sent.append(request.content.decode("utf-8"))
        return grounded(request)
    ctx = ex_ctx(tmp_path, capture)
    lead_id = seed(ctx, body="홈페이지 제작 의뢰합니다. 계약금은 110-123-456789 계좌로 보내드립니다.").lead_id
    ctx.ai_coordinator.analyze_lead(lead_id)
    assert sent
    body = json.loads(sent[0])["messages"][1]["content"]
    assert "110-123-456789" not in "".join(sent) and "[계좌번호]" in body
