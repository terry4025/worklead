"""테스트용 합성 구인 사이트 (httpx.MockTransport). 실제 사이트를 호출하지 않는다.

지역별 검색 + 페이지 이동 + JobPosting JSON-LD 상세 페이지를 흉내 내어,
당근알바 조사가 끝났을 때 쓰일 것과 같은 프로필 기반 어댑터 경로를 검증한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

from worklead.sources.profiled import ProfiledSiteAdapter

HOST = "jobs.fixture.test"

REGIONS = [
    {"param": "seoul-gangnam", "label": "서울 강남구", "sido": "서울특별시"},
    {"param": "seoul-mapo", "label": "서울 마포구", "sido": "서울특별시"},
    {"param": "busan-suyeong", "label": "부산 수영구", "sido": "부산광역시"},
    {"param": "busan-haeundae", "label": "부산 해운대구", "sido": "부산광역시"},
    {"param": "jeju-jeju", "label": "제주 제주시", "sido": "제주특별자치도"},
]

JOBS: dict[str, dict] = {
    "101": {
        "title": "쇼핑몰 주문 엑셀 매크로 만들어주실 분",
        "description": "<p>주문 엑셀을 자동 정리하는 매크로를 만들어주실 분 구합니다.</p><p>작업은 전 과정 원격으로 진행합니다. 지역 상관없이 지원 가능합니다.</p><p>예산 60만원</p>",
        "regions": ["seoul-gangnam"],
    },
    "102": {
        "title": "카페 랜딩페이지 제작 의뢰",
        "description": "<p>카페 소개 랜딩페이지 제작을 의뢰합니다.</p><p>미팅 없이 온라인으로만 진행, 재택 작업 가능합니다.</p><p>예산은 150만원입니다.</p>",
        "regions": ["busan-suyeong", "busan-haeundae"],  # 두 지역에서 같은 공고
    },
    "103": {
        "title": "온라인 쇼핑몰 CS 직원 (주 5일 출근)",
        "description": "<p>상품 등록과 고객 응대 업무입니다. 주 5일 출근, 월급 220만원, 4대보험.</p>",
        "regions": ["seoul-mapo"],
    },
    "104": {
        "title": "홈페이지 제작해 드립니다",
        "description": "<p>홈페이지 제작해 드립니다. 30만원부터.</p>",
        "regions": ["seoul-mapo"],
    },
    "105": {
        "title": "예약 페이지 개발 외주",
        "description": "<p>미용실 예약 페이지 개발 외주 맡기려고 합니다. 원격 작업 가능.</p>",
        "regions": ["jeju-jeju"],
        "page": 2,
    },
    "106": {
        "title": "관리자 페이지 수정 의뢰",
        "description": "<p>기존 사이트 관리자 페이지 수정해 주실 분 구합니다. 비대면 진행, 예산 40만원.</p>",
        "regions": ["jeju-jeju"],
    },
}


@dataclass
class SiteBehavior:
    robots: str = "User-agent: *\nDisallow: /private\n"
    robots_status: int = 200
    #: 지역별 특수 응답: "403" | "429" | "broken" | "empty"
    region_mode: dict[str, str] = field(default_factory=dict)
    detail_mode: dict[str, str] = field(default_factory=dict)
    closed: set[str] = field(default_factory=set)
    requests: list[str] = field(default_factory=list)


def _list_page(region: str, page: int) -> str:
    ids = [jid for jid, j in JOBS.items() if region in j["regions"] and j.get("page", 1) == page]
    if not ids:
        return "<html><body><div class='empty'>검색 결과가 없어요</div></body></html>"
    links = "".join(f"<li><a href='/jobs/{jid}'>{JOBS[jid]['title']}</a></li>" for jid in ids)
    return f"<html><body><ul>{links}</ul><a href='/about'>소개</a></body></html>"


def _detail(jid: str, closed: bool) -> str:
    j = JOBS[jid]
    now = datetime.now(UTC)
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": j["title"],
        "description": j["description"],
        "datePosted": (now - timedelta(hours=10)).isoformat(),
        "validThrough": (now + (timedelta(days=-1) if closed else timedelta(days=7))).date().isoformat(),
        "identifier": {"@type": "PropertyValue", "value": jid},
        "jobLocation": {"@type": "Place", "address": {"addressRegion": "서울", "addressLocality": "강남구"}},
    }
    marker = "<div>모집마감</div>" if closed else ""
    return f"<html><head><script type='application/ld+json'>{json.dumps(posting, ensure_ascii=False)}</script></head><body>{marker}<h1>{j['title']}</h1></body></html>"


def transport(behavior: SiteBehavior) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        behavior.requests.append(f"{url.path}?{url.query}" if url.query else url.path)
        if url.hostname != HOST:
            return httpx.Response(599)
        if url.path == "/robots.txt":
            return httpx.Response(behavior.robots_status, text=behavior.robots)
        if url.path == "/search":
            q = parse_qs(url.query)
            region = q.get("region", [""])[0]
            page = int(q.get("page", ["1"])[0])
            mode = behavior.region_mode.get(region)
            if mode == "403":
                return httpx.Response(403, text="forbidden")
            if mode == "429":
                return httpx.Response(429, headers={"Retry-After": "120"}, text="slow down")
            if mode == "broken":
                return httpx.Response(200, text="<html><body><div id='new-layout'></div></body></html>")
            if mode == "captcha":
                return httpx.Response(200, text="<html><body><div class='g-recaptcha'></div></body></html>")
            return httpx.Response(200, text=_list_page(region, page), headers={"content-type": "text/html; charset=utf-8"})
        if url.path.startswith("/jobs/"):
            jid = url.path.rsplit("/", 1)[1]
            mode = behavior.detail_mode.get(jid)
            if mode == "404":
                return httpx.Response(404)
            if mode == "403":
                return httpx.Response(403)
            if mode == "noscript":
                return httpx.Response(200, text="<html><body>no data</body></html>")
            if jid not in JOBS:
                return httpx.Response(404)
            return httpx.Response(200, text=_detail(jid, jid in behavior.closed), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def profile(region_status: str = "unverified", regions: list[dict] | None = None, budget: int = 500) -> dict:
    return {
        "schema_version": 1,
        "source_id": "fixture-site",
        "verified": True,
        "allowed_hosts": [HOST],
        "request_budget_per_day": budget,
        "min_interval_seconds": 0,
        "search": {
            "status": "verified",
            "url_template": f"https://{HOST}/search?q={{query}}&region={{region}}&page={{page}}",
            "query_mode": "joined",
            "joiner": " ",
            "first_page": 1,
            "max_pages": 2,
        },
        "regions": {"status": region_status, "items": regions if regions is not None else REGIONS},
        "list": {"detail_link_pattern": r"/jobs/\d+$", "post_id_pattern": r"/jobs/(\d+)$", "empty_markers": ["검색 결과가 없어요"]},
        "detail": {
            "strategy": "jsonld_jobposting",
            "closed_markers": ["모집마감"],
            "open_markers": [],
            "valid_through_means_open": True,
            "deletion_reliable": False,
        },
        "capabilities": {"region_search": {"support": "supported"}, "detail": {"support": "supported"}},
    }


def adapter(**kw) -> ProfiledSiteAdapter:
    return ProfiledSiteAdapter(profile(**kw), name="합성 테스트 사이트", scope_note="테스트 전용", adapter_version="test", parser_version="fixture-1")
