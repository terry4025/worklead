"""알바몬 어댑터 — IT 업직종·재택 알바 목록에서 개발·자동화 관련 공고만 상세를 확인한다.

조사 근거: docs/SOURCE_RESEARCH_ALBAMON.md (2026-09-26 확인). 확인한 값은 profile.json 에 있다.

- 탐색: 목록 페이지에 포함된 공고 데이터(`__NEXT_DATA__`)의 **1쪽만** 읽는다. 2쪽 이후는 페이지 주소로 제공되지 않아
  화면 내부 요청을 추측하지 않는다 (부분 탐색으로 표시). 목록은 최신순이라 짧은 주기로 새 공고를 따라간다.
- 1차 선별: 목록 데이터(제목·업직종·급여 형태)로 개발·자동화 관련만 상세를 요청한다. 교육생 모집 광고·연봉제 공고,
  한 목록 안에서 같은 제목으로 반복 게시한 공고는 제외.
- 상세: schema.org JobPosting + 페이지 내 본문(`viewData.content`). robots 가 금지한 `/jobs/detail-content` 는 요청하지 않는다.
- 저장하지 않음: 담당자 전화번호, 도로명 주소, 로고·사진, 조회·지원 통계.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...analysis.pay import parse_pay
from ...analysis.provider import mask_personal
from ...analysis.rules import TRAINEE_AD
from ...analysis.types import DateInfo, Ev, PayInfo
from ...engine.http import FetchResult, Fetcher
from ..base import (
    Capability,
    DiscoverResult,
    DiscoveryPlan,
    HealthReport,
    ParsedPost,
    ParseError,
    PostRef,
    Revalidation,
    SourceNotReady,
    TaskSpec,
)
from ..daangn.adapter import title_keywords
from ..htmlparse import find_job_posting, html_to_text, normalize_text, parse_iso
from ..profiled import TaskFailed, canonicalize, load_profile

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
#: 개발·자동화로 보는 알바몬 업직종 이름 (재택 목록 선별용 — 목록 데이터의 parts 에 표시되는 이름)
IT_PART_NAMES = {"프로그래머", "HTML코딩", "웹·콘텐츠기획", "웹·모바일디자인", "사이트관리·기술지원", "데이터수집·가공"}
#: 어느 목록에서든 상세를 확인할 개발 업직종
DEV_PART_NAMES = {"프로그래머", "HTML코딩"}
CONDITIONS_HEADER = "구인 양식 표시 조건"
_UNIT = {"HOUR": "시급", "DAY": "일급", "WEEK": "주급", "MONTH": "월급", "YEAR": "연봉"}


@dataclass
class ResearchCheck:
    ready: bool
    missing: list[str]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _next_data(markup: str) -> dict[str, Any] | None:
    m = _NEXT_RE.search(markup)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def list_items(markup: str) -> tuple[list[dict[str, Any]], int | None]:
    """목록 페이지의 공고 항목과 전체 건수. 구조를 찾지 못하면 ParseError."""
    data = _next_data(markup)
    if data is None:
        raise ParseError("목록 페이지에서 공고 데이터를 찾지 못했습니다 (구조 변경 가능성)")
    queries = (((data.get("props") or {}).get("pageProps") or {}).get("dehydratedState") or {}).get("queries") or []
    for q in queries:
        body = (q.get("state") or {}).get("data")
        if not isinstance(body, dict) or not isinstance(body.get("base"), dict):
            continue
        base = body["base"]
        total = (base.get("pagination") or {}).get("totalCount")
        items: list[dict[str, Any]] = []
        seen: set[Any] = set()
        groups = [base.get("normal")] + list((body.get("paid") or {}).values())
        for g in groups:
            for x in (g or {}).get("collection") or []:
                if isinstance(x, dict) and x.get("recruitNo") and x["recruitNo"] not in seen:
                    seen.add(x["recruitNo"])
                    items.append(x)
        return items, total if isinstance(total, int) else None
    raise ParseError("목록 데이터에서 공고 목록 구조를 찾지 못했습니다 (구조 변경 가능성)")


class AlbamonAdapter:
    kind = "site"

    def __init__(self, profile: dict[str, Any]):
        self.profile = profile
        self.source_id: str = profile["source_id"]
        self.name = "알바몬"
        self.scope_note = "IT 업직종·재택 알바 목록 1쪽 · 개발·자동화 관련 공고만 · 담당자 연락처 저장 안 함"
        self.adapter_version = "0.1.0"
        self.parser_version = "albamon-0.1.0"
        self.allowed_hosts: set[str] = set(profile.get("allowed_hosts") or [])
        self.daily_request_budget: int = int(profile.get("request_budget_per_day") or 200)
        self.min_interval_seconds: float = float(profile.get("min_interval_seconds") or 5)
        self.default_interval_minutes: int | None = profile.get("default_interval_minutes") or 60
        self._lists: list[dict[str, Any]] = list(profile.get("lists") or [])
        det = profile.get("detail") or {}
        self._detail_tpl: str | None = det.get("url_template")
        self._detail_re = re.compile(det["link_pattern"]) if det.get("link_pattern") else None

    # ── 조사·상태 ───────────────────────────────────────────────────
    def research_check(self) -> ResearchCheck:
        missing = []
        if not self.profile.get("verified"):
            missing.append("조사 검증 완료 표시(verified)")
        if not self._lists:
            missing.append("목록 페이지")
        if not self._detail_tpl or not self._detail_re:
            missing.append("상세 주소 형식")
        if not self.allowed_hosts:
            missing.append("허용 호스트")
        return ResearchCheck(not missing, missing)

    def describe_capabilities(self) -> list[Capability]:
        labels = {
            "nationwide_search": "전국 단일 검색",
            "region_search": "지역별 검색",
            "remote_filter": "재택 전용 필터",
            "sort": "정렬",
            "pagination": "페이지 이동",
            "incremental": "증분 수집",
            "detail": "상세 조회",
            "revalidate": "상태 재확인",
            "structured_data": "구조화 데이터",
        }
        declared = self.profile.get("capabilities", {})
        return [Capability(k, label, declared.get(k, {}).get("support", "unverified"), declared.get(k, {}).get("note")) for k, label in labels.items()]

    def healthcheck(self) -> HealthReport:
        chk = self.research_check()
        if not chk.ready:
            return HealthReport("unknown", "조사 미완료: " + ", ".join(chk.missing), "research_incomplete")
        return HealthReport("ok", None)

    def block_detector(self, result: FetchResult) -> str | None:
        return None

    # ── 탐색 ────────────────────────────────────────────────────────
    def plan_discovery(self, query_groups: list[dict], now: datetime) -> DiscoveryPlan:
        chk = self.research_check()
        if not chk.ready:
            raise SourceNotReady("research_incomplete", "사이트 조사가 끝나지 않아 자동 탐색을 계획할 수 없습니다. 누락: " + ", ".join(chk.missing))
        keywords = title_keywords(query_groups)
        if not keywords:
            raise SourceNotReady("no_queries", "제목 선별에 쓸 검색어가 없습니다 (구매 의도 외 묶음을 켜 주세요)")
        tasks = [TaskSpec("albamon", str(li["id"]), str(li.get("label") or li["id"]), "\n".join(keywords)) for li in self._lists]
        notes = [
            "IT 업직종(웹·콘텐츠기획·프로그래머·HTML코딩)과 재택 알바 목록의 1쪽만 확인합니다 (최신순)",
            "2쪽 이후는 사이트가 페이지 주소로 제공하지 않아 확인하지 않습니다",
            "교육생 모집 광고·연봉제 공고는 상세를 요청하지 않습니다",
        ]
        return DiscoveryPlan(tasks, "전국", "not_applicable", "목록 1쪽", 1, notes, None, None, coverage_note="IT 업직종·재택 목록 1쪽 기준 (전체 공고 포괄 아님)")

    def _wanted(self, item: dict[str, Any], kind: str, keywords: list[str]) -> bool:
        title = str(item.get("recruitTitle") or "")
        if not title or any(p.search(title) for p in TRAINEE_AD):
            return False
        pay_type = ((item.get("payType") or {}).get("description") or "").strip()
        if pay_type == "연봉":
            return False
        parts = {str(p) for p in item.get("parts") or []}
        if parts & DEV_PART_NAMES or any(k in _squash(title) for k in keywords):
            return True
        # 재택 목록은 IT 관련 업직종까지 넓게 본다. 업직종 목록(예: 웹·콘텐츠기획)은 체험단·방송 모집 광고가 많아 개발 업직종·제목으로만 거른다
        return kind == "remote_all" and bool(parts & IT_PART_NAMES)

    def discover(self, task: TaskSpec, fetcher: Fetcher) -> DiscoverResult:
        li = next((x for x in self._lists if str(x["id"]) == task.region_scope), None)
        if li is None:
            raise TaskFailed("unknown_list", f"알 수 없는 목록: {task.region_scope}")
        res = fetcher.get(str(li["url"]))
        if res.outcome != "ok":
            raise TaskFailed(res.outcome, f"목록 요청 실패 (HTTP {res.status or '-'}, {res.outcome})")
        items, total = list_items(res.text or "")
        if not items and total not in (0, None):
            raise ParseError("목록 건수는 있는데 공고 항목이 없습니다 (구조 변경 가능성)")
        keywords = [_squash(k) for k in (task.query or "").split("\n") if k.strip()]
        refs = []
        assert self._detail_tpl is not None
        seen: set[str] = set()
        for x in items:
            title = str(x.get("recruitTitle") or "")
            # 같은 제목으로 여러 번 올린 광고는 한 번만 확인한다 (예산 절약)
            if _squash(title) in seen or not self._wanted(x, str(li.get("kind") or ""), keywords):
                continue
            seen.add(_squash(title))
            rid = str(x["recruitNo"])
            refs.append(PostRef(self._detail_tpl.format(id=rid), rid, title))
        return DiscoverResult(refs, None)

    # ── 상세 ────────────────────────────────────────────────────────
    def fetch_detail(self, ref: PostRef, fetcher: Fetcher) -> FetchResult:
        return fetcher.get(ref.url)

    def _post_id(self, url: str) -> str | None:
        m = self._detail_re.search(url) if self._detail_re else None
        return m.group(1) if m else None

    def parse(self, ref: PostRef, result: FetchResult, observed_at: datetime) -> ParsedPost:
        markup = result.text or ""
        posting = find_job_posting(markup)
        nd = _next_data(markup)
        view = ((((nd or {}).get("props") or {}).get("pageProps") or {}).get("data") or {}).get("viewData") or {}
        if posting is None and not view:
            raise ParseError("공고 페이지에서 공고 데이터를 찾지 못했습니다 (구조 변경 가능성)")
        posting = posting or {}
        title = normalize_text(str(view.get("recruitTitle") or posting.get("title") or ""))
        content = str(view.get("content") or "")
        body = html_to_text(content) if "<" in content else content.strip()
        if not body:
            body = normalize_text(str(posting.get("description") or ""))
        # 담당자 전화번호·이메일은 저장하지 않는다 (지원은 원문에서 직접)
        body = mask_personal(body)
        if not title or not body:
            raise ParseError("제목 또는 본문이 비어 있습니다")

        remote = posting.get("jobLocationType") == "TELECOMMUTE"
        region = _region(posting)
        cond = _conditions(view, remote, region)
        body = body + ("\n\n— " + CONDITIONS_HEADER + " —\n" + "\n".join(cond) if cond else "")

        published_at, prec = parse_iso(posting.get("datePosted"))
        valid_through, vprec = parse_iso(posting.get("validThrough"))
        status, status_ev, notes = _status(view, valid_through, observed_at)
        pay = _pay(posting, view)
        types = [str(t.get("description")) for t in view.get("employmentType") or [] if isinstance(t, dict) and t.get("description")]
        if types:
            notes.append("고용 형태 표시: " + " · ".join(types))
        post_id = ref.source_post_id or self._post_id(result.final_url)
        return ParsedPost(
            title=title,
            body=body,
            original_url=ref.url,
            canonical_url=canonicalize(ref.url),
            source_post_id=post_id,
            posted_region_raw=region,
            workplace_raw="재택근무" if remote else region,
            applicant_region_raw=None,
            published=DateInfo(published_at, prec, posting.get("datePosted")),  # type: ignore[arg-type]
            deadline=DateInfo(valid_through, vprec, posting.get("validThrough")) if valid_through else None,  # type: ignore[arg-type]
            pay=pay,
            source_status=status,
            status_evidence=status_ev,
            contact_channel=(self.profile.get("detail") or {}).get("contact_channel_label"),
            parse_notes=notes,
        )

    def revalidate(self, url: str, fetcher: Fetcher, observed_at: datetime) -> Revalidation:
        result = fetcher.get(url)
        if result.outcome in ("not_found", "gone"):
            return Revalidation(None, "error", f"HTTP {result.status} — 삭제 여부를 확정하지 않음", http_status=result.status)
        if result.outcome != "ok":
            return Revalidation(None, "error", f"재확인 실패 ({result.outcome})", http_status=result.status)
        parsed = self.parse(PostRef(url, self._post_id(url)), result, observed_at)
        return Revalidation(parsed.source_status, "accessible", None, parsed, result.status)


def _region(posting: dict[str, Any]) -> str | None:
    """시·도와 시·군·구만 (도로명 주소는 저장하지 않는다)."""
    locs = posting.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs] if isinstance(locs, dict) else []
    for loc in locs:
        addr = (loc or {}).get("address") or {}
        parts = [str(addr.get(k)) for k in ("addressRegion", "addressLocality") if addr.get(k)]
        if parts:
            return " ".join(parts)
    return None


def _conditions(view: dict[str, Any], remote: bool, region: str | None) -> list[str]:
    """페이지에 표시되는 구인 조건. 판정 규칙이 읽는 단어가 원문 표시와 다르게 생기지 않도록 표시 문구 그대로 쓴다.

    재택근무를 고르지 않고 사업장 주소를 적은 공고는 `근무지 유형: 사업장` 으로 표시한다 (판정 규칙이 출근 근무로 추정).
    """
    lines: list[str] = []
    if remote:
        lines.append("근무지: 재택근무")
    elif region:
        lines.append(f"근무지 유형: 사업장 ({region})")
    period = (view.get("workPeriod") or {}).get("description")
    if period:
        lines.append(f"근무 기간: {period}")
    types = [str(t.get("description")) for t in view.get("employmentType") or [] if isinstance(t, dict) and t.get("description")]
    # 여러 형태 중 하나로 고를 수 있는 공고(예: 프리랜서·정규직)는 정규직이 아닌 선택지만 조건 줄에 쓴다 (전체는 기록에 남김)
    shown = [t for t in types if t != "정규직"] or types
    if shown:
        lines.append("고용 형태: " + " · ".join(shown))
    works = [w for g in view.get("workContentGroups") or [] if isinstance(g, dict) for w in g.get("workContents") or []]
    if works:
        lines.append("업무: " + ", ".join(dict.fromkeys(str(w) for w in works)))
    return lines


def _status(view: dict[str, Any], valid_through: datetime | None, observed_at: datetime) -> tuple[str | None, list[Ev], list[str]]:
    notes: list[str] = []
    # validThrough 는 날짜 단위 — 그날 끝까지는 모집 중으로 본다
    if valid_through is not None and valid_through + timedelta(days=1) <= observed_at:
        return "closed", [Ev(f"validThrough {valid_through.date().isoformat()}", None, None, "explicit", "high", "구조화 데이터 마감일 경과")], notes
    key = ((view.get("postStatus") or {}).get("key") or "").upper()
    if key == "OPEN":
        return "open", [Ev("postStatus=OPEN (게재중인 공고)", None, None, "explicit", "medium", "알바몬 공고 데이터의 게재 상태 (확인 시점 기준)")], notes
    if key:
        notes.append(f"게재 상태 {key} — 모집 여부 판단 보류")
    return None, [], notes


def _pay(posting: dict[str, Any], view: dict[str, Any]) -> PayInfo | None:
    label = None
    st = ((view.get("salaryType") or {}).get("description") or "").strip()
    base = posting.get("baseSalary") if isinstance(posting.get("baseSalary"), dict) else {}
    val = (base.get("value") or {}) if isinstance(base.get("value"), dict) else {}
    amount = val.get("value")
    unit = _UNIT.get(str(val.get("unitText") or "").upper())
    if isinstance(amount, (int, float)) and amount > 0:
        word = {"건별": "건당"}.get(st, st) or unit
        if word:
            label = f"{word} {int(amount):,}원"
    if not label:
        return None
    info = parse_pay(label)
    if info.min is None and info.max is None:
        return None
    info.raw = label.replace("건당", "건별") if st == "건별" else label
    info.evidence = [Ev(info.raw, None, None, "explicit", "high", "알바몬 급여 표시 (구인 양식 항목)")]
    return info


def create_from(path: Path) -> AlbamonAdapter:
    return AlbamonAdapter(load_profile(path))

