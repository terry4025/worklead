"""API 계약 스키마 (Pydantic). contracts/openapi.json 은 여기서 생성된다.

규칙: 모르는 값은 null, 금액은 최소 통화 단위 정수 또는 null (0 은 실제 0 일 때만),
시각은 UTC ISO 8601. 상태 축은 합치지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

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
QueueId = Literal["fresh", "recommended", "needs_review", "interested", "active", "dismissed", "auto_excluded", "all"]
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


CafeState = Literal["collect_only", "candidate", "needs_join", "eligible", "approved", "excluded"]


class CafeTemplate(Model):
    version: int = Field(ge=1)
    title: str
    body: str


class CafeTemplatePatch(Model):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


class CafeAttempt(Model):
    id: str
    cafe_id: str
    board_id: str
    template_version: int
    status: Literal['pending', 'posted', 'already_exists', 'posted_mismatch', 'failed', 'removed', 'skipped_rule']
    post_url: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    verified_at: datetime | None


class CafeAttemptList(Model):
    items: list[CafeAttempt]


class CafeCommentOut(Model):
    id: str
    attempt_id: str
    cafe_name: str
    post_url: str
    author_nickname: str
    body: str
    written_at: datetime | None
    written_raw: str | None
    is_reply: bool
    classification: Literal['request', 'question', 'other']
    read_at: datetime | None
    memo: str
    lead_id: str | None


class CafeCommentCoverage(Model):
    attempt_id: str
    post_url: str
    cafe_name: str
    reported_count: int | None = Field(default=None, ge=0)
    read_count: int = Field(ge=0)
    needs_app_check: bool
    checked_at: datetime


class CafeCommentList(Model):
    items: list[CafeCommentOut]
    unread_count: int = Field(ge=0)
    coverage: list[CafeCommentCoverage] = Field(default_factory=list)


class CafeCommentPatch(Model):
    read: bool | None = None
    memo: str | None = Field(default=None, max_length=4000)


class CafeCommentDraft(Model):
    text: str


class CafeRepostIn(Model):
    confirm: Literal[True]

    @field_validator('confirm', mode='before')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True: raise ValueError('삭제·숨김 글의 명시적 재게시 확인이 필요합니다')
        return value


class CafeSettings(Model):
    auto_post: bool = False
    auto_approve: bool = False
    min_navigation_seconds: int = Field(default=8, ge=8, le=3600)
    daily_page_limit: int = Field(default=300, ge=0, le=10000)
    daily_post_limit: int = Field(default=5, ge=1, le=50)
    min_post_minutes: int = Field(default=30, ge=30, le=10080)
    collect_interval_minutes: int = Field(default=180, ge=30, le=10080)
    comment_interval_minutes: int = Field(default=30, ge=30, le=10080)
    comment_monitor_days: int = Field(default=60, ge=1, le=60)
    keywords: list[str] = Field(default_factory=lambda: ["알바", "구인구직", "아르바이트", "외주", "프리랜서", "개발", "IT", "부업", "재택"], max_length=50)

    @field_validator("keywords")
    @classmethod
    def valid_keywords(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("검색어는 1~100자여야 합니다")
        return list(dict.fromkeys(value.strip() for value in values))


class CafeSettingsPatch(Model):
    auto_post: bool | None = None
    auto_approve: bool | None = None
    min_navigation_seconds: int | None = Field(default=None, ge=8, le=3600)
    daily_page_limit: int | None = Field(default=None, ge=0, le=10000)
    daily_post_limit: int | None = Field(default=None, ge=1, le=50)
    min_post_minutes: int | None = Field(default=None, ge=30, le=10080)
    collect_interval_minutes: int | None = Field(default=None, ge=30, le=10080)
    comment_interval_minutes: int | None = Field(default=None, ge=30, le=10080)
    comment_monitor_days: int | None = Field(default=None, ge=1, le=60)
    keywords: list[str] | None = Field(default=None, max_length=50)


class CafeConsentIn(Model):
    version: str


class CafeConsentOut(Model):
    version: str
    accepted_at: datetime


class CafeSessionOut(Model):
    state: Literal["logged_out", "logged_in", "needs_user"]
    checked_at: datetime | None
    reason: str | None


class CafeUsage(Model):
    day: str
    page_count: int
    daily_page_limit: int
    post_count: int
    daily_post_limit: int


class CafeCapabilities(Model):
    collection: bool = True
    own_posts: bool = False
    full_comments: bool = False
    posting: bool = True
    title_duplicates: bool = True
    visible_comments: bool = True


class CafeJob(Model):
    id: str
    kind: Literal["check", "login", "discover", "inspect", "collect", "post", "comments"]
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    result: dict[str, Any]


class CafeSnapshot(Model):
    consent: CafeConsentOut | None
    consent_text: str
    consent_version: str
    session: CafeSessionOut
    usage: CafeUsage
    capabilities: CafeCapabilities
    settings: CafeSettings
    active_job: CafeJob | None


class CafeBoardOut(Model):
    id: str
    cafe_id: str
    name: str
    url: str
    kind: Literal["job_seek", "job_offer", "free", "other"]
    post_allowed: bool
    approved: bool
    evidence: str | None


class CafeEntry(Model):
    id: str
    url: str
    name: str
    region: str | None
    category: str | None
    member_count: int | None
    post_count: int | None
    joined: bool | None
    status: CafeState
    status_reason: str | None
    rule_evidence: str | None
    first_seen_at: datetime
    last_checked_at: datetime | None
    approved_at: datetime | None
    boards: list[CafeBoardOut]


class CafeList(Model):
    items: list[CafeEntry]


class CafeJobList(Model):
    items: list[CafeJob]


class CafeUrlIn(Model):
    url: str = Field(min_length=1, max_length=2000)


class CafeStatePatch(Model):
    status: CafeState


class CafeBoardPatch(Model):
    approved: bool


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
class LeadFreshness(Model):
    discovery_delay_seconds: float | None = Field(default=None, ge=0, description="정확한 현재 게시 시각부터 최초 발견까지. 미래·근사 시각은 null; 최초 게시 지연 보장은 아님")
    first_listed_at: datetime | None = Field(default=None, description="이 설치가 공개 목록에서 처음 관찰한 시각. 기능 도입 전 노출은 추정하지 않음")
    listing_to_detail_seconds: float | None = Field(default=None, ge=0)


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
    freshness: LeadFreshness | None = None
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


class AIInterpretation(Model):
    suggested_intent: DemandIntent
    evidence_quotes: list[str]
    uncertain: list[str]
    questions: list[str]


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
    ai_interpretation: AIInterpretation | None = None


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
    confirmed: bool = False
    dismissed: bool = False
    revision: int = Field(default=0, ge=0)
    database_generation: int = Field(default=0, ge=0)


class RelationUpdateIn(Model):
    action: Literal["confirm", "dismiss"]
    expected_revision: int = Field(ge=0)
    expected_generation: int = Field(ge=0)


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


class JobApplicationHold(Model):
    code: str
    message: str


class JobApplicationCandidate(Model):
    lead_id: str
    title: str
    original_url: str | None
    recommendation: Recommendation
    published_at: datetime | None
    last_checked_at: datetime | None
    candidate: bool
    can_prepare: bool
    can_submit: Literal[False] = False
    mode: Literal["draft_only"] = "draft_only"
    submission_supported: Literal[False] = False
    hold_reasons: list[JobApplicationHold]
    questions: list[str]
    draft: Draft | None


class JobApplicationList(Model):
    mode: Literal["draft_only"] = "draft_only"
    submission_supported: Literal[False] = False
    total: int = Field(ge=0)
    items: list[JobApplicationCandidate]


class JobApplicationDraftPatch(Model):
    text: str = Field(min_length=1, max_length=20000)
    expected_text: str | None


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


class RelationUpdateOut(Model):
    lead: LeadDetail
    related_lead: LeadDetail


class FeedbackIn(Model):
    kind: Literal["remote", "real_request"]
    value: Literal["confirmed", "denied", "yes", "no", "clear"]
    note: str | None = Field(default=None, max_length=2000)


class OutcomeIn(Model):
    kind: OutcomeKind
    amount: int | None = Field(default=None, strict=True, ge=0, le=9_007_199_254_740_991, description="최소 통화 단위의 안전한 정수. 모르면 null")
    currency: str = Field(default="KRW", strict=True, pattern=r"^[A-Z]{3}$")
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


class SourceMonitoring(Model):
    state: Literal["disabled", "stopped", "never", "running", "waiting", "overdue"]
    interval_minutes: int
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    next_due_at: datetime | None = None
    overdue_seconds: int = Field(default=0, ge=0)
    previous_gap_seconds: int | None = Field(default=None, ge=0, description="직전 탐색 종료부터 최근 탐색 시작까지 실제 간격. OS·앱 중단 원인은 추정하지 않음")


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
    monitoring: SourceMonitoring | None = None


class SourceList(Model):
    items: list[SourceOut]


class PolicyReviewIn(Model):
    status: Literal["permission_pending", "allowed", "restricted", "blocked"]
    basis: str | None = Field(default=None, max_length=4000, description="허용·제한 판단 근거 (약관 조항, 허가 문서 등). allowed/restricted 에 필수")
    note: str | None = Field(default=None, max_length=4000)


class SourcePatch(Model):
    auto_collect_enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)
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
    skipped: int = 0
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
    business_registered: bool = False
    tax_invoice: bool = False
    portfolio_url: str = Field(default="https://www.seyj.kr/", max_length=300, pattern=r"^https://[^\s]+$")
    price_anchor_note: str = Field(default="", max_length=300)


class QuietHours(Model):
    enabled: bool
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class NotificationSettings(Model):
    new_recommended: bool
    new_review: bool = True
    daangn_messages: bool = True
    meaningful_change: bool
    source_issue: bool
    quiet_hours: QuietHours


class AISettings(Model):
    enabled: bool
    external_transfer_consent: bool
    engine_label: str | None
    monthly_cost_cap: int | None = Field(ge=0)
    daily_cost_cap: int | None = Field(ge=0)
    key_configured: bool | None = Field(description="API 키 저장 여부. 저장소 상태를 확인하지 못하면 null. 값은 반환하지 않음")
    provider_available: bool
    provider: Literal["openai", "exaone"] = "openai"
    model: Literal["gpt-4.1-mini-2025-04-14", "k-exaone_v2.5"] = "gpt-4.1-mini-2025-04-14"
    daily_call_cap: int = Field(default=300, ge=1, le=5000)
    assist_review: bool = False
    assist_recheck: bool = False
    assist_application: bool = False
    assist_chat: bool = False
    assist_quote: bool = False
    usd_to_krw_rate: int | None = Field(default=None, ge=1, le=100000)
    max_output_tokens: int = Field(default=1024, ge=256, le=4096)
    pricing_confirmed: bool = False
    price_version: str | None = Field(default=None, description="확인한 서버 요금표 버전; 읽기 전용")
    revision: int = Field(default=0, ge=0)
    database_generation: int = Field(default=0, ge=0)


class AISettingsPatch(Model):
    enabled: bool | None = None
    external_transfer_consent: bool | None = None
    monthly_cost_cap: int | None = Field(default=None, ge=0, le=100000000)
    daily_cost_cap: int | None = Field(default=None, ge=0, le=100000000)
    provider: Literal["openai", "exaone"] | None = None
    model: Literal["gpt-4.1-mini-2025-04-14", "k-exaone_v2.5"] | None = None
    daily_call_cap: int | None = Field(default=None, ge=1, le=5000)
    assist_review: bool | None = None
    assist_recheck: bool | None = None
    assist_application: bool | None = None
    assist_chat: bool | None = None
    assist_quote: bool | None = None
    usd_to_krw_rate: int | None = Field(default=None, ge=1, le=100000)
    max_output_tokens: int | None = Field(default=None, ge=256, le=4096)
    pricing_confirmed: bool | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    expected_generation: int | None = Field(default=None, ge=0)

    @field_validator("enabled", "external_transfer_consent", "pricing_confirmed", "assist_review", "assist_recheck",
                     "assist_application", "assist_chat", "assist_quote", mode="before")
    @classmethod
    def strict_boolean(cls, value: Any) -> Any:
        if type(value) is not bool:
            raise ValueError("a boolean is required")
        return value

    @field_validator("monthly_cost_cap", "daily_cost_cap", "usd_to_krw_rate", "max_output_tokens", "expected_revision", "expected_generation",
                     "daily_call_cap", mode="before")
    @classmethod
    def strict_integer(cls, value: Any, info: Any) -> Any:
        if value is None and info.field_name in ("monthly_cost_cap", "daily_cost_cap", "usd_to_krw_rate"):
            return value
        if type(value) is not int:
            raise ValueError("an integer is required")
        return value

    @field_validator("provider", "model", mode="before")
    @classmethod
    def required_choice(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("a supported provider or model is required")
        return value


class AIPriceMetadata(Model):
    input_usd_per_million: float = Field(ge=0)
    cached_input_usd_per_million: float = Field(ge=0)
    output_usd_per_million: float = Field(ge=0)
    source_url: str
    version: str


class AIBudgetPeriod(Model):
    period: str
    estimated_krw: int = Field(ge=0)
    reserved_krw: int = Field(ge=0)
    uncertain_krw: int = Field(ge=0)
    attempts: int = Field(ge=0)


class AILedgerStatus(Model):
    day: AIBudgetPeriod
    month: AIBudgetPeriod


class AIStatus(Model):
    provider: Literal["openai", "exaone"]
    model: Literal["gpt-4.1-mini-2025-04-14", "k-exaone_v2.5"]
    key_supported: bool
    key_configured: bool | None
    error_code: str | None
    provider_available: bool
    enabled: bool
    ready: bool
    blocked_reason: str | None
    settings_revision: int = Field(ge=0)
    database_generation: int = Field(ge=0)
    pricing_checked_at: str
    price_metadata: AIPriceMetadata
    ledger: AILedgerStatus | None
    calls_today: int | None = None


class AICredentialAction(Model):
    expected_revision: int = Field(ge=0, strict=True)
    expected_generation: int = Field(ge=0, strict=True)


class AICredentialIn(AICredentialAction):
    key: SecretStr = Field(json_schema_extra={"writeOnly": True})

    @field_validator("key", mode="before")
    @classmethod
    def validate_secret(cls, value: Any) -> Any:
        # Fixed validation text; no key or submitted value in the exception.
        if not isinstance(value, str) or not value or len(value) > 2560 or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError("invalid credential")
        return value


class QueryGroup(Model):
    id: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    label: str
    enabled: bool
    keywords: list[str]


class CollectionSettings(Model):
    prevent_sleep: bool = False


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
    collection: CollectionSettings = CollectionSettings()


class SettingsPatch(Model):
    profile: ProfileSettings | None = None
    recheck: dict[Literal["ttl_hours"], int] | None = None
    notifications: NotificationSettings | None = None
    ai: AISettingsPatch | None = None
    retention: dict[Literal["raw_days"], int] | None = None
    query_groups: list[QueryGroup] | None = None
    collection: CollectionSettings | None = None

# ── 가져오기·내보내기·지표·부트스트랩 ─────────────────────────────────
class ImportIn(Model):
    format: Literal["text", "csv", "json"]
    content: str = Field(max_length=2 * 1024 * 1024)
    file_name: str | None = Field(default=None, max_length=255)
    original_url: str | None = Field(default=None, max_length=2000)


class ExportIn(Model):
    kind: Literal["leads_csv", "leads_json", "diagnostics"]


class BackupOut(Model):
    id: str
    file_name: str
    created_at: datetime
    size_bytes: int = Field(ge=0)


class BackupList(Model):
    directory: str
    items: list[BackupOut]


class BackupInspection(Model):
    backup: BackupOut
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: str | None
    can_restore: bool
    problem: str | None
    lead_count: int | None = Field(ge=0)


class RestoreIn(Model):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def require_confirmation(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("explicit confirmation is required")
        return value


class RestoreOut(Model):
    restored_backup_id: str
    safety_backup: BackupOut
    lead_count: int = Field(ge=0)
    auto_collection_paused: Literal[True]
    ai_analysis_paused: Literal[True] = True


# Native delivery never contains source titles, bodies, or contact details.
NativeNotificationKind = Literal["new_lead", "new_review_lead", "lead_changed", "source_issue", "cafe_request", "daangn_message"]
NativeNotificationState = Literal["pending", "attempted", "accepted", "failed", "unknown", "suppressed"]


class NativeNotificationItem(Model):
    id: str
    kind: NativeNotificationKind
    created_at: datetime


class NativeNotificationList(Model):
    items: list[NativeNotificationItem]
    backlog_count: int = Field(ge=0, description="현재 시각·설정·created_after 조건에서 미시도인 알림 수")


class NativeNotificationClaimOut(Model):
    allowed: bool
    kind: NativeNotificationKind | None
    state: NativeNotificationState
    claim_token: str | None
    reason: str | None


class NativeNotificationResultIn(Model):
    state: Literal["accepted", "failed", "unknown"]
    claim_token: str = Field(pattern=r"^[a-f0-9]{32}$")


class NativeNotificationResultOut(Model):
    id: str
    kind: NativeNotificationKind | None
    state: NativeNotificationState


class CategoryOut(Model):
    id: str
    label: str


class QueueOut(Model):
    id: QueueId
    label: str
    description: str


class AppNotice(Model):
    id: str
    kind: Literal["new_lead", "new_review_lead"]
    title: str
    created_at: datetime


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
    notices: list[AppNotice] = Field(default_factory=list, description="최근 24시간 생성된 영속 야간 요약. 현재 유효 공고 수가 아닌 당시 요약 기록")


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


class MoneyTotal(Model):
    amount: str | None = Field(pattern=r"^-?\d+$", description="정확한 최소 통화 단위 10진 정수 문자열. 미기록 금액이 있으면 null. 기록이 없거나 실제 합계가 0이면 '0'")
    known_amount: str = Field(pattern=r"^-?\d+$", description="금액이 기록된 행만 합산한 정확한 10진 정수 문자열. 미기록분은 추정하지 않음")
    records: int = Field(ge=0)
    missing_amounts: int = Field(ge=0)


class CurrencySales(Model):
    currency: str = Field(description="원본 통화 코드. 기존 자료의 코드도 보존하며 서로 환산하지 않음")
    contract: MoneyTotal
    collected: MoneyTotal
    refunded: MoneyTotal
    direct_cost: MoneyTotal


class SalesMetrics(Model):
    period_days: int
    period_start: date | None = None
    period_end: date | None = None
    time_zone: Literal["Asia/Seoul"] = "Asia/Seoul"
    stage_scope: Literal["current_all"] = Field(default="current_all", description="연락·협의·수주 수는 기간 전환율이 아닌 현재 전체 리드의 단계별 수")
    reviewed: int
    contacted: int
    negotiating: int
    won: int
    contracts_confirmed: int = Field(description="발생일 기간 안의 전체 통화 계약 확인 기록 수")
    contract_amount: int | None = Field(description="KRW 계약 확인 합계. 금액 미기록 또는 안전한 JS 정수 범위를 넘으면 null. 정확값은 by_currency에 보존")
    collected: int | None = Field(description="KRW 실제 수금 합계. 금액 미기록 또는 안전한 JS 정수 범위를 넘으면 null. 정확값은 by_currency에 보존. 수주 표시만으로 늘지 않음")
    refunded: int | None = Field(description="KRW 환불 합계. 금액 미기록 또는 안전한 JS 정수 범위를 넘으면 null. 정확값은 by_currency에 보존")
    direct_cost: int | None = Field(description="KRW 직접 비용 합계. 금액 미기록 또는 안전한 JS 정수 범위를 넘으면 null. 정확값은 by_currency에 보존")
    by_currency: list[CurrencySales] = Field(default_factory=list)


class SourceFreshnessMetrics(Model):
    source_id: str
    period_days: int = 7
    records: int
    samples: int
    unavailable_samples: int
    median_discovery_seconds: float | None = None
    p95_discovery_seconds: float | None = None
    within_15_minutes: int
    within_1_hour: int
    note: str


class MetricsOut(Model):
    collection: CollectionMetrics
    quality: QualityMetrics
    sales: SalesMetrics
    freshness: list[SourceFreshnessMetrics] = Field(default_factory=list)
    note: str
