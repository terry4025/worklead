"""Offline title discovery, using real observed title shapes and synthetic IDs."""

import json
from datetime import UTC, datetime
from html import escape
from urllib.parse import quote

import pytest

from worklead.engine.http import FetchResult
from worklead.sources.base import DiscoverResult, SourceNotReady, TaskSpec
from worklead.sources.daangn import create


NOW = datetime(2026, 9, 30, tzinfo=UTC)
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
MAP = "https://jobs.daangn.com/sitemaps/job-posts-1.xml"


class LocalSitemap:
    def __init__(self, titles):
        entries = []
        for index, title in enumerate(titles):
            url = "https://jobs.daangn.com/job-posts/" + quote(title.replace(" ", "-")) + f"-sample{index:06d}"
            entries.append(f"<url><loc>{escape(url)}</loc><lastmod>2026-09-28T00:00:00Z</lastmod></url>")
        self.xml = f'<urlset {NS}>' + "".join(entries) + "</urlset>"
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        text = f'<sitemapindex {NS}><sitemap><loc>{MAP}</loc></sitemap></sitemapindex>' if url.endswith("/sitemap.xml") else self.xml
        return FetchResult(url, url, 200, "ok", text)


def discover(titles, keywords, *, groups=None):
    adapter = create()
    plan = adapter.plan_discovery(groups or [{"id": "custom", "enabled": True, "keywords": keywords}], NOW)
    fetcher = LocalSitemap(titles)
    result = adapter.discover(plan.tasks[0], fetcher)
    assert fetcher.requests == ["https://jobs.daangn.com/sitemap.xml", MAP]
    return result


def titles(refs):
    return {ref.title_hint for ref in refs}


def test_observed_web_service_title_and_recent_website_request_keep_core_priority():
    # First title is from the saved 2026-09-28 public sitemap; second is L135.
    source_titles = ["aws gcp이용 웹 서비스 배포", "홈페이지 제작좀 도와주실분"]
    result = discover(source_titles, ["웹사이트"])
    assert titles(result.refs) == set(source_titles)
    assert result.exploration_refs == []
    assert result.metadata["title_candidates"] == result.metadata["core_candidates"] == 2
    assert result.metadata["exploration_candidates"] == 0


@pytest.mark.parametrize(("keyword", "title"), [
    ("파이썬", "Python 프로그램 제작 의뢰"),
    ("소프트웨어", "프로그램 수정 요청"),
    ("홈페이지", "웹 개발 의뢰"),
])
def test_named_technical_aliases_preserve_active_query_scope(keyword, title):
    result = discover([title], [keyword])
    assert titles(result.refs) == {title}


def test_excel_only_does_not_silently_collect_unselected_website_candidates():
    result = discover(["홈페이지 제작 의뢰", "엑셀 매크로 수정 요청", "노션 정리 의뢰"], ["엑셀"])
    assert titles(result.refs) == {"엑셀 매크로 수정 요청"}
    assert result.exploration_refs == []


def test_disabled_website_group_is_not_revived_by_synonyms():
    result = discover(["웹서비스 개발 의뢰", "엑셀 매크로 요청"], [], groups=[
        {"id": "web", "enabled": False, "keywords": ["홈페이지"]},
        {"id": "sheet", "enabled": True, "keywords": ["엑셀"]},
    ])
    assert titles(result.refs) == {"엑셀 매크로 요청"}
    assert result.exploration_refs == []


def test_all_search_groups_disabled_still_stop_discovery():
    with pytest.raises(SourceNotReady, match="활성화된 검색어"):
        create().plan_discovery([{"id": "web", "enabled": False, "keywords": ["홈페이지"]}], NOW)


def test_specific_custom_keywords_promote_only_explicit_software_delivery():
    source_titles = ["노션 고수님", "React 화면 수정 의뢰"]
    result = discover(source_titles + ["식당 직원 구합니다"], ["노션", "React"])
    assert titles(result.refs) == {"React 화면 수정 의뢰"}
    assert titles(result.exploration_refs) == {"노션 고수님"}
    assert result.metadata["title_candidates"] == 1
    assert result.metadata["exploration_candidates"] == 1


def test_generic_recruitment_words_require_an_actual_creation_request():
    result = discover([
        "식당 직원 구합니다", "직원 모집", "인원 의뢰", "물품 제작 의뢰",
        "네이버스토어 만들어주실 분 구합니다", "그림 수정좀 도와주실분",
    ], ["구합니다", "모집", "의뢰", "도와주실"])
    assert result.refs == []
    assert titles(result.exploration_refs) == {
        "물품 제작 의뢰", "네이버스토어 만들어주실 분 구합니다", "그림 수정좀 도와주실분",
    }


def test_weak_requests_must_still_match_an_enabled_keyword():
    result = discover(["물품 제작 의뢰", "노션 고수님"], ["엑셀"])
    assert result.refs == []
    assert result.exploration_refs == []


def test_observed_shop_staff_titles_do_not_spend_exploration_requests():
    staff_titles = [
        "용달차 짐옮기기 의류쇼핑몰 짐", "쇼핑몰 사무업무 오후 근무자 모집",
        "의류쇼핑몰 물류이동 힘좀쓰시고손빠른분", "인터넷쇼핑몰매니저 옷핏좋은분 단순컴터업무",
        "쇼핑몰 MD", "쇼핑몰 검수 파트타임 모집", "온라인 쇼핑몰 보조업무 해주실 오전 알바분 구합",
        "라라베리 쇼핑몰 검수 포장 단순 작업 하실분 구해요", "쇼핑몰 재품각인 생산보조",
    ]
    result = discover(staff_titles, ["쇼핑몰", "구합니다", "모집", "도와주실"])
    assert result.refs == []
    assert result.exploration_refs == []


def test_shop_creation_renewal_and_specific_custom_requests_remain_candidates():
    result = discover([
        "쇼핑몰 제작 의뢰", "쇼핑몰 리뉴얼 의뢰", "쇼핑몰 유지보수 요청",
        "쇼핑몰 만들어주실 분", "React 쇼핑몰 화면 도와주실 분",
    ], ["쇼핑몰", "React"])
    assert titles(result.refs) == {"쇼핑몰 제작 의뢰"}
    assert titles(result.exploration_refs) == {
        "쇼핑몰 리뉴얼 의뢰", "쇼핑몰 유지보수 요청", "쇼핑몰 만들어주실 분",
        "React 쇼핑몰 화면 도와주실 분",
    }


def test_core_and_exploration_are_disjoint_and_legacy_query_lists_still_work():
    adapter = create()
    spec = TaskSpec("national-title-v1", "nationwide-sitemap", "전국", json.dumps(["홈페이지", "노션"]))
    result = adapter.discover(spec, LocalSitemap(["웹 서비스 개발 의뢰", "노션 고수님"]))
    assert titles(result.refs) == {"웹 서비스 개발 의뢰"}
    assert titles(result.exploration_refs) == {"노션 고수님"}
    assert not ({ref.source_post_id for ref in result.refs} & {ref.source_post_id for ref in result.exploration_refs})


def test_existing_discover_result_positional_contract_is_unchanged():
    old = DiscoverResult([], None, {"existing": True})
    assert old.metadata == {"existing": True}
    assert old.exploration_refs == []


def test_bounded_exploration_policy_and_scope_note_are_explicit():
    adapter = create()
    config = adapter.profile["discovery"]
    assert config["exploration_max_requests"] == 50
    assert config["exploration_reserve_requests"] == 30
    plan = adapter.plan_discovery([{"id": "web", "keywords": ["홈페이지"]}], NOW)
    assert any("제한" in note and "본문" in note for note in plan.notes)


@pytest.mark.parametrize(("title", "keywords"), [
    ("사이트 만들어주실분", ["홈페이지"]),
    ("구형맥(mac) AI 에이전트 만들어 주실분", ["만들어주실"]),
    ("챗봇 개발 의뢰", ["의뢰"]),
    ("고객 상담 bot 만들어 주실 분", ["만들어주실"]),
    ("bot 만들어 주실 분", ["봇"]),
    ("AI agent 제작 요청", ["AI 에이전트"]),
    ("HTML CSS 화면 수정 의뢰", ["의뢰"]),
    ("website development request", ["홈페이지"]),
    ("Build a website", ["웹사이트"]),
    ("chatbot development request", ["챗봇"]),
    ("React code fix request", ["React"]),
])
def test_explicit_software_delivery_titles_have_core_priority(title, keywords):
    result = discover([title], keywords)
    assert titles(result.refs) == {title}
    assert result.exploration_refs == []


@pytest.mark.parametrize("title", [
    "팬케이크 만들어주실 분", "일주일 반찬 만들어주실 분", "아이들 저녁 만들어주실 분",
    "목공 선반 제작 의뢰", "가구 제작 외주 의뢰", "청소 용역 외주 의뢰",
    "물류 포장 외주 의뢰", "용달 짐옮기기 외주 의뢰",
])
def test_obvious_physical_work_does_not_consume_weak_exploration(title):
    result = discover([title], ["만들어주실", "의뢰"])
    assert result.refs == []
    assert result.exploration_refs == []


def test_physical_domain_words_do_not_suppress_software_delivery():
    source_titles = [
        "음식 배송 자동화 프로그램 제작 의뢰", "목공 예약 사이트 만들어주실분",
        "물류 관리 chatbot 개발 의뢰",
    ]
    result = discover(source_titles, ["의뢰", "만들어주실"])
    assert titles(result.refs) == set(source_titles)


def test_bare_site_promotion_and_agent_recruitment_are_not_software_orders():
    result = discover([
        "구인사이트 링크 홍보", "사이트 가입 구합니다", "AI 에이전트 모집",
        "bot 사용 가능한분 모집",
    ], ["홈페이지", "구합니다", "모집"])
    assert result.refs == []
    assert result.exploration_refs == []


def test_new_software_patterns_still_respect_excel_only_search():
    result = discover(["사이트 만들어주실분", "AI 에이전트 만들어주실분"], ["엑셀"])
    assert result.refs == []
    assert result.exploration_refs == []


@pytest.mark.parametrize("title", [
    "React 물류 화면 도와주실 분", "React 배송 화면 도와주실 분",
    "React 목공 예약 컴포넌트 도와주실 분",
])
def test_technical_software_objects_preserve_domain_specific_weak_candidates(title):
    result = discover([title], ["React"])
    assert result.refs == []
    assert titles(result.exploration_refs) == {title}


@pytest.mark.parametrize(("title", "keyword"), [
    ("물류 포장 외주 의뢰", "물류"), ("목공 선반 제작 의뢰", "목공"),
    ("배송 대행 외주 의뢰", "배송"),
    ("React 사용 가능한 분 물류 포장 외주 의뢰", "React"),
])
def test_custom_keywords_alone_do_not_reinstate_physical_work(title, keyword):
    result = discover([title], [keyword])
    assert result.refs == []
    assert result.exploration_refs == []
