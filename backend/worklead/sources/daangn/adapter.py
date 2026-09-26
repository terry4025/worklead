"""당근알바 전용 어댑터 — 공식 사이트맵으로 전국 공고를 찾고, 관련 공고만 상세를 확인한다.

조사 근거: docs/SOURCE_RESEARCH.md (2026-09-26 확인). 조사로 확인한 값은 profile.json 에 있다.

- 탐색: `jobs.daangn.com/robots.txt` 에 명시된 사이트맵 색인 → `job-posts-N.xml` 의 공고 URL·lastmod.
  지역 ID·검색 경로를 쓰지 않으므로 지역을 추측하거나 지역별로 요청을 나눌 필요가 없다.
- 1차 선별: 공고 URL 슬러그에 들어 있는 제목을 검색어 묶음과 대조해 맞는 공고만 상세를 요청한다.
  "구매 의도" 묶음(구합니다·의뢰 등)은 거의 모든 구인글 제목에 있어 제목 선별에 쓰지 않는다.
  본문에만 업무 단어가 있는 공고는 놓칠 수 있다 (알려진 한계).
- 상세: 공고 페이지에 포함된 공고 데이터(JobPost)에서 제목·본문(마스킹본)·모집 종료·게시 시각·근무 조건·지역을 읽는다.
  급여 형식(시급 등)은 작성 양식의 필수 항목이라 작성자의 의뢰/고용 표현으로 보지 않고 보수 필드로만 쓴다.
- 저장하지 않음: 작성자 정보, 상세 주소, 좌표, 조회·지원자 수.
- 쓰지 않음: `www.daangn.com` (robots.txt 가 AI 에이전트를 막고 일반 크롤러의 `/kr/jobs/s/` 검색을 금지),
  `/api/`, `/job-posts/*/apply` (robots.txt 금지), 로그인·지원·채팅.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from ...analysis.pay import parse_pay
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
from ..htmlparse import collect, normalize_text, parse_iso
from ..profiled import TaskFailed, canonicalize, load_profile

#: 제목 선별에 쓰지 않는 검색어 묶음 (거의 모든 구인글 제목에 있는 표현)
INTENT_ONLY_GROUPS = {"buyer_intent"}
SITEMAP_TASK_GROUP = "sitemap"
CONDITIONS_HEADER = "게시판 표시 조건"

_STORE_RE = re.compile(r"window\.__RELAY_STORE__\s*=\s*(\"(?:[^\"\\]|\\.)*\")\s*;", re.S)
_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.S)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LASTMOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>")
_PAY_LABEL_RE = re.compile(r"^(?:시급|일급|주급|월급|연봉|건당)\s")
_WEEKDAY = {"MON": "월", "TUE": "화", "WED": "수", "THU": "목", "FRI": "금", "SAT": "토", "SUN": "일"}


@dataclass
class ResearchCheck:
    ready: bool
    missing: list[str]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def title_from_url(url: str) -> str:
    """`/job-posts/<제목-슬러그>-<공고ID>` 에서 제목 추정 (문장부호는 슬러그에서 빠져 있다)."""
    slug = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    head = slug.rsplit("-", 1)[0] if "-" in slug else slug
    return head.replace("-", " ").strip()


def title_keywords(query_groups: list[dict]) -> list[str]:
    out: list[str] = []
    for g in query_groups:
        if not g.get("enabled", True) or g.get("id") in INTENT_ONLY_GROUPS:
            continue
        for k in g.get("keywords", []):
            k = str(k).strip()
            if k and k not in out:
                out.append(k)
    return out


def parse_sitemap_urls(xml: str) -> list[tuple[str, str | None]]:
    """사이트맵 XML 의 (loc, lastmod). 외부 개체를 해석하지 않도록 XML 파서 대신 정규식으로 읽는다."""
    out = []
    for block in _URL_BLOCK_RE.findall(xml):
        loc = _LOC_RE.search(block)
        if not loc:
            continue
        lm = _LASTMOD_RE.search(block)
        out.append((loc.group(1).replace("&amp;", "&"), lm.group(1) if lm else None))
    return out


def parse_sitemap_index(xml: str) -> list[str]:
    return [m.replace("&amp;", "&") for m in re.findall(r"<sitemap>\s*<loc>\s*([^<\s]+)\s*</loc>", xml)]


def _relay_store(markup: str) -> dict[str, Any] | None:
    m = _STORE_RE.search(markup)
    if not m:
        return None
    try:
        store = json.loads(json.loads(m.group(1)))
    except (ValueError, TypeError):
        return None
    return store if isinstance(store, dict) else None


def _job_post(store: dict[str, Any], post_id: str | None) -> dict[str, Any] | None:
    posts = [v for v in store.values() if isinstance(v, dict) and v.get("__typename") == "JobPost"]
    if post_id:
        # 이 페이지의 공고: 루트 → permalinkPairByPublicId(공고ID) → (daangn)Permalink → model
        # (후기 영역 등 다른 공고가 함께 들어 있으므로 개수로 고르지 않는다)
        root = store.get("client:root", {})
        pair = _deref(store, root.get(f'permalinkPairByPublicId(publicId:"{post_id}")'))
        for key in ("daangnPermalink", "permalink"):
            link = _deref(store, pair.get(key))
            if link.get("publicId") not in (None, post_id):
                continue
            model = _deref(store, link.get("model"))
            if model.get("__typename") == "JobPost":
                return model
    return posts[0] if len(posts) == 1 else None


def _deref(store: dict[str, Any], node: Any) -> dict[str, Any]:
    if isinstance(node, dict) and "__ref" in node:
        found = store.get(node["__ref"])
        return found if isinstance(found, dict) else {}
    return {}


def _conditions(jp: dict[str, Any], region_text: str | None) -> list[str]:
    """페이지에 표시되는 근무 조건 (급여 제외 — 급여는 보수 필드로)."""
    lines: list[str] = []
    dates = [d for d in jp.get("workDates") or [] if isinstance(d, str)]
    if dates:
        lines.append(f"기간: {dates[0]} ~ {dates[-1]} (총 {len(dates)}일)" if len(dates) > 1 else f"기간: {dates[0]} (하루)")
    days = [d for d in jp.get("workDays") or [] if isinstance(d, str)]
    if days:
        lines.append("요일: " + "·".join(_WEEKDAY.get(d, d) for d in days) + (" (협의 가능)" if jp.get("isWorkDaysNegotiable") else ""))
    if jp.get("isWorkTimeNegotiable"):
        lines.append("시간: 협의")
    elif jp.get("workTimeStart") and jp.get("workTimeEnd"):
        lines.append(f"시간: {jp['workTimeStart']}~{jp['workTimeEnd']}")
    if region_text:
        lines.append(f"근무지(동 단위): {region_text}")
    return lines


class DaangnAdapter:
    kind = "site"

    def __init__(self, profile: dict[str, Any]):
        self.profile = profile
        self.source_id: str = profile["source_id"]
        self.name = "당근알바"
        self.scope_note = "공식 사이트맵의 공개 구인글만 · 제목으로 1차 선별 · 중고거래·동네생활·비즈프로필·채팅은 범위 밖"
        self.adapter_version = "0.2.0"
        self.parser_version = "daangn-0.2.0"
        self.allowed_hosts: set[str] = set(profile.get("allowed_hosts") or [])
        self.daily_request_budget: int = int(profile.get("request_budget_per_day") or 300)
        self.min_interval_seconds: float = float(profile.get("min_interval_seconds") or 5)
        self.default_interval_minutes: int | None = profile.get("default_interval_minutes") or 360
        sm = profile.get("sitemap") or {}
        self._index_url: str | None = sm.get("index_url")
        self._job_sitemap_re = re.compile(sm["job_sitemap_pattern"]) if sm.get("job_sitemap_pattern") else None
        self._max_files = int(sm.get("max_files") or 5)
        self._max_age = timedelta(days=int(sm.get("max_age_days") or 60))
        #: 사이트맵은 압축 해제 후 수 MB (2026-09-26: 7.5MB) → 일반 페이지 상한(3MB)과 따로 둔다
        self._sitemap_max_bytes = int(sm.get("max_bytes") or 20 * 1024 * 1024)
        det = profile.get("detail") or {}
        self._link_re = re.compile(det["link_pattern"]) if det.get("link_pattern") else None

    # ── 조사·상태 ───────────────────────────────────────────────────
    def research_check(self) -> ResearchCheck:
        missing = []
        if not self.profile.get("verified"):
            missing.append("조사 검증 완료 표시(verified)")
        if not self._index_url or not self._job_sitemap_re:
            missing.append("사이트맵 위치")
        if not self._link_re:
            missing.append("상세 링크 형식")
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
        out = []
        for key, label in labels.items():
            v = declared.get(key, {"support": "unverified"})
            out.append(Capability(key, label, v.get("support", "unverified"), v.get("note")))
        return out

    def healthcheck(self) -> HealthReport:
        chk = self.research_check()
        if not chk.ready:
            return HealthReport("unknown", "조사 미완료: " + ", ".join(chk.missing), "research_incomplete")
        return HealthReport("ok", None)

    def block_detector(self, result: FetchResult) -> str | None:
        return None  # 공통 판별기(CAPTCHA 등)만 사용. 사이트 고유 차단 표시는 관찰되지 않음

    # ── 탐색 ────────────────────────────────────────────────────────
    def plan_discovery(self, query_groups: list[dict], now: datetime) -> DiscoveryPlan:
        chk = self.research_check()
        if not chk.ready:
            raise SourceNotReady("research_incomplete", "사이트 조사가 끝나지 않아 자동 탐색을 계획할 수 없습니다. 누락: " + ", ".join(chk.missing))
        keywords = title_keywords(query_groups)
        if not keywords:
            raise SourceNotReady("no_queries", "제목 선별에 쓸 검색어가 없습니다 (구매 의도 외 묶음을 켜 주세요)")
        task = TaskSpec(SITEMAP_TASK_GROUP, "nationwide", "전국 (사이트맵)", "\n".join(keywords))
        notes = [
            "robots.txt 에 명시된 공식 사이트맵으로 전국 공고 목록을 확인합니다",
            "제목이 검색어와 맞는 공고만 상세를 요청합니다 (본문에만 업무 단어가 있으면 놓칠 수 있음)",
            f"최근 {self._max_age.days}일 안에 갱신된 공고만 확인합니다",
            "사이트맵이 모든 공고를 담는지는 확인되지 않았습니다",
        ]
        return DiscoveryPlan(
            [task],
            "전국",
            "not_applicable",
            "사이트맵",
            self._max_files,
            notes,
            None,
            None,
            coverage_note="전국 공식 사이트맵 기준 탐색 (사이트맵 전체성·본문 기준 누락은 미확인)",
        )

    def _job_sitemaps(self, fetcher: Fetcher) -> list[str]:
        assert self._index_url and self._job_sitemap_re
        res = fetcher.get(self._index_url)
        if res.outcome != "ok":
            raise TaskFailed(res.outcome, f"사이트맵 색인 요청 실패 (HTTP {res.status or '-'}, {res.outcome})")
        children = [u for u in parse_sitemap_index(res.text or "") if self._job_sitemap_re.search(u)]
        if not children:
            raise ParseError("사이트맵 색인에서 공고 사이트맵을 찾지 못했습니다 (구조 변경 가능성)")
        return children[: self._max_files]

    def discover(self, task: TaskSpec, fetcher: Fetcher) -> DiscoverResult:
        assert self._link_re is not None
        idx = int(task.cursor) if task.cursor else 0
        children = self._job_sitemaps(fetcher)
        if idx >= len(children):
            return DiscoverResult([], None)
        res = fetcher.get(children[idx], max_bytes=self._sitemap_max_bytes)
        if res.outcome != "ok":
            raise TaskFailed(res.outcome, f"공고 사이트맵 요청 실패 (HTTP {res.status or '-'}, {res.outcome})")
        text = res.text or ""
        entries = parse_sitemap_urls(text)
        if not entries and "<urlset" not in text:
            raise ParseError("공고 사이트맵에서 URL 목록을 찾지 못했습니다 (구조 변경 가능성)")
        keywords = [_squash(k) for k in (task.query or "").split("\n") if k.strip()]
        cutoff = datetime.now(UTC) - self._max_age
        picked: list[tuple[datetime, PostRef]] = []
        for loc, lastmod in entries:
            if urlsplit(loc).hostname not in self.allowed_hosts:
                continue
            m = self._link_re.search(loc)
            if not m:
                continue
            modified, _ = parse_iso(lastmod)
            if modified is not None and modified < cutoff:
                continue
            title = title_from_url(loc)
            if not any(k in _squash(title) for k in keywords):
                continue
            picked.append((modified or datetime.min.replace(tzinfo=UTC), PostRef(loc, m.group(1), title)))
        picked.sort(key=lambda p: p[0], reverse=True)
        return DiscoverResult([r for _, r in picked], str(idx + 1) if idx + 1 < len(children) else None)

    # ── 상세 ────────────────────────────────────────────────────────
    def fetch_detail(self, ref: PostRef, fetcher: Fetcher) -> FetchResult:
        return fetcher.get(ref.url)

    def _post_id(self, url: str) -> str | None:
        if not self._link_re:
            return None
        m = self._link_re.search(url)
        return m.group(1) if m else None

    def parse(self, ref: PostRef, result: FetchResult, observed_at: datetime) -> ParsedPost:
        markup = result.text or ""
        post_id = ref.source_post_id or self._post_id(result.final_url)
        store = _relay_store(markup)
        jp = _job_post(store, post_id) if store else None
        if jp is None:
            raise ParseError("공고 페이지에서 공고 데이터를 찾지 못했습니다 (구조 변경 가능성)")
        title = normalize_text(str(jp.get("title(masking:true)") or jp.get("title") or ""))
        content = str(jp.get("content(masking:true)") or jp.get("content") or "").strip()
        if not title or not content:
            raise ParseError("제목 또는 본문이 비어 있습니다")

        region = _deref(store or {}, jp.get("workplaceRegion"))
        region_text = " ".join(str(region[k]) for k in ("name1", "name2", "name3") if region.get(k)) or None
        cond = _conditions(jp, region_text)
        # 머리글에 판정 규칙이 읽는 단어(알바·근무시간 등)를 넣지 않는다 — 원문에 없는 고용 신호가 생긴다
        body = content + ("\n\n— " + CONDITIONS_HEADER + " —\n" + "\n".join(cond) if cond else "")

        status, status_ev, notes = self._status(jp)
        published_at, prec = parse_iso(jp.get("publishedAt") or jp.get("createdAt"))
        bumped_at, bprec = parse_iso(jp.get("lastBringUpDate"))
        page = collect(markup)
        pay = self._pay(jp, title, page)
        if jp.get("employmentType"):
            notes.append(f"고용 형태 표시: {jp['employmentType']}")
        return ParsedPost(
            title=title,
            body=body,
            original_url=ref.url,
            canonical_url=canonicalize(ref.url),
            source_post_id=post_id,
            posted_region_raw=region_text,
            workplace_raw=region_text,
            applicant_region_raw=None,
            published=DateInfo(published_at, prec, jp.get("publishedAt") or jp.get("createdAt")),  # type: ignore[arg-type]
            source_updated=DateInfo(bumped_at, bprec, jp.get("lastBringUpDate")) if bumped_at else DateInfo(),  # type: ignore[arg-type]
            deadline=None,
            pay=pay,
            source_status=status,
            status_evidence=status_ev,
            contact_channel=(self.profile.get("detail") or {}).get("contact_channel_label"),
            parse_notes=notes,
        )

    @staticmethod
    def _status(jp: dict[str, Any]) -> tuple[str | None, list[Ev], list[str]]:
        notes: list[str] = []
        if jp.get("deleted") is True:
            return "deleted", [Ev("deleted=true", None, None, "explicit", "high", "당근알바 공고 데이터의 삭제 표시")], notes
        if jp.get("closed") is True:
            when = jp.get("closedAt")
            return "closed", [Ev("closed=true" + (f" ({when})" if when else ""), None, None, "explicit", "high", "당근알바 공고 데이터의 모집 종료 표시")], notes
        if jp.get("hidden") is True:
            notes.append("공고가 숨김 상태로 표시됨 — 모집 여부 판단 보류")
            return None, [], notes
        if jp.get("status") not in (None, "ACCEPTED"):
            notes.append(f"공고 검수 상태 {jp.get('status')} — 모집 여부 판단 보류")
            return None, [], notes
        if jp.get("closed") is False:
            return "open", [Ev("closed=false", None, None, "explicit", "medium", "당근알바 공고 데이터에 모집 종료 표시 없음 (확인 시점 기준)")], notes
        return None, [], notes

    @staticmethod
    def _pay(jp: dict[str, Any], title: str, page: Any) -> PayInfo | None:
        """급여: 페이지 제목의 급여 표시(예: '시급 20,000원')를 해석한다. 표시가 없으면 None (본문 규칙에 맡김)."""
        og = normalize_text(page.meta.get("og:title") or "")
        label = None
        if og.startswith(title) and og[len(title) :].startswith(", "):
            label = og[len(title) + 2 :].strip()
        elif ", " in og:
            tail = og.rsplit(", ", 1)[1].strip()
            label = tail if _PAY_LABEL_RE.match(tail) else None
        if not label:
            return None
        info = parse_pay(label)
        if info.min is None and info.max is None and info.unit == "unknown":
            return None
        info.raw = label
        info.evidence = [Ev(label, None, None, "explicit", "high", "당근알바 급여 표시 (작성 양식 항목)")]
        return info

    def revalidate(self, url: str, fetcher: Fetcher, observed_at: datetime) -> Revalidation:
        result = fetcher.get(url)
        if result.outcome in ("not_found", "gone"):
            if (self.profile.get("detail") or {}).get("deletion_reliable"):
                return Revalidation("deleted", "accessible", f"원천 삭제 응답 (HTTP {result.status})", http_status=result.status)
            return Revalidation(None, "error", f"HTTP {result.status} — 삭제 여부를 확정하지 않음", http_status=result.status)
        if result.outcome != "ok":
            return Revalidation(None, "error", f"재확인 실패 ({result.outcome})", http_status=result.status)
        parsed = self.parse(PostRef(url, self._post_id(url)), result, observed_at)
        return Revalidation(parsed.source_status, "accessible", None, parsed, result.status)


def create_from(path: Path) -> DaangnAdapter:
    return DaangnAdapter(load_profile(path))
