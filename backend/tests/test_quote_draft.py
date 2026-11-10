import pytest

from tests.conftest import client_for
from tests.exaone_fixtures import ex_ctx, exaone_json
from tests.test_ai_runtime_quality import seed
from worklead.analysis.provider import AIFailure
from worklead.analysis.types import Profile
from worklead.analysis.writer import quote_step, validate_quote

SRC = "홈페이지 제작 의뢰\n홈페이지 제작 의뢰합니다. 원격으로 진행합니다. 건당 100만원"
QUOTE = ("견적서 초안\n이해한 문제: 원격으로 홈페이지를 새로 만들고 싶으십니다.\n1단계 필수: 소개 화면 제작 [금액] / [기간]\n"
         "2단계 선택: 문의 양식 연결 [금액] / [기간]\n제외 범위: 호스팅 비용\n리스크: 사진 자료가 필요합니다.\n"
         "추가 요청: 범위를 다시 정한 뒤 별도 견적\n유지보수: 구축비의 10%/년 기준\n결제 조건: [결제 조건]")


def test_quote_needs_placeholders_and_no_invented_amounts():
    assert validate_quote(QUOTE, source=SRC, profile=Profile())
    with pytest.raises(AIFailure):
        validate_quote(QUOTE.replace("[금액]", "30만원", 1), source=SRC, profile=Profile())
    with pytest.raises(AIFailure):
        validate_quote(QUOTE.replace("[금액]", "금액 협의"), source=SRC, profile=Profile())


@pytest.mark.parametrize("old,new", [
    ("[금액] / [기간]", "10만 / 2주"),
    ("[금액]", "5천원"),
    ("[기간]", "3개월"),
    ("[결제 조건]", "계약금 10%, 잔금 완료 후"),
    ("[결제 조건]", "[결제 조건]\n계약금 30%"),
    ("호스팅 비용", "호스팅 비용 5"),
])
def test_quote_rejects_ai_set_money_period_or_terms(old, new):
    with pytest.raises(AIFailure):
        validate_quote(QUOTE.replace(old, new, 1), source=SRC, profile=Profile())


def test_quote_allows_source_amount_and_spaced_structure():
    text = QUOTE.replace("구축비의 10%/년", "구축비의 10 % / 년").replace("1단계", "1 단계") + "\n참고: 공고 금액 건당 100만 원"
    assert validate_quote(text, source=SRC, profile=Profile())


def test_quote_request_round_trip(tmp_path):
    ctx = ex_ctx(tmp_path, lambda r: exaone_json({"text": QUOTE}))
    lead_id = seed(ctx).lead_id
    client = client_for(ctx)
    assert client.get(f"/v1/leads/{lead_id}/quote-draft").json()["state"] == "none"
    assert client.post(f"/v1/leads/{lead_id}/quote-draft").json()["state"] == "pending"
    quote_step(ctx)
    body = client.get(f"/v1/leads/{lead_id}/quote-draft").json()
    assert body["state"] == "ready" and "[금액]" in body["text"]


def test_quote_switch_off_is_409(tmp_path):
    ctx = ex_ctx(tmp_path, assist_quote=False)
    lead_id = seed(ctx).lead_id
    r = client_for(ctx).post(f"/v1/leads/{lead_id}/quote-draft")
    assert r.status_code == 409 and r.json()["error"]["code"] == "assist_disabled"


FILLED = [
    "원격으로 회사 소개 홈페이지를 새로 만들고 싶으십니다",
    "소개 화면과 서비스 안내 화면 제작",
    "문의 양식과 알림 연결",
    "호스팅과 도메인 비용, 사진 촬영",
    "사진과 소개 문구 자료를 미리 받아야 일정이 맞습니다",
    "범위를 넘는 요청은 다시 정한 뒤 별도로 견적을 드립니다",
]


def test_filled_literal_template_passes_validation():
    from worklead.analysis.writer import QUOTE_TEMPLATE
    parts = QUOTE_TEMPLATE.split("…")
    assert len(parts) == len(FILLED) + 1
    text = "".join(p + f for p, f in zip(parts, FILLED)) + parts[-1]
    assert len(text) >= 150  # QuoteOut min_length
    assert validate_quote(text, source=SRC, profile=Profile()) == text


def test_quote_prompt_overrides_playbook_rules(tmp_path):
    import json

    from worklead.analysis.prompts import PLAYBOOK
    from worklead.analysis.writer import QUOTE_OVERRIDE, QUOTE_TEMPLATE
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return exaone_json({"text": QUOTE})

    ctx = ex_ctx(tmp_path, handler)
    lead_id = seed(ctx).lead_id
    client = client_for(ctx)
    client.post(f"/v1/leads/{lead_id}/quote-draft")
    quote_step(ctx)
    assert seen and seen[0].index(PLAYBOOK) < seen[0].index(QUOTE_OVERRIDE) and QUOTE_TEMPLATE in seen[0]


LEAKED_TAIL = ("}略前面逸出，不用在续级。考え直し前面正文里… "
               "Wait the prior assistant final appears to have included JSON with")


def test_leaked_reasoning_tail_is_rejected():
    with pytest.raises(AIFailure, match="모델 내부 문장"):
        validate_quote(QUOTE + LEAKED_TAIL, source=SRC, profile=Profile())
