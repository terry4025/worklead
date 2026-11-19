"""Title prefilter: real small software requests reach the detail check; coding teacher ads do not."""
import json

from worklead.sources.daangn import create

QUERY = json.dumps(["엑셀", "프로그램", "구글시트", "스프레드시트"], ensure_ascii=False)


def kind(title):
    return create().saved_candidate_kind(title, QUERY)


def test_google_sheets_and_arduino_requests_reach_detail_check():
    assert kind("구글시트로 가계부 같은거 만들기") == "core"
    assert kind("스프레드시트 자동 정리 만들어주실 분") == "core"
    assert kind("코딩 아두이노 오류 봐주실분 구합니다") == "core"
    assert kind("간단한 코딩 해주실 분") == "core"


def test_excel_only_query_stays_narrow():
    assert create().saved_candidate_kind("구글시트로 가계부 같은거 만들기", json.dumps(["엑셀"], ensure_ascii=False)) is None


def test_coding_teacher_titles_stay_out():
    for title in ("ai 로봇 코딩 강사 모집 초보자 가능", "레고 코딩 학원 아이들을 사랑하는 선생님 구인합니다",
                  "코딩 선생님 구해요", "ai 활용 바이브코딩 과외 선생님 찾습니다",
                  "아두이노 코딩 강사 모집", "초등 아두이노 수업 선생님 구해요",
                  "구글시트 강의 해주실 분", "코딩 도와줄 과외 선생님"):
        assert kind(title) is None, title
    assert kind("코딩 해외 취업") != "core"


def test_existing_core_titles_unchanged():
    assert create().saved_candidate_kind("홈페이지 제작 의뢰", json.dumps(["홈페이지"], ensure_ascii=False)) is not None
    assert kind("엑셀 매크로 만들어주실 분") == "core"


def test_programming_and_html_titles_reach_detail_check():
    assert kind("컴퓨터 프로그래밍 가능하신분 견적") == "core"
    assert create().saved_candidate_kind("AI영상/ html 코드 제작가능하신분 찾습니다", json.dumps(["홈페이지"], ensure_ascii=False)) == "core"


def test_programming_teacher_titles_stay_out():
    for title in ("프로그래밍 강사 모집", "html 강의 해주실 선생님"):
        assert create().saved_candidate_kind(title, json.dumps(["프로그램", "홈페이지"], ensure_ascii=False)) is None, title
