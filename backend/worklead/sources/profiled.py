"""조사 프로필 기반 사이트 어댑터.

사이트 조사(SOURCE_RESEARCH.md)로 확인한 값 — 검색 URL 형식, 지역 목록, 상세 링크 형식, 상세 해석 방식 —
을 프로필(JSON)로 받아 동작한다. 확인되지 않은 항목이 있으면 실행하지 않고 SourceNotReady 를 낸다.
DOM 선택자·숨은 API·지역 ID 를 코드에서 추측하지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from ..analysis.types import DateInfo, Ev, PayInfo
from ..engine.http import FetchResult, Fetcher
from .base import (
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
from .htmlparse import collect, extract_links, find_job_posting, html_to_text, job_location_text, normalize_text, parse_iso
from .regions import SIDO

_SALARY_UNIT = {"HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month", "YEAR": "unknown"}


class TaskFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ProfileCheck:
    ready: bool
    missing: list[str]


def load_profile(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_profile(p: dict[str, Any]) -> ProfileCheck:
    missing: list[str] = []
    if not p.get("verified"):
        missing.append("조사 검증 완료 표시(verified)")
    search = p.get("search", {})
    if not (search.get("url_template") or search.get("nationwide_url_template")):
        missing.append("검색 URL 형식")
    if not search.get("nationwide_url_template"):
        regions = p.get("regions", {})
        if not regions.get("items"):
            missing.append("공개 지역 목록")
    if not p.get("list", {}).get("detail_link_pattern"):
        missing.append("상세 링크 형식")
    if not p.get("detail", {}).get("strategy"):
        missing.append("상세 해석 방식")
    if not p.get("allowed_hosts"):
        missing.append("허용 호스트")
    return ProfileCheck(not missing, missing)


def canonicalize(url: str) -> str:
    """조각(#)만 제거한다. 쿼리는 공고 식별에 쓰일 수 있어 보존한다 (서로 다른 공고를 합치지 않기 위해)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


class ProfiledSiteAdapter:
    kind = "site"

    def __init__(self, profile: dict[str, Any], *, name: str, scope_note: str | None, adapter_version: str, parser_version: str):
        self.profile = profile
        self.source_id: str = profile["source_id"]
        self.name = name
        self.scope_note = scope_note
        self.adapter_version = adapter_version
        self.parser_version = parser_version
        self.allowed_hosts: set[str] = set(profile.get("allowed_hosts") or [])
        self.daily_request_budget: int = int(profile.get("request_budget_per_day") or 200)
        self.min_interval_seconds: float = float(profile.get("min_interval_seconds") or 5)
        self.default_interval_minutes: int | None = profile.get("default_interval_minutes") or 180
        link = profile.get("list", {}).get("detail_link_pattern")
        self._link_re = re.compile(link) if link else None
        pid = profile.get("list", {}).get("post_id_pattern")
        self._post_id_re = re.compile(pid) if pid else None

    # ── 기능 선언 ───────────────────────────────────────────────────
    def describe_capabilities(self) -> list[Capability]:
        declared = self.profile.get("capabilities", {})
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
        out = []
        for key, label in labels.items():
            v = declared.get(key, {"support": "unverified"})
            out.append(Capability(key, label, v.get("support", "unverified"), v.get("note")))
        return out

    def healthcheck(self) -> HealthReport:
        chk = check_profile(self.profile)
        if not chk.ready:
            return HealthReport("unknown", "조사 미완료: " + ", ".join(chk.missing), "research_incomplete")
        return HealthReport("ok", None)

    def block_detector(self, result: FetchResult) -> str | None:
        markers = self.profile.get("detail", {}).get("block_markers") or []
        text = result.text or ""
        for m in markers:
            if m and m in text:
                return f"block_marker:{m}"
        return None

    # ── 탐색 계획 ───────────────────────────────────────────────────
    def _queries(self, query_groups: list[dict]) -> list[tuple[str, str]]:
        mode = self.profile.get("search", {}).get("query_mode", "per_keyword")
        joiner = self.profile.get("search", {}).get("joiner", " ")
        out: list[tuple[str, str]] = []
        for g in query_groups:
            if not g.get("enabled", True):
                continue
            kws = [k for k in g.get("keywords", []) if k.strip()]
            if not kws:
                continue
            if mode == "joined":
                out.append((g["id"], joiner.join(kws)))
            else:
                out.extend((f"{g['id']}:{k}", k) for k in kws)
        return out

    def plan_discovery(self, query_groups: list[dict], now: datetime) -> DiscoveryPlan:
        chk = check_profile(self.profile)
        if not chk.ready:
            raise SourceNotReady("research_incomplete", "사이트 조사가 끝나지 않아 자동 탐색을 계획할 수 없습니다. 누락: " + ", ".join(chk.missing))
        search = self.profile["search"]
        depth_limit = int(search.get("max_pages", 2))
        queries = self._queries(query_groups)
        if not queries:
            raise SourceNotReady("no_queries", "활성화된 검색어 묶음이 없습니다")
        if search.get("nationwide_url_template"):
            tasks = [TaskSpec(qg, "nationwide", "전국", q) for qg, q in queries]
            return DiscoveryPlan(tasks, "전국", "not_applicable", "검색어", depth_limit, ["전국 단일 검색 경로 사용"], len(SIDO), len(SIDO))
        regions = self.profile["regions"]
        items = regions["items"]
        tasks = [TaskSpec(qg, str(r["param"]), r.get("label"), q) for r in items for qg, q in queries]
        covered = len({r.get("sido") for r in items if r.get("sido") in SIDO})
        status = "verified" if regions.get("status") == "verified" else "unverified"
        notes = []
        if status != "verified":
            notes.append("지역 목록의 전체성이 확인되지 않아 부분 탐색으로 표시합니다")
        if covered < len(SIDO):
            notes.append(f"전국 17개 시·도 중 {covered}곳만 지역 목록 확보")
        return DiscoveryPlan(tasks, "전국", status, "지역×검색어", depth_limit, notes, len(SIDO), covered)

    # ── 탐색·상세 ───────────────────────────────────────────────────
    def _search_url(self, task: TaskSpec, page: int) -> str:
        search = self.profile["search"]
        template = search.get("nationwide_url_template") if task.region_scope == "nationwide" else search["url_template"]
        return template.format(query=quote(task.query or ""), region=quote(task.region_scope), page=page)

    def discover(self, task: TaskSpec, fetcher: Fetcher) -> DiscoverResult:
        search = self.profile["search"]
        first = int(search.get("first_page", 1))
        page = int(task.cursor) if task.cursor else first
        result = fetcher.get(self._search_url(task, page))
        if result.outcome != "ok":
            raise TaskFailed(result.outcome, f"목록 요청 실패 (HTTP {result.status or '-'}, {result.outcome})")
        assert self._link_re is not None
        links = extract_links(result.text or "", result.final_url, self._link_re)
        if not links:
            empty_markers = self.profile.get("list", {}).get("empty_markers") or []
            text = result.text or ""
            if not any(m and m in text for m in empty_markers):
                # 결과 없음 표시도 링크도 없으면 구조 변경·차단 가능성 → 파싱 실패로 기록
                raise ParseError("목록에서 상세 링크와 '결과 없음' 표시를 모두 찾지 못했습니다 (구조 변경 가능성)")
            return DiscoverResult([], None)
        refs = [PostRef(url=u, source_post_id=self._post_id(u)) for u in links]
        has_next = page - first + 1 < int(search.get("max_pages", 2))
        return DiscoverResult(refs, str(page + 1) if has_next else None)

    def _post_id(self, url: str) -> str | None:
        if not self._post_id_re:
            return None
        m = self._post_id_re.search(url)
        return m.group(1) if m else None

    def fetch_detail(self, ref: PostRef, fetcher: Fetcher) -> FetchResult:
        return fetcher.get(ref.url)

    def parse(self, ref: PostRef, result: FetchResult, observed_at: datetime) -> ParsedPost:
        strategy = self.profile["detail"]["strategy"]
        if strategy != "jsonld_jobposting":
            raise ParseError(f"지원하지 않는 상세 해석 방식: {strategy}")
        markup = result.text or ""
        posting = find_job_posting(markup)
        if posting is None:
            raise ParseError("JobPosting 구조화 데이터를 찾지 못했습니다")
        title = normalize_text(str(posting.get("title") or ""))
        body = html_to_text(str(posting.get("description") or ""))
        if not title or not body:
            raise ParseError("제목 또는 본문이 비어 있습니다")
        page = collect(markup)
        page_text = normalize_text("".join(page.text))

        published_at, prec = parse_iso(posting.get("datePosted"))
        valid_through, vprec = parse_iso(posting.get("validThrough"))
        ident = posting.get("identifier")
        post_id = None
        if isinstance(ident, dict) and ident.get("value"):
            post_id = str(ident["value"])
        post_id = post_id or ref.source_post_id or self._post_id(result.final_url)

        status: str | None = None
        status_ev: list[Ev] = []
        for marker in self.profile["detail"].get("closed_markers") or []:
            if marker and marker in page_text:
                status = "closed"
                status_ev.append(Ev(marker, None, None, "explicit", "high", "원문 페이지의 마감 표시"))
                break
        if status is None and valid_through is not None:
            if valid_through < observed_at:
                status = "closed"
                status_ev.append(Ev(f"validThrough {posting.get('validThrough')}", None, None, "explicit", "high", "구조화 데이터 마감일 경과"))
            elif self.profile["detail"].get("valid_through_means_open"):
                status = "open"
                status_ev.append(Ev(f"validThrough {posting.get('validThrough')}", None, None, "explicit", "medium", "구조화 데이터 마감일 이전 (조사로 확인된 규칙)"))
        if status is None:
            for marker in self.profile["detail"].get("open_markers") or []:
                if marker and marker in page_text:
                    status = "open"
                    status_ev.append(Ev(marker, None, None, "explicit", "medium", "원문 페이지의 모집 중 표시 (조사로 확인된 표시)"))
                    break

        pay = _salary(posting.get("baseSalary"))
        canonical = page.canonical if page.canonical and urlsplit(page.canonical).hostname in self.allowed_hosts else None
        notes = []
        if posting.get("jobLocationType") == "TELECOMMUTE":
            notes.append("구조화 데이터 jobLocationType=TELECOMMUTE")
        applicant = posting.get("applicantLocationRequirements")
        applicant_txt = None
        if isinstance(applicant, dict):
            applicant_txt = str(applicant.get("name") or "") or None
        return ParsedPost(
            title=title,
            body=body,
            original_url=result.final_url,
            canonical_url=canonicalize(canonical or result.final_url),
            source_post_id=post_id,
            posted_region_raw=None,
            workplace_raw=job_location_text(posting),
            applicant_region_raw=applicant_txt,
            published=DateInfo(published_at, prec, posting.get("datePosted")),  # type: ignore[arg-type]
            deadline=DateInfo(valid_through, vprec, posting.get("validThrough")) if valid_through else None,  # type: ignore[arg-type]
            pay=pay,
            source_status=status,
            status_evidence=status_ev,
            contact_channel=self.profile.get("detail", {}).get("contact_channel_label"),
            parse_notes=notes,
        )

    def revalidate(self, url: str, fetcher: Fetcher, observed_at: datetime) -> Revalidation:
        result = fetcher.get(url)
        if result.outcome in ("not_found", "gone"):
            if self.profile.get("detail", {}).get("deletion_reliable"):
                return Revalidation("deleted", "accessible", f"원천 삭제 응답 (HTTP {result.status})", http_status=result.status)
            return Revalidation(None, "error", f"HTTP {result.status} — 삭제 여부를 확정하지 않음", http_status=result.status)
        if result.outcome != "ok":
            return Revalidation(None, "error", f"재확인 실패 ({result.outcome})", http_status=result.status)
        parsed = self.parse(PostRef(url), result, observed_at)
        return Revalidation(parsed.source_status, "accessible", None, parsed, result.status)


def _salary(node: Any) -> PayInfo | None:
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    currency = str(node.get("currency") or "KRW")
    unit = None
    lo = hi = None
    if isinstance(value, dict):
        unit = value.get("unitText")
        lo = value.get("minValue", value.get("value"))
        hi = value.get("maxValue", value.get("value"))
    elif isinstance(value, (int, float)):
        lo = hi = value
    try:
        lo_i = int(lo) if lo is not None else None
        hi_i = int(hi) if hi is not None else None
    except (TypeError, ValueError):
        return None
    if lo_i is None and hi_i is None:
        return None
    mapped = _SALARY_UNIT.get(str(unit).upper(), "unknown") if unit else "unknown"
    raw = f"baseSalary {lo_i}~{hi_i} {currency}/{unit or '?'}"
    return PayInfo(raw=raw, currency=currency, min=lo_i, max=hi_i, unit=mapped, negotiable=False, evidence=[Ev(raw, None, None, "explicit", "high", "구조화 데이터 baseSalary")])


def now_utc() -> datetime:
    return datetime.now(UTC)
