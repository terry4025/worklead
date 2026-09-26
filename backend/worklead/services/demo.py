"""데모 전용 합성 데이터 (--demo). 별도 DB 파일에만 넣고 운영 데이터·성과 통계와 섞지 않는다.

글 본문은 합성이며, 분류·점수·추천은 실제 규칙 파이프라인이 계산한다.
원문 URL 은 만들지 않는다 (실제 사이트 주소를 추측하지 않음).
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from ..analysis.types import DateInfo
from ..context import AppContext
from ..db import utcnow
from ..models import AnalysisResult, Lead, LeadActivity, Outcome, Source, SourcePolicy, SourceRecord, new_id
from ..sources.base import Capability, HealthReport, ParsedPost
from ..engine.pipeline import apply_analysis, ingest

DEMO_SOURCE = "demo-synthetic"


class DemoAdapter:
    source_id = DEMO_SOURCE
    name = "데모 합성 데이터"
    kind = "manual"
    adapter_version = "demo"
    parser_version = "demo"
    allowed_hosts: set[str] = set()
    daily_request_budget = 0
    min_interval_seconds = 0.0
    scope_note = "화면 확인용 합성 글 — 실제 수집 결과 아님"
    default_interval_minutes = None

    def describe_capabilities(self) -> list[Capability]:
        return [Capability("demo", "합성 데이터", "supported", "실제 사이트와 연결되지 않음")]

    def healthcheck(self) -> HealthReport:
        return HealthReport("ok", None)

    def block_detector(self, result: Any) -> None:
        return None


POSTS: list[dict[str, Any]] = [
    {
        "title": "스마트스토어 주문 엑셀 정리 매크로(VBA) 만들어주실 분",
        "body": "스마트스토어에서 내려받는 주문 엑셀을 매일 손으로 정리하고 있습니다.\n주문 파일을 넣으면 택배 송장 양식과 일별 매출 시트로 자동 정리되는 매크로를 만들어주실 분 구합니다.\n\n- 엑셀 2019 사용 중, 파일 샘플 제공 가능\n- 작업은 전 과정 원격으로 진행합니다. 자료는 메일과 카톡으로 드려요.\n- 지역 상관없이 지원 가능합니다.\n- 예산: 50~80만원 (범위 확인 후 조정 가능)\n- {d7}까지 지원 받습니다.",
        "region": "경기 성남시 분당구",
        "status": "open",
        "checked_h": 2,
        "published_h": 26,
        "seen_h": 20,
    },
    {
        "title": "카페 브랜드 소개 랜딩페이지 제작 의뢰 (반응형 1페이지)",
        "body": "새로 여는 카페 브랜드 소개용 랜딩페이지 1페이지 제작을 의뢰합니다.\n메뉴 소개, 매장 위치 지도, 인스타그램 연결, 예약 문의 폼 정도 필요합니다.\n\n사진과 문구는 저희가 준비해 두었습니다.\n미팅 없이 온라인으로만 진행하고 싶고, 재택 작업 가능합니다.\n전국 어디서나 연락 주세요.\n\n예산은 150만원 생각하고 있습니다. 도메인·호스팅 비용은 저희가 따로 부담합니다.\n오픈 일정 때문에 이번 달 안에 시작하면 좋겠습니다.",
        "region": "부산 수영구",
        "status": "open",
        "checked_h": 5,
        "published_h": 30,
        "seen_h": 29,
        "mark": "interested",
        "stage": "reviewing",
        "memo": "사진 퀄리티 좋음. 예약 폼은 네이버 예약 링크로 대체 가능한지 물어볼 것.",
    },
    {
        "title": "구글 시트 주문 내역 → 카카오 알림톡 자동 발송 연동",
        "body": "구글 스프레드시트에 주문이 들어오면 고객에게 카카오 알림톡이 자동으로 나가도록 연동해 주실 분을 찾습니다.\n현재는 직원이 하나씩 복사해서 보내고 있어요.\n\n알림톡 발송 대행사 계정은 이미 있습니다(API 키 보유).\n작업은 원격으로 진행하고, 화면 공유로 설명드릴 수 있습니다.\n금액은 작업 범위 보고 협의하고 싶습니다. 견적 부탁드립니다.",
        "region": "서울 송파구",
        "status": "open",
        "checked_h": 1,
        "published_h": 8,
        "seen_h": 7,
    },
    {
        "title": "쇼핑몰 상세페이지 수정·기존 사이트 유지보수 (월 단위)",
        "body": "카페24로 운영 중인 쇼핑몰의 상세페이지 수정과 간단한 유지보수를 맡아주실 분 구합니다.\n한 달에 5~8건 정도 수정 요청이 있고, 급한 건은 이틀 안에 처리해 주시면 됩니다.\n\n완전 재택이며 소통은 슬랙으로 합니다.\n월 30만원 고정, 3개월 후 재계약 여부 결정합니다.\n전국 누구나 지원 가능.",
        "region": "대구 중구",
        "status": "open",
        "checked_h": 9,
        "published_raw": "2일 전",
        "seen_h": 44,
    },
    {
        "title": "학원 수강생·출결 관리 웹 프로그램 개발 외주",
        "body": "수학학원에서 쓸 수강생 관리, 출결, 수납 내역 확인용 웹 프로그램 개발 외주 맡기려고 합니다.\n강사 5명, 학생 약 200명 규모입니다.\n\n개발은 재택으로 하셔도 되지만, 첫 미팅은 학원에서 대면으로 한 번 하고 싶습니다(강남역 인근).\n이후 진행은 온라인으로 합니다.\n예산은 300~500만원 사이로 생각하고 있으며 기능 범위에 따라 조정 가능합니다.",
        "region": "서울 강남구",
        "status": "open",
        "checked_h": 3,
        "published_h": 18,
        "seen_h": 16,
    },
    {
        "title": "워드프레스 홈페이지 리뉴얼 해주실 분 구합니다",
        "body": "인테리어 업체 홈페이지(워드프레스)가 오래되어 리뉴얼하려고 합니다.\n시공 사례 게시판과 견적 문의 폼이 필요해요.\n기존 자료는 메일로 전달드릴 수 있습니다.\n관심 있으신 분 연락 주세요.",
        "region": "광주 서구",
        "status": "open",
        "checked_h": 20,
        "published_raw": "3일 전",
        "seen_h": 60,
    },
    {
        "title": "엑셀 데이터 정리 단기 알바 (재택 가능)",
        "body": "거래처 목록 엑셀 파일 약 3,000행을 정리해 주실 분을 구합니다.\n중복 제거, 주소 형식 통일, 담당자 연락처 분리 작업입니다.\n\n재택 가능하며 파일로 주고받습니다.\n시급 12,000원, 하루 4시간 정도로 1~2주 예상합니다.\n엑셀 기본 함수 사용 가능하신 분.",
        "region": "인천 연수구",
        "status": "open",
        "checked_h": 6,
        "published_h": 12,
        "seen_h": 11,
    },
    {
        "title": "앱·웹 개발자 구합니다 (프리랜서)",
        "body": "스타트업에서 앱·웹 개발 가능한 프리랜서 구합니다.\nReact, Node.js 경험자 우대.\n자세한 내용은 연락 주시면 설명드리겠습니다.",
        "region": "대전 유성구",
        "status": "open",
        "checked_h": 74,
        "published_raw": "4일 전",
        "seen_h": 96,
    },
    {
        "title": "네일샵 예약 페이지 만들어주세요",
        "body": "네일샵 예약 페이지를 만들고 싶어요.\n날짜랑 시간 선택하면 예약되고, 사장님한테 알림 가는 정도면 됩니다.",
        "region": "제주 제주시",
        "status": "unknown",
        "checked_h": 4,
        "seen_h": 4,
    },
    {
        "title": "거래처 발주서 PDF → 엑셀 자동 변환 프로그램 제작",
        "body": "거래처에서 오는 PDF 발주서(양식 3종)를 엑셀로 자동 변환하는 프로그램이 필요합니다.\n매주 100건 정도 들어옵니다.\n제작해 주실 수 있는 분 견적 보내주세요.\n윈도우 PC에서 돌아가면 됩니다.",
        "region": "경남 창원시",
        "status": "open",
        "checked_h": 2,
        "published_h": 5,
        "seen_h": 4,
        "ai_failure": ("ai_timeout", "외부 AI 응답 시간 초과 (60초). 같은 입력으로 자동 재요청하지 않았습니다."),
    },
    {
        "title": "치과 홈페이지 제작 의뢰 (기존 도메인 있음)",
        "body": "치과 홈페이지 새로 제작 의뢰드립니다. 기존 도메인은 있고 호스팅은 새로 알아보려 합니다.\n진료 안내, 의료진 소개, 온라인 상담 신청 페이지 필요합니다.\n원격 작업 가능. 예산 200만원.",
        "region": "울산 남구",
        "status": "open",
        "access": "blocked",
        "checked_h": 50,
        "published_h": 80,
        "seen_h": 76,
    },
    {
        "title": "홈페이지 제작해 드립니다 ✔ 저렴한 가격 빠른 제작",
        "body": "홈페이지·쇼핑몰·랜딩페이지 제작해 드립니다.\n10년 경력, 30만원부터 가능합니다.\n포트폴리오 보시고 연락 주세요.",
        "region": "서울 마포구",
        "status": "open",
        "checked_h": 10,
        "published_h": 30,
        "seen_h": 28,
    },
    {
        "title": "온라인 쇼핑몰 CS·상품등록 직원 (주 5일 출근)",
        "body": "온라인 쇼핑몰 상품 등록과 고객 문의 응대 업무입니다.\n근무지: 경기 김포시 사무실 (주 5일 출근)\n월급 230만원, 4대보험.",
        "region": "경기 김포시",
        "status": "open",
        "checked_h": 7,
        "published_h": 40,
        "seen_h": 38,
    },
    {
        "title": "필라테스 센터 오픈 이벤트 랜딩페이지 제작",
        "body": "[마감] 필라테스 센터 오픈 이벤트용 랜딩페이지 제작 의뢰합니다.\n재택 작업 가능, 예산 80만원.",
        "region": "경기 고양시",
        "status": "unknown",
        "checked_h": 22,
        "published_h": 140,
        "seen_h": 130,
    },
    {
        "title": "고수익 재택 부업! 누구나 가능한 데이터 입력",
        "body": "하루 1시간, 월 300만원 이상 고수익 보장!\n재택으로 누구나 가능합니다.\n시작 전 교육 자료비 5만원 입금 후 안내드립니다.",
        "region": "서울 중구",
        "status": "open",
        "checked_h": 12,
        "published_h": 20,
        "seen_h": 19,
    },
    {
        "title": "사내 재고관리 프로그램(C#) 개발자 — 재택 정규직",
        "body": "제조업체 사내 재고관리 프로그램(C#, WinForms) 유지보수·개발 정규직 채용합니다.\n완전 재택 근무, 월 1회 본사(충북 청주) 회의 참석.\n연봉 4,200만원 협의.",
        "region": "충북 청주시",
        "status": "open",
        "checked_h": 8,
        "published_h": 45,
        "seen_h": 44,
    },
    {
        "title": "소규모 법률사무소 홈페이지 제작",
        "body": "변호사 2인 사무소 홈페이지 제작 의뢰합니다.\n업무분야 소개, 상담 예약 폼, 블로그형 칼럼 게시판이 필요합니다.\n전 과정 비대면 진행 원합니다. 예산 250만원.",
        "region": "서울 서초구",
        "status": "open",
        "checked_h": 14,
        "published_h": 60,
        "seen_h": 58,
        "mark": "interested",
        "stage": "contacted",
        "memo": "당근 채팅으로 문의 보냄. 회신 대기.",
        "draft": True,
    },
    {
        "title": "플라워샵 꽃 정기구독 신청 랜딩페이지",
        "body": "꽃 정기구독 서비스 신청을 받는 랜딩페이지를 만들고 싶습니다.\n결제는 스마트스토어 링크로 연결할 예정이에요.\n온라인으로만 진행 가능하신 분, 재택 환영합니다. 예산 100만원 내외.",
        "region": "경기 수원시",
        "status": "open",
        "checked_h": 30,
        "published_h": 120,
        "seen_h": 118,
        "stage": "negotiating",
        "memo": "견적 120만원 제시, 상대는 100만원 희망. 수정 횟수 줄이는 안으로 조율 중.",
    },
    {
        "title": "견적서·거래명세서 엑셀 자동 작성 VBA",
        "body": "견적서와 거래명세서를 엑셀로 자동 작성하는 VBA를 제작해 주실 분 구합니다.\n품목 DB 시트에서 선택하면 양식이 채워지고 PDF로 저장되면 좋겠습니다.\n재택 진행, 예산 60만원.",
        "region": "경북 구미시",
        "status": "closed",
        "checked_h": 48,
        "published_h": 260,
        "seen_h": 250,
        "stage": "won",
        "memo": "착수금 30만원 입금 확인. 잔금은 납품 후.",
        "outcomes": [("contract_confirmed", 600_000, 6, "채팅으로 금액·범위 합의"), ("payment_received", 300_000, 5, "착수금")],
    },
]


def _kst_md(dt) -> str:  # type: ignore[no-untyped-def]
    from datetime import timezone

    k = dt.astimezone(timezone(timedelta(hours=9)))
    return f"{k.month}월 {k.day}일"


def seed(ctx: AppContext) -> int:
    now = utcnow()
    ctx.adapters[DEMO_SOURCE] = DemoAdapter()
    with ctx.db.session() as s:
        if s.get(Source, DEMO_SOURCE) is None:
            s.add(Source(id=DEMO_SOURCE, name=DemoAdapter.name, kind="manual", adapter_version="demo", scope_note=DemoAdapter.scope_note, health_status="ok"))
            s.add(SourcePolicy(source_id=DEMO_SOURCE, status="not_required", reviewed_by="system"))
            s.flush()
    for p in POSTS:
        body = p["body"].replace("{d7}", _kst_md(now + timedelta(days=7)))
        seen = now - timedelta(hours=p["seen_h"])
        published = DateInfo()
        if "published_h" in p:
            published = DateInfo(now - timedelta(hours=p["published_h"]), "exact", None)
        elif "published_raw" in p:
            from ..analysis.dates import parse_relative_published

            published = parse_relative_published(p["published_raw"], seen)
        parsed = ParsedPost(
            title=p["title"],
            body=body,
            original_url=None,
            canonical_url=None,
            source_post_id="demo-" + hashlib.sha1(p["title"].encode()).hexdigest()[:10],
            posted_region_raw=p.get("region"),
            published=published,
            source_status=p["status"] if p["status"] != "unknown" else None,
            contact_channel="원문에서 직접 (데모)",
        )
        with ctx.db.session() as s:
            r = ingest(s, DEMO_SOURCE, parsed, parser_version="demo", now=seen, path=(p.get("region") or "-", p.get("region"), "demo"), provider=ctx.provider)
            lead = s.get(Lead, r.lead_id)
            rec = s.get(SourceRecord, r.record_id)
            assert lead is not None and rec is not None
            rec.first_seen_at = seen
            lead.first_seen_at = seen
            rec.last_checked_at = now - timedelta(hours=p["checked_h"])
            rec.access_status = p.get("access", "accessible")
            if p["status"] != "unknown":
                rec.source_status = p["status"]
            apply_analysis(s, lead, rec, now=now, provider=ctx.provider, source_kind="site")
            if "ai_failure" in p:
                code, msg = p["ai_failure"]
                ana = s.get(AnalysisResult, lead.analysis_id)
                if ana is not None:
                    ana.status, ana.failure_code, ana.failure_message, ana.engine = "failed", code, msg, "ai:demo"
                lead.analysis_status = "failed"
                lead.reasons = (lead.reasons or [])[:2] + [{"tone": "negative", "text": "AI 분석 실패 — 규칙 결과만"}]
            if p.get("mark"):
                lead.user_mark = p["mark"]
            if p.get("stage"):
                lead.sales_stage = p["stage"]
            if p.get("memo"):
                lead.memo = p["memo"]
            if p.get("draft"):
                from ..engine.pipeline import generate_draft

                lead.draft_text = generate_draft(s, lead, now)
                lead.draft_generated_at = now - timedelta(hours=40)
            for kind, amount, days, note in p.get("outcomes", []):
                s.add(
                    Outcome(
                        id=new_id("out"),
                        lead_id=lead.id,
                        kind=kind,
                        amount=amount,
                        occurred_on=(now - timedelta(days=days)).date().isoformat(),
                        note=note,
                        evidence_ref=None,
                        created_at=now - timedelta(days=days),
                    )
                )
                s.add(LeadActivity(id=new_id("act"), lead_id=lead.id, at=now - timedelta(days=days), kind="outcome", text=f"{note} 기록"))
    return len(POSTS)


def seed_if_empty(ctx: AppContext) -> int:
    with ctx.db.session() as s:
        count = s.scalar(select(func.count()).select_from(Lead)) or 0
    if count:
        ctx.adapters.setdefault(DEMO_SOURCE, DemoAdapter())
        return 0
    return seed(ctx)
