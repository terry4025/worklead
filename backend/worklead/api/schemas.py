"""API 계약 스키마 (Pydantic). contracts/openapi.json 은 여기서 생성된다.

규칙: 모르는 값은 null, 금액은 최소 통화 단위 정수 또는 null (0 은 실제 0 일 때만),
시각은 UTC ISO 8601. 상태 축은 합치지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Basis = Literal["explicit", "inferred", "user_confirmed"]
Confidence = Literal["high", "medium", "low"]
DatePrecision = Literal["exact", "day", "approximate", "unknown"]
WorkMode = Literal["fully_remote", "hybrid", "onsite", "negotiable", "unknown"]
CollaborationMode = Literal["online_only", "onsite_required", "negotiable", "unknown"]
ApplicantScope = Literal["nationwide", "regional_restriction", "unknown"]
DemandIntent = Literal["buyer_project", "buyer_ongoing", "employee_hiring", "seller_service", "job_seeker", "information", "unknown"]
EngagementType = Literal["project", "hourly_contract", "part_time", "full_time", "unknown"]
SourceStatus = Literal["open", "closed", "deleted", "unknown"]
AccessStatus = Literal["accessible", "login_required", "blocked", "error"]
AnalysisStatus = Literal["pending", "rules_only", "complete", "failed", "stale"]
SalesStage = Literal["new", "reviewing", "contacted", "negotiating", "won", "lost", "on_hold", "ignored"]
Recommendation = Literal["recommended", "needs_review", "excluded"]
PayUnit = Literal["project", "hour", "day", "week", "month", "negotiable", "unknown"]
UserMark = Literal["interested", "dismissed"]
QueueId = Literal["recommended", "needs_review", "interested", "active", "dismissed", "auto_excluded", "all"]
RemoteFilter = Literal["any", "confirmed", "confirmed_or_inferred", "unknown", "onsite"]
RecruitFilter = Literal["not_closed", "open_fresh", "recheck", "unknown", "closed", "access_issue", "any"]
IntentFilter = Literal["any", "buyer", "hiring", "seller", "other"]
SortKey = Literal["priority", "published", "checked"]
PolicyStatus = Literal["permission_pending", "allowed", "restricted", "blocked", "not_required"]
SourceHealth = Literal["ok", "degraded", "blocked", "error", "paused", "unknown"]
Support = Literal["supported", "unsupported", "unverified"]
RunState = Literal["queued", "running", "paused", "succeeded", "partial", "failed", "cancelled"]
RunKind = Literal["discovery", "recheck_recent", "recheck_stale", "recheck_lead", "reanalyze_lead", "reanalyze_all", "import", "export"]
OutcomeKind = Literal["contract_confirmed", "payment_received", "refund", "direct_cost"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── 오류 ─────────────────────────────────────────────────────────────
class ErrorBody(Model):
    code: str
    message: str
    details: Any | None = None
    request_id: str
    retryable: bool


class ErrorResponse(Model):
    error: ErrorBody


# ── 공통 조각 ─────────────────────────────────────────────────────────
class IntentAssessed(Model):
    value: DemandIntent
    basis: Basis | None


class EngagementAssessed(Model):
    value: EngagementType
    basis: Basis | None


class WorkModeAssessed(Model):
    value: WorkMode
    basis: Basis | None


class CollaborationAssessed(Model):
    value: CollaborationMode
    basis: Basis | None


class ScopeAssessed(Model):
    value: ApplicantScope
    basis: Basis | None


class Pay(Model):
    raw: str | None
    currency: str
    min: int | None
    max: int | None
    unit: PayUnit
    negotiable: bool


class FuzzyDate(Model):
    at: datetime | None
    precision: DatePrecision
    raw: str | None


class Reason(Model):
    tone: Literal["positive", "caution", "negative"]
    text: str


class Priority(Model):
    total: int | None = Field(description="규칙 기반 우선순위 (0-100). 수주 확률이 아니다.")
    unknown_factors: int


class Span(Model):
    start: int
    end: int


# ── 리드 ─────────────────────────────────────────────────────────────
class LeadSummary(Model):
    id: str
    source_id: str
    title: str
    categories: list[str]
    demand_intent: IntentAssessed
    engagement_type: EngagementAssessed
    work_mode: WorkModeAssessed
    collaboration_mode: CollaborationAssessed
    applicant_scope: ScopeAssessed
    pay: Pay
    source_status: SourceStatus
    access_status: AccessStatus
    analysis_status: AnalysisStatus
    recheck_due: bool = Field(description="모집 중이지만 확인 TTL 이 지남")
    last_checked_at: datetime | None
    published: FuzzyDate
    first_seen_at: datetime
    deadline: FuzzyDate = Field(description="구인 마감일 (근무일·납기와 다름)")
    posted_region: str | None
    found_in: list[str] = Field(description="이 공고가 발견된 탐색 지역 (게시 지역과 다를 수 있음)")
    recommendation: Recommendation
    priority: Priority
    reasons: list[Reason]
    user_mark: UserMark | None
    sales_stage: SalesStage
    has_memo: bool
    active_job: Literal["recheck", "reanalyze"] | None


class LeadPage(Model):
    items: list[LeadSummary]
    next_cursor: str | None
    has_more: bool


class QueueCounts(Model):
    counts: dict[QueueId, int]


class Evidence(Model):
    id: str
    quote: str
    span: Span | None = Field(description="body_text 기준 문자 위치")
    basis: Basis
    confidence: Confidence | None = Field(description="규칙 신뢰 수준. 모델 자기평가 확률이 아니다.")
    observed_at: datetime
    source_record_id: str
    version: str | None
    note: str | None


class ScoreFactor(Model):
    key: str
    label: str
    max: int
    score: int | None = Field(description="null = 근거 부족으로 평가하지 않음 (0점과 구분)")
    reason: str


class RiskPenalty(Model):
    score: int
    reasons: list[str]


class ScoreBreakdown(Model):
    total: int | None
    factors: list[ScoreFactor]
    risk_penalty: RiskPenalty
    rule_version: str


class RiskSignal(Model):
    id: str
    label: str
    quote: str | None
    span: Span | None


class AnalysisFailure(Model):
    code: str
    message: str


class Analysis(Model):
    status: AnalysisStatus
    summary: str | None
    summary_engine: Literal["rules", "ai"] | None
    fit: list[str]
    unfit: list[str]
    uncertain: list[str]
    deliverables: list[str]
    tech_requirements: list[str]
    questions: list[str]
    next_action: str | None
    conversion_opportunity: str | None
    engine: str | None
    version: str | None
    analyzed_at: datetime | None
    failure: AnalysisFailure | None


class ProfitScenario(Model):
    key: Literal["conservative", "base", "optimistic"]
    contract_value: int | None
    direct_cost: int | None
    hours: float | None
    contribution: int | None = Field(description="예상 기여액 = 계약 대가 - 직접 비용 (세후 순이익 아님)")
    effective_hourly: int | None
    residual_after_target: int | None


class Profitability(Model):
    status: Literal["calculated", "hypothesis", "not_calculated"]
    basis: str | None
    reason: str | None
    assumptions: list[str]
    scenarios: list[ProfitScenario]
    revenue_type: Literal["one_time", "recurring", "unknown"]
    target_hourly: int | None


class Outcome(Model):
    id: str
    kind: OutcomeKind
    amount: int | None
    currency: str
    occurred_on: date
    note: str | None
    evidence_ref: str | None
    recorded_at: datetime


class RelatedRecord(Model):
    id: str
    lead_id: str | None
    source_id: str
    title: str
    url: str | None
    relation: Literal["duplicate", "repost", "similar"]
    basis: str | None
    seen_at: datetime


class DiscoveryPath(Model):
    region_scope: str
    region_label: str | None
    query_group: str
    first_seen_at: datetime
    last_seen_at: datetime
    times_seen: int


class Draft(Model):
    text: str
    generated_at: datetime | None
    edited_by_user: bool


class FeedbackState(Model):
    remote: Literal["confirmed", "denied"] | None
    real_request: Literal["yes", "no"] | None


class Activity(Model):
    at: datetime
    kind: str
    text: str


class WorkPeriod(Model):
    start: FuzzyDate
    end: FuzzyDate


class LeadDetail(LeadSummary):
    body_text: str | None = Field(description="안전한 텍스트. 보존 기간이 지나면 null")
    body_retained_until: datetime | None
    original_url: str | None
    contact_channel: str | None
    workplace: str | None
    applicant_region: str | None
    source_updated: FuzzyDate
    last_seen_at: datetime | None
    work_period: WorkPeriod
    evidence: dict[str, list[Evidence]]
    score: ScoreBreakdown | None
    analysis: Analysis
    risks: list[RiskSignal]
    profitability: Profitability | None
    draft: Draft | None
    memo: str
    outcomes: list[Outcome]
    related: list[RelatedRecord]
    discovery_paths: list[DiscoveryPath]
    activity: list[Activity]
    feedback: FeedbackState
    parser_version: str
    content_hash: str


class LeadPatch(Model):
    user_mark: UserMark | None = None
    sales_stage: SalesStage | None = None
    memo: str | None = Field(default=None, max_length=20000)
    draft_text: str | None = Field(default=None, max_length=20000)


class FeedbackIn(Model):
    kind: Literal["remote", "real_request"]
    value: Literal["confirmed", "denied", "yes", "no", "clear"]
    note: str | None = Field(default=None, max_length=2000)


class OutcomeIn(Model):
    kind: OutcomeKind
    amount: int | None = Field(default=None, ge=0, description="최소 통화 단위 정수. 모르면 null")
    currency: str = "KRW"
    occurred_on: date
    note: str | None = Field(default=None, max_length=2000)
    evidence_ref: str | None = Field(default=None, max_length=500)


# ── 소스·실행 ─────────────────────────────────────────────────────────
class Capability(Model):
    key: str
    label: str
    support: Support
    note: str | None


class Budget(Model):
    used: int
    limit: int | None
    unit_label: str


class Coverage(Model):
    target_label: str = Field(description="탐색 목표 (예: 전국). 실제 확인 범위와 별개")
    region_list_status: Literal["verified", "unverified", "not_applicable", "unknown"]
    unit_label: str
    planned: int | None
    completed: int
    pending: int
    blocked: int
    failed: int
    scan_cycle: int | None
    last_visited_at: datetime | None
    next_up: str | None
    depth_limit: int | None
    budget: Budget | None
    market_coverage: Literal["unknown"] = Field(description="원천 전체 모집단을 모르면 unknown")
    target_units_total: int | None
    target_units_covered: int | None
    estimated_cycle_days: float | None
    notes: list[str]


class Policy(Model):
    status: PolicyStatus
    basis: str | None
    note: str | None
    reviewed_at: datetime | None
    reviewed_by: str | None
    robots_status: str
    robots_checked_at: datetime | None
    robots_summary: str | None


class Health(Model):
    status: SourceHealth
    checked_at: datetime | None
    message: str | None
    code: str | None


class AutoCollect(Model):
    enabled: bool
    interval_minutes: int | None


class Research(Model):
    ready: bool
    missing: list[str]


class SourceOut(Model):
    id: str
    name: str
    kind: Literal["site", "manual"]
    scope_note: str | None
    adapter_version: str
    policy: Policy
    auto_collect: AutoCollect
    health: Health
    stopped_reason: str | None
    capabilities: list[Capability]
    coverage: Coverage | None
    research: Research
    last_run_id: str | None


class SourceList(Model):
    items: list[SourceOut]


class PolicyReviewIn(Model):
    status: Literal["permission_pending", "allowed", "restricted", "blocked"]
    basis: str | None = Field(default=None, max_length=4000, description="허용·제한 판단 근거 (약관 조항, 허가 문서 등). allowed/restricted 에 필수")
    note: str | None = Field(default=None, max_length=4000)


class SourcePatch(Model):
    auto_collect_enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=60, le=10080)
    clear_stop: bool | None = Field(default=None, description="차단으로 정지된 소스를 사용자가 확인 후 해제")
    policy: PolicyReviewIn | None = None


class Progress(Model):
    done: int
    total: int | None
    label: str | None


class RunCounts(Model):
    requests: int
    details_fetched: int
    created: int
    updated: int
    duplicates: int
    excluded: int
    parse_failures: int
    fetch_failures: int
    policy_stops: int
    ai_failures: int


class RunError(Model):
    code: str
    message: str
    retry_after: datetime | None


class RunOut(Model):
    id: str
    source_id: str | None
    lead_id: str | None
    kind: RunKind
    state: RunState
    trigger: Literal["manual", "schedule"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: Progress
    counts: RunCounts
    error: RunError | None
    note: str | None
    scan_cycle: int | None
    result: dict[str, Any]


class RunPage(Model):
    items: list[RunOut]
    next_cursor: str | None
    has_more: bool


class RunCreate(Model):
    source_id: str
    kind: Literal["discovery", "recheck_recent", "recheck_stale"]
    idempotency_key: str | None = Field(default=None, max_length=200)


class RunActionIn(Model):
    action: Literal["pause", "resume", "cancel"]


class JobAccepted(Model):
    job_id: str
    run_id: str | None


# ── 설정 ─────────────────────────────────────────────────────────────
class ProfileSettings(Model):
    services: list[str]
    skills: str
    excluded_work: str
    min_contract: int | None = Field(ge=0)
    target_hourly: int | None = Field(ge=0)
    weekly_hours: int | None = Field(ge=0, le=168)
    onsite: Literal["no", "first_meeting", "yes"]
    allow_short_term_employment: bool


class QuietHours(Model):
    enabled: bool
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class NotificationSettings(Model):
    new_recommended: bool
    meaningful_change: bool
    source_issue: bool
    quiet_hours: QuietHours


class AISettings(Model):
    enabled: bool
    external_transfer_consent: bool
    engine_label: str | None
    monthly_cost_cap: int | None = Field(ge=0)
    daily_cost_cap: int | None = Field(ge=0)
    key_configured: bool = Field(description="API 키 저장 여부 (값은 절대 반환하지 않음)")
    provider_available: bool


class QueryGroup(Model):
    id: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    label: str
    enabled: bool
    keywords: list[str]


class QueryGroups(Model):
    version: int
    groups: list[QueryGroup]


class SettingsOut(Model):
    profile: ProfileSettings
    recheck: dict[Literal["ttl_hours"], int]
    notifications: NotificationSettings
    ai: AISettings
    retention: dict[Literal["raw_days"], int]
    query_groups: QueryGroups


class SettingsPatch(Model):
    profile: ProfileSettings | None = None
    recheck: dict[Literal["ttl_hours"], int] | None = None
    notifications: NotificationSettings | None = None
    ai: dict[Literal["enabled", "external_transfer_consent", "monthly_cost_cap", "daily_cost_cap"], bool | int | None] | None = None
    retention: dict[Literal["raw_days"], int] | None = None
    query_groups: list[QueryGroup] | None = None


# ── 가져오기·내보내기·지표·부트스트랩 ─────────────────────────────────
class ImportIn(Model):
    format: Literal["text", "csv", "json"]
    content: str = Field(max_length=2 * 1024 * 1024)
    file_name: str | None = Field(default=None, max_length=255)
    original_url: str | None = Field(default=None, max_length=2000)


class ExportIn(Model):
    kind: Literal["leads_csv", "leads_json", "diagnostics"]


class CategoryOut(Model):
    id: str
    label: str


class QueueOut(Model):
    id: QueueId
    label: str
    description: str


class BootstrapOut(Model):
    mode: Literal["live", "demo"]
    app_version: str
    api_version: str
    contract_version: str
    server_time: datetime
    categories: list[CategoryOut]
    queues: list[QueueOut]
    recheck_ttl_hours: int
    event_seq: int


class HealthOut(Model):
    status: Literal["ok", "degraded"]
    version: str
    db: Literal["ok", "error"]
    workers: Literal["running", "stopped"]
    time: datetime


class CollectionMetrics(Model):
    period_days: int
    runs: int
    requests: int
    details_fetched: int
    created: int
    duplicates: int
    parse_failures: int
    fetch_failures: int
    policy_stops: int
    ai_failures: int
    ai_cost_krw: int | None = Field(description="확인 가능한 비용 합계. 모르면 null")


class QualityMetrics(Model):
    period_days: int
    reviewed: int = Field(description="분모: 사용자가 판단(관심·제외·진행)을 남긴 리드 수")
    confirmed_fit: int
    remote_false_positive: int
    buyer_false_positive: int
    median_hours_to_review: float | None
    note: str


class SalesMetrics(Model):
    period_days: int
    reviewed: int
    contacted: int
    negotiating: int
    won: int
    contracts_confirmed: int
    contract_amount: int | None
    collected: int = Field(description="실제 수금 기록 합계 (won 표시만으로 늘지 않음)")
    refunded: int
    direct_cost: int


class MetricsOut(Model):
    collection: CollectionMetrics
    quality: QualityMetrics
    sales: SalesMetrics
    note: str
