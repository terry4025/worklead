"""알바몬 구조를 흉내 낸 합성 사이트 (httpx.MockTransport). 실제 사이트를 호출하지 않는다.

2026-09-26 조사(docs/SOURCE_RESEARCH_ALBAMON.md)에서 관찰한 구조만 재현한다: 목록 페이지의 `__NEXT_DATA__`
(dehydratedState.queries[0].state.data.base.normal.collection), 상세의 JobPosting JSON-LD 와 viewData.content.
제목·본문·연락처는 모두 합성이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

HOST = "www.albamon.com"
ROBOTS = (
    "User-agent: *\nDisallow: /jobs/detail/*?*keyword\nDisallow: /jobs/detail-content\nDisallow: /jobs/detail/content\n"
    "Disallow: /jobs/detail/manager\nDisallow: /jobs/apply/\n\nSitemap: https://www.albamon.com/sitemap.xml\n"
)
TODAY = datetime.now(UTC).date()

# 목록 항목 (list) + 상세 (detail)
JOBS: dict[str, dict] = {
    "900001": {
        "list": "part-9060",
        "title": "쇼핑몰 관리자 페이지 수정 (재택, 단기)",
        "payType": "건별",
        "pay": 300000,
        "unitText": "HOUR",
        "parts": ["프로그래머", "HTML코딩"],
        "remote": True,
        "period": "1개월~3개월",
        "types": ["프리랜서"],
        "content": "<p>운영 중인 쇼핑몰 관리자 페이지 일부를 수정해 주실 분.</p><p>재택근무로 진행하며 미팅 없이 결과물은 온라인으로 공유합니다.</p><p>문의: 010-1234-5678 / owner@example.com</p>",
    },
    "900002": {
        "list": "part-9060",
        "title": "[국비지원] 풀스택 개발 교육생 모집",
        "payType": "",
        "pay": 0,
        "parts": ["프로그래머"],
        "content": "<p>교육생 모집</p>",
    },
    "900003": {
        "list": "part-9060",
        "title": "시니어 백엔드 개발자",
        "payType": "연봉",
        "pay": 60000000,
        "parts": ["프로그래머"],
        "content": "<p>정규직</p>",
    },
    "900004": {
        "list": "part-9060",
        "title": "웹개발 코딩 알바",
        "payType": "시급",
        "pay": 15000,
        "unitText": "HOUR",
        "parts": ["프로그래머"],
        "remote": False,
        "period": "1개월~3개월",
        "types": ["알바"],
        "content": "<p>웹개발 코딩 가능한 분. 매일 사무실로 출근하셔서 근무합니다.</p>",
    },
    "900005": {
        "list": "telecommuting",
        "title": "재택 상담원 모집",
        "payType": "월급",
        "pay": 2500000,
        "parts": ["고객상담·인바운드"],
        "content": "<p>상담</p>",
    },
    "900006": {
        "list": "telecommuting",
        "title": "엑셀 반복작업 자동화 재택",
        "payType": "건별",
        "pay": 100000,
        "parts": ["사무보조", "데이터수집·가공"],
        "remote": True,
        "period": "1주일~1개월",
        "types": ["프리랜서"],
        "content": "<p>매일 하는 엑셀 정리 작업을 매크로로 자동화해 주실 분 구합니다.</p><p>재택근무, 건별 지급.</p>",
    },
    "900007": {
        "list": "part-9005",
        "title": "-급반짝- 하루 3시간 5만원 보장분석 체험단 모집",
        "payType": "시급",
        "pay": 16000,
        "parts": ["웹·콘텐츠기획"],
        "content": "<p>체험단</p>",
    },
    "900008": {
        "list": "part-9005",
        "title": "회사 홈페이지 리뉴얼 작업자 구합니다",
        "payType": "건별",
        "pay": 800000,
        "parts": ["웹·콘텐츠기획", "웹·모바일디자인"],
        "remote": True,
        "period": "1주일~1개월",
        "types": ["프리랜서"],
        "content": "<p>회사 홈페이지를 새로 만들어 주실 분. 미팅 없이 온라인으로만 진행합니다.</p>",
    },
    "900009": {
        "list": "part-9060",
        "title": "웹개발 코딩 알바",
        "payType": "시급",
        "pay": 15000,
        "parts": ["프로그래머"],
        "content": "<p>같은 제목 반복 게시</p>",
    },
    "900010": {
        "list": "part-9060",
        "title": "상품 정보 수집 프로그램 개발 알바",
        "payType": "건별",
        "pay": 500000,
        "parts": ["프로그래머"],
        "remote": False,
        "types": ["알바"],
        "content": "<p>여러 쇼핑몰 상품 정보를 모으는 프로그램을 만들어 주실 분.</p>",
    },
}


def list_html(list_id: str) -> str:
    items = [
        {
            "recruitNo": int(jid),
            "recruitTitle": j["title"],
            "postedDate": "1시간전",
            "payType": {"key": "X", "value": "X", "description": j["payType"]},
            "pay": f"{j['pay']:,}원" if j["pay"] else "",
            "parts": j["parts"],
            "workplaceArea": "재택근무" if j.get("remote") else "서울 강남구",
            "managerPhoneNumber": "010-9999-0000",
        }
        for jid, j in JOBS.items()
        if j["list"] == list_id
    ]
    data = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {"queryKey": ["RECRUIT_PART_LIST"], "state": {"data": {"base": {"pagination": {"page": 1, "size": 20, "totalCount": len(items)}, "normal": {"collection": items}}}}}
                    ]
                }
            }
        }
    }
    return f'<html><head><title>목록</title></head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></body></html>'


def detail_html(jid: str) -> str:
    j = JOBS[jid]
    posting = {
        "@context": "http://schema.org",
        "@type": "JobPosting",
        "title": j["title"],
        "datePosted": (TODAY - timedelta(days=1)).isoformat(),
        "validThrough": (TODAY + timedelta(days=7)).isoformat(),
        "employmentType": ["FREE_LANCER"],
        "description": "합성 회사에서 채용을 진행합니다.",
        "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "streetAddress": "합성로 1 2층", "addressLocality": "강남구", "addressRegion": "서울특별시"}}],
        "baseSalary": {"@type": "MonetaryAmount", "currency": "KRW", "value": {"@type": "QuantitativeValue", "value": j["pay"], "unitText": j.get("unitText", "HOUR")}},
    }
    if j.get("remote"):
        posting["jobLocationType"] = "TELECOMMUTE"
    view = {
        "recruitTitle": j["title"],
        "content": j["content"],
        "postStatus": {"key": "OPEN", "value": "1", "description": "게재중인 공고"},
        "workPeriod": {"value": "30", "description": j.get("period", "")},
        "salaryType": {"key": "X", "value": "X", "description": j["payType"]},
        "employmentType": [{"key": "K", "value": 1, "description": t} for t in j.get("types", [])],
        "managerPhoneNumber": "010-9999-0000",
    }
    nd = {"props": {"pageProps": {"data": {"viewData": view}}}}
    return (
        f"<html><head><title>{j['title']} | 알바몬</title>"
        f'<script type="application/ld+json">{json.dumps([posting], ensure_ascii=False)}</script></head><body>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd, ensure_ascii=False)}</script></body></html>'
    )


@dataclass
class Behavior:
    list_mode: dict[str, str] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)


def transport(b: Behavior) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        b.requests.append(url.path + (f"?{url.query}" if url.query else ""))
        html = {"content-type": "text/html; charset=utf-8"}
        if url.hostname != HOST:
            return httpx.Response(599)
        if url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if url.path in ("/jobs/part", "/jobs/telecommuting"):
            list_id = "telecommuting" if url.path == "/jobs/telecommuting" else "part-" + parse_qs(url.query).get("parts", [""])[0]
            mode = b.list_mode.get(list_id)
            if mode == "broken":
                return httpx.Response(200, text="<html><body>점검 중</body></html>", headers=html)
            return httpx.Response(200, text=list_html(list_id), headers=html)
        if url.path.startswith("/jobs/detail/"):
            jid = url.path.rsplit("/", 1)[-1]
            if jid not in JOBS:
                return httpx.Response(404)
            return httpx.Response(200, text=detail_html(jid), headers=html)
        return httpx.Response(404)

    return httpx.MockTransport(handler)
