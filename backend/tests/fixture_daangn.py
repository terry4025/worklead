"""당근알바 구조를 흉내 낸 합성 사이트 (httpx.MockTransport). 실제 사이트를 호출하지 않는다.

2026-09-26 조사(docs/SOURCE_RESEARCH.md)에서 관찰한 구조만 재현한다:
robots.txt, 사이트맵 색인 → job-posts-N.xml(loc·lastmod), 상세 페이지의 og:title 급여 표시와
페이지 내 공고 데이터(__RELAY_STORE__: 루트 → permalinkPair → daangnPermalink → JobPost, Region).
제목·본문·닉네임은 모두 합성이며 실제 게시글이 아니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, unquote, urlsplit

import httpx

HOST = "jobs.daangn.com"
ROBOTS = "User-agent: *\nDisallow: /me\nDisallow: /auth/\nDisallow: /api/\nDisallow: /job-posts/*/apply\nDisallow: /dev/\nAllow: /\n\nSitemap: https://jobs.daangn.com/sitemap.xml\n"

NOW = datetime.now(UTC)

JOBS: dict[str, dict] = {
    "a1b2c3d4e5f6": {
        "title": "재고 엑셀 매크로 만들어주실 분",
        "content": "재고 엑셀을 자동으로 정리하는 매크로를 만들어주실 분 찾습니다.\n재택으로 진행해도 됩니다. 결과물은 파일로 주고받아요.\n연락처 010-1234-5678",
        "pay": "건당 300,000원",
        "region": ("서울특별시", "마포구", "합정동"),
        "lastmod": NOW - timedelta(hours=3),
    },
    "b2c3d4e5f6g7": {
        "title": "카페 홈페이지 제작 도와주실 분",
        "content": "카페 홈페이지를 같이 만들어주실 분 구해요. 매장으로 출근하셔서 작업해 주셔야 합니다.",
        "pay": "시급 15,000원",
        "region": ("부산광역시", "수영구", "광안동"),
        "lastmod": NOW - timedelta(days=1),
    },
    "c3d4e5f6g7h8": {
        "title": "앱 개발해주실 분 구합니다",
        "content": "간단한 예약 앱 개발해주실 분 구합니다. 원격으로 진행 가능합니다.",
        "pay": "시급 20,000원",
        "region": ("제주특별자치도", "제주시", "노형동"),
        "lastmod": NOW - timedelta(days=2),
        "closed": True,
    },
    "d4e5f6g7h8i9": {
        "title": "주말 홀서버 모집",
        "content": "주말 홀 서빙 알바 구합니다.",
        "pay": "시급 11,000원",
        "region": ("서울특별시", "강남구", "역삼동"),
        "lastmod": NOW - timedelta(hours=5),
    },
    "e5f6g7h8i9j0": {
        "title": "웹사이트 수정 의뢰",
        "content": "오래된 웹사이트 수정 의뢰합니다.",
        "pay": "건당 100,000원",
        "region": ("대구광역시", "중구", "동인동"),
        "lastmod": NOW - timedelta(days=200),
    },
    "f6g7h8i9j0k1": {
        "title": "랜딩페이지 제작",
        "content": "랜딩페이지 제작",
        "pay": "건당 200,000원",
        "region": ("광주광역시", "서구", "치평동"),
        "lastmod": NOW - timedelta(hours=8),
        "broken": True,
    },
}


def job_url(jid: str) -> str:
    slug = JOBS[jid]["title"].replace(" ", "-")
    return f"https://{HOST}/job-posts/{quote(slug)}-{jid}"


def _store(jid: str, closed: bool) -> dict:
    j = JOBS[jid]
    jp_key = f"JobPost:{jid}"
    region_key = f"Region:{jid}"
    pair_key = f'client:root:permalinkPairByPublicId(publicId:"{jid}")'
    link_key = pair_key + ":daangnPermalink"
    return {
        "client:root": {"__id": "client:root", "__typename": "__Root", "me": None, f'permalinkPairByPublicId(publicId:"{jid}")': {"__ref": pair_key}},
        pair_key: {"__typename": "PermalinkPair", "permalink": None, "daangnPermalink": {"__ref": link_key}},
        link_key: {"__typename": "Permalink", "publicId": jid, "model": {"__ref": jp_key}},
        jp_key: {
            "__typename": "JobPost",
            "closed": closed,
            "hidden": False,
            "deleted": False,
            "status": "ACCEPTED",
            "title": j["title"],
            "content": j["content"],
            "title(masking:true)": j["title"],
            "content(masking:true)": j["content"].replace("010-1234-5678", "010-****-****"),
            "createdAt": (j["lastmod"] - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
            "publishedAt": j["lastmod"].isoformat().replace("+00:00", "Z"),
            "closedAt": NOW.isoformat().replace("+00:00", "Z") if closed else None,
            "salary": 1,
            "salaryType": "HOURLY",
            "workDays": [],
            "workDates": [(NOW + timedelta(days=d)).date().isoformat() for d in range(3)],
            "isWorkTimeNegotiable": True,
            "employmentType": "PART_TIME_JOB",
            "workplaceAddress": "합성 상세 주소 1-2",
            "workplaceRegion": {"__ref": region_key},
            "author": {"__ref": "User:1"},
            "lastBringUpDate": None,
        },
        # 후기 영역에 함께 들어 있는 다른 공고 — 이 페이지의 공고로 고르면 안 된다
        "JobPost:other": {"__typename": "JobPost", "title": "짐 옮기기 도와주실 분", "content": "다른 공고", "closed": True},
        region_key: {"__typename": "Region", "name1": j["region"][0], "name2": j["region"][1], "name3": j["region"][2], "name": j["region"][2]},
        "User:1": {"__typename": "User", "nickname": "합성닉네임"},
    }


def detail_html(jid: str, closed: bool) -> str:
    j = JOBS[jid]
    if j.get("broken"):
        return "<html><head><title>당근 알바</title></head><body><div id='new-layout'></div></body></html>"
    store = json.dumps(json.dumps(_store(jid, closed), ensure_ascii=False), ensure_ascii=False)
    return (
        "<!DOCTYPE html><html><head>"
        f"<title>{j['title']}, {j['pay']}</title>"
        f"<meta property=\"og:title\" content=\"{j['title']}, {j['pay']}\"/>"
        f"<meta name=\"description\" content=\"{j['content'][:40]}\"/>"
        "<script type=\"application/ld+json\">{\"@context\":\"https://schema.org\",\"@type\":\"BreadcrumbList\",\"itemListElement\":[]}</script>"
        f"</head><body><h1>{j['title']}</h1><p>합성닉네임</p>"
        f"<script type=\"text/javascript\">window.__RELAY_STORE__ = {store};</script>"
        "</body></html>"
    )


def sitemap_index() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>https://{HOST}/sitemaps/static.xml</loc></sitemap>"
        f"<sitemap><loc>https://{HOST}/sitemaps/job-posts-1.xml</loc></sitemap>"
        f"<sitemap><loc>https://{HOST}/sitemaps/region-searches.xml</loc></sitemap>"
        "</sitemapindex>"
    )


def job_sitemap(extra: int = 0) -> str:
    urls = "".join(
        f"<url><loc>{job_url(jid)}</loc><lastmod>{j['lastmod'].isoformat().replace('+00:00', 'Z')}</lastmod></url>" for jid, j in JOBS.items()
    )
    lm = NOW.isoformat().replace("+00:00", "Z")
    filler = f"https://{HOST}/job-posts/{quote('주말-홀서버-모집합니다-평일-가능')}-"
    urls += "".join(f"<url><loc>{filler}x{n:011d}</loc><lastmod>{lm}</lastmod></url>" for n in range(extra))
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


@dataclass
class Behavior:
    closed: set[str] = field(default_factory=lambda: {jid for jid, j in JOBS.items() if j.get("closed")})
    detail_mode: dict[str, str] = field(default_factory=dict)
    sitemap_mode: str | None = None
    #: 업무 무관 공고를 덧붙여 사이트맵을 키운다 (압축 해제 크기 검증용)
    sitemap_extra: int = 0
    requests: list[str] = field(default_factory=list)


def transport(b: Behavior) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        b.requests.append(unquote(url.path))
        html = {"content-type": "text/html; charset=utf-8"}
        xml = {"content-type": "application/xml; charset=utf-8"}
        if url.hostname != HOST:
            return httpx.Response(599)
        if url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap_index(), headers=xml)
        if url.path == "/sitemaps/job-posts-1.xml":
            if b.sitemap_mode == "broken":
                return httpx.Response(200, text="<html><body>점검 중</body></html>", headers=html)
            if b.sitemap_mode == "403":
                return httpx.Response(403)
            return httpx.Response(200, text=job_sitemap(b.sitemap_extra), headers=xml)
        if url.path.startswith("/job-posts/"):
            jid = url.path.rsplit("-", 1)[-1]
            mode = b.detail_mode.get(jid)
            if mode == "404":
                return httpx.Response(404)
            if jid not in JOBS:
                return httpx.Response(404)
            return httpx.Response(200, text=detail_html(jid, jid in b.closed), headers=html)
        return httpx.Response(404)

    return httpx.MockTransport(handler)
