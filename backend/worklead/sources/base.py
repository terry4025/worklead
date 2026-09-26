"""SourceAdapter 계약.

어댑터 책임은 사이트 고유 부분만이다: 기능 선언, 탐색 계획, 목록 탐색, 상세 확보, 파싱, 재확인, 상태 점검.
HTTP 실행·허용 호스트·요청 제한·작업 이력·정규화·중복·점수·알림은 공통 엔진이 담당한다.
어댑터는 UI 나 영업 상태를 바꾸지 않는다. 지원하지 않는 기능은 unsupported 로 선언하고 가짜 결과를 돌려주지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

from ..analysis.types import DateInfo, Ev, PayInfo

if TYPE_CHECKING:
    from ..engine.http import FetchResult, Fetcher

Support = Literal["supported", "unsupported", "unverified"]


@dataclass
class Capability:
    key: str
    label: str
    support: Support
    note: str | None = None


@dataclass
class TaskSpec:
    query_group: str
    region_scope: str
    region_label: str | None
    query: str | None = None
    cursor: str | None = None
    depth: int = 0


@dataclass
class DiscoveryPlan:
    tasks: list[TaskSpec]
    target_label: str
    region_list_status: Literal["verified", "unverified", "not_applicable"]
    unit_label: str
    depth_limit: int
    notes: list[str] = field(default_factory=list)
    #: 전국 목표 대비 지역 목록이 확보된 광역 단위 수 (알 수 없으면 None)
    target_units_total: int | None = None
    target_units_covered: int | None = None
    #: 완료 시 표시할 탐색 범위 설명 (없으면 지역 목록 상태로 판단)
    coverage_note: str | None = None


@dataclass
class PostRef:
    url: str
    source_post_id: str | None = None
    title_hint: str | None = None


@dataclass
class DiscoverResult:
    refs: list[PostRef]
    next_cursor: str | None = None


@dataclass
class ParsedPost:
    title: str
    body: str
    original_url: str | None
    canonical_url: str | None
    source_post_id: str | None
    posted_region_raw: str | None = None
    workplace_raw: str | None = None
    applicant_region_raw: str | None = None
    published: DateInfo = field(default_factory=DateInfo)
    source_updated: DateInfo = field(default_factory=DateInfo)
    deadline: DateInfo | None = None
    pay: PayInfo | None = None
    #: 원천이 제공한 신뢰할 수 있는 상태 (없으면 None → 본문 규칙으로 판단)
    source_status: str | None = None
    status_evidence: list[Ev] = field(default_factory=list)
    contact_channel: str | None = None
    parse_notes: list[str] = field(default_factory=list)


@dataclass
class Revalidation:
    source_status: str | None  # None = 판단 보류 (기존 값 유지)
    access_status: str
    note: str | None = None
    parsed: ParsedPost | None = None
    http_status: int | None = None


@dataclass
class HealthReport:
    status: Literal["ok", "degraded", "blocked", "error", "paused", "unknown"]
    message: str | None
    code: str | None = None


class SourceNotReady(Exception):
    """조사·정책 확인이 끝나지 않아 실행할 수 없는 상태. '정상 0건'으로 기록하지 않는다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ParseError(Exception):
    pass


class SourceAdapter(Protocol):
    source_id: str
    name: str
    kind: Literal["site", "manual"]
    adapter_version: str
    parser_version: str
    allowed_hosts: set[str]
    daily_request_budget: int
    min_interval_seconds: float
    scope_note: str | None
    default_interval_minutes: int | None

    def describe_capabilities(self) -> list[Capability]: ...

    def healthcheck(self) -> HealthReport: ...

    def plan_discovery(self, query_groups: list[dict], now: datetime) -> DiscoveryPlan: ...

    def discover(self, task: TaskSpec, fetcher: Fetcher) -> DiscoverResult: ...

    def fetch_detail(self, ref: PostRef, fetcher: Fetcher) -> FetchResult: ...

    def parse(self, ref: PostRef, result: FetchResult, observed_at: datetime) -> ParsedPost: ...

    def revalidate(self, url: str, fetcher: Fetcher, observed_at: datetime) -> Revalidation: ...

    def block_detector(self, result: FetchResult) -> str | None: ...


def query_label(query: str | None) -> str | None:
    """계획에 저장된 검색어 목록(줄바꿈 구분)을 짧게 표시."""
    if not query or "\n" not in query:
        return query
    parts = [p for p in query.split("\n") if p]
    return f"제목 선별 {parts[0]} 외 {len(parts) - 1}개"
