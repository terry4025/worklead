"""Unworded software requests seen in live Daangn posts (2026-11-10..19) go to manual review, never auto-apply."""
import pytest

from tests.test_software_request_coverage import check

POSITIVE = [
    ("AI 활용 웹사이트 제작", "자꾸 AI로 15분 30분에 해준신다고 하는데 4시간에 제대로 빌딩 완료하실수 있는 분이면 충분합니다. 소요시간 작업일자등은 협의가능합니다. 제작배포까지 책임감있게 약속지키시면 계속 드립니다"),
    ("코딩할 수 있는 분 구합니다", "HTML과 CSS를 다룰 줄 아시는 분을 찾고 있어요. 데이터 정리 자동화 작업과 함께 코드 수정 및 오류 해결을 도와주시면 됩니다."),
    ("원격결재 시스템 구축", "1.원격 결재시스템 구축. 2.전기 자전거 홍보 브러그 구축 1.2번을 완성하고.브러그 활동을 도와주실 분."),
    ("상세페이지 제작 가능한분 구함(이미지 다운가능해야함)", "상세페이지 제작 가능한 분 구합니다. 상세페이지에 들어갈 이미지는 합법적인 사이트에서 다운로드 가능한 분이었으면 좋겠습니다.. 건티슈 판매 관련 상세페이지입니다"),
    ("웹디자이너) 재택 / 홈페이지 디자인 작업 가능하신분", "홈페이지 디자인재택으로 가능하신 분 한분 모집합니다. UX/UI 개발은 저희가 진행합니다. 많은 지원 바랍니다."),
]
NEGATIVE = [
    ("바이브코딩 알려주실분", "바이브 코딩 알려주실분 구합니다 ** 문자바랍니다"),
    ("코딩 과학 파트강사 구합니다", "초등 코딩 과학 수업을 맡아 주실 강사님을 모집합니다."),
    ("자동화설비 제작 및 셋업 팀원 충원", "자동화 설비 제작 및 셋업을 함께할 분 모집합니다."),
    ("쇼핑몰 포장 알바 구해요", "쇼핑몰 상품 포장 도와주실 분 구합니다."),
    # Live replay false positives (2026-11-19): hiring and how-to posts.
    ("AI IT 개발 자 및 이지바로 회계 사업관리 구인", "회계 프로그램 개발과 사업관리를 함께하실 분 모집합니다."),
    ("(수습) 영상관제 시스템 개발. (12월~2월", "영상관제 시스템 개발 업무를 함께하실 분 모집합니다."),
    ("피그마 상세페이지 gif 넣고 추출하는법", "피그마 상세페이지 GIF 작업 도와주실 분 맥북 사용 중이구요. 이미 상세페이지는 만들어 두었는데 그 안에 들어갈 이미지 몇 개를 GIF로 첨부하고 내보내는 과정을 도와주실 분이면 좋겠어요."),
]


@pytest.mark.parametrize("title,body", POSITIVE)
def test_unworded_software_request_goes_to_review_with_inferred_basis(title, body):
    result = check(title, body)
    intent = result.judgements["demand_intent"]
    assert (intent.value, intent.basis) == ("buyer_project", "inferred")
    assert result.recommendation == "needs_review"
    assert any(r.text == "의뢰 표현 추정 — 원문 확인" for r in result.reasons)


@pytest.mark.parametrize("title,body", NEGATIVE)
def test_teaching_physical_and_packing_posts_are_not_inferred(title, body):
    assert check(title, body).judgements["demand_intent"].basis != "inferred"


def test_explicit_buyer_keeps_explicit_basis():
    intent = check("홈페이지 리뉴얼 도와주실분", "홈페이지 리뉴얼을 도와주실 분을 찾고 있어요.").judgements["demand_intent"]
    assert intent.basis == "explicit"
