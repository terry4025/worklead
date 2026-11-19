"""Explicit software delivery variants observed in local public records."""
import pytest

from tests.test_software_request_coverage import check


@pytest.mark.parametrize("title,body", [
    ("홈페이지 리뉴얼 도와주실분", "홈페이지 리뉴얼을 도와주실 분을 찾고 있어요. 쇼핑 기능을 추가하는 작업이 필요합니다."),
    ("웹 및 어플제작", "웹&앱 모티브를 드리면 구현도 해주실분 구합니다. 제작완료 후 넘겨주세요."),
    ("앱 패키징과 등록", "웹사이트를 앱으로 등록하는 작업을 도와주실 분을 찾습니다. 앱 내 오류 수정 작업을 부탁드립니다."),
    ("웹페이지 편집 및 디자인 작업", "개발중인 웹사이트 페이지, 프로젝트 일정에 필요한 디자인작업등을 도와주실 분을 찾습니다."),
])
def test_named_software_delivery_help_is_buyer_with_evidence_and_no_remote_invention(title, body):
    result = check(title, body)
    intent = result.judgements["demand_intent"]
    assert intent.value == "buyer_project" and intent.basis == "explicit"
    assert result.recommendation == "needs_review"
    assert result.judgements["work_mode"].value == "unknown"
    assert result.dev_relevant
    evidence = intent.evidence[0]
    assert evidence.quote and (evidence.start is None or (body + '\n건당 30만원')[evidence.start:evidence.end] == evidence.quote)
    assert check(title, body, pay="시급 2만원").recommendation == "excluded"


@pytest.mark.parametrize("title,body", [
    ("홈페이지 작업 직원 모집", "홈페이지 리뉴얼을 도와주실 분. 직원 채용합니다. 월급 300만원, 주 5일."),
    ("앱 등록 대행", "웹사이트를 앱으로 등록해 드립니다."),
    ("웹사이트 과외", "웹사이트 리뉴얼 사용법을 가르쳐 주실 선생님을 찾습니다."),
    ("웹페이지 디자인 조언", "포트폴리오 웹페이지 디자인 방향에 대한 조언을 주세요."),
    ("행사 디자인", "행사 현수막 디자인작업등을 도와주실 분을 찾습니다."),
    ("사무실 리뉴얼", "사무실 리뉴얼을 도와주실 분을 찾습니다."),
    ("앱 설치 부업", "앱을 설치하고 회원가입을 도와주실 분을 찾습니다."),
    ("앱 개발 테스트 부업", "앱을 설치하고 회원 등록을 도와주실 분을 찾습니다. 새 계정을 만드는 일입니다."),
])
def test_non_delivery_employment_advice_and_physical_help_stay_excluded(title, body):
    assert check(title, body).recommendation == "excluded"
