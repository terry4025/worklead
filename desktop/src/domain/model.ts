/**
 * 화면 모델 (view model)
 *
 * - 전송 형식(contracts/openapi.json, snake_case)과 분리된 화면용 모델이다.
 *   data/live/mapper.ts 가 계약 타입을 이 모델로 변환한다 (타입 검사로 누락을 잡는다).
 * - enum 값은 계약·docs/REQUIREMENTS.md 의 용어를 그대로 쓴다.
 * - 모르는 값은 null 이다. 금액 null 은 0원이 아니다.
 * - 시각은 UTC ISO 8601 문자열로 받고 화면에서 한국 시간으로 표시한다.
 */

export type IsoDateTime = string;

// ── 분류 축 (서로 독립) ───────────────────────────────────────────────
export type WorkMode = 'fully_remote' | 'hybrid' | 'onsite' | 'negotiable' | 'unknown';
export type CollaborationMode = 'online_only' | 'onsite_required' | 'negotiable' | 'unknown';
export type ApplicantScope = 'nationwide' | 'regional_restriction' | 'unknown';
export type DemandIntent =
  | 'buyer_project'
  | 'buyer_ongoing'
  | 'employee_hiring'
  | 'seller_service'
  | 'job_seeker'
  | 'information'
  | 'unknown';
export type EngagementType = 'project' | 'hourly_contract' | 'part_time' | 'full_time' | 'unknown';

// ── 상태 축 (하나의 status 로 합치지 않는다) ────────────────────────────
export type SourceStatus = 'open' | 'closed' | 'deleted' | 'unknown';
export type AccessStatus = 'accessible' | 'login_required' | 'blocked' | 'error';
export type AnalysisStatus = 'pending' | 'rules_only' | 'complete' | 'failed' | 'stale';
export type SalesStage =
  | 'new'
  | 'reviewing'
  | 'contacted'
  | 'negotiating'
  | 'won'
  | 'lost'
  | 'on_hold'
  | 'ignored';
export type Recommendation = 'recommended' | 'needs_review' | 'excluded';

export type PayUnit = 'project' | 'hour' | 'day' | 'week' | 'month' | 'negotiable' | 'unknown';
export type EvidenceBasis = 'explicit' | 'inferred' | 'user_confirmed';
export type Confidence = 'high' | 'medium' | 'low';
export type DatePrecision = 'exact' | 'day' | 'approximate' | 'unknown';

/** 사용자의 관심/제외 표시. 자동 판정(recommendation)과 별도로 저장된다. */
export type UserMark = 'interested' | 'dismissed' | null;

/** 판정 값과 가장 강한 근거 유형. 근거가 없으면 basis 는 null. */
export interface Assessed<T> {
  value: T;
  basis: EvidenceBasis | null;
}

export interface Pay {
  /** 보수 원문 표현. 없으면 null */
  raw: string | null;
  currency: string;
  /** 최소 통화 단위 정수. 모르면 null (0 아님) */
  min: number | null;
  max: number | null;
  unit: PayUnit;
  negotiable: boolean;
}

export interface FuzzyDate {
  at: IsoDateTime | null;
  precision: DatePrecision;
  /** 원문 표현 (예: "3일 전"). 정밀도가 낮을 때 함께 보여준다. */
  raw: string | null;
}

export interface Reason {
  tone: 'positive' | 'caution' | 'negative';
  text: string;
}

export interface LeadSummary {
  id: string;
  sourceId: string;
  title: string;
  categories: string[];
  demandIntent: Assessed<DemandIntent>;
  engagementType: Assessed<EngagementType>;
  workMode: Assessed<WorkMode>;
  collaborationMode: Assessed<CollaborationMode>;
  applicantScope: Assessed<ApplicantScope>;
  pay: Pay;
  sourceStatus: SourceStatus;
  accessStatus: AccessStatus;
  analysisStatus: AnalysisStatus;
  /** 백엔드가 TTL 기준으로 파생한 재확인 필요 여부 */
  recheckDue: boolean;
  lastCheckedAt: IsoDateTime | null;
  published: FuzzyDate;
  firstSeenAt: IsoDateTime;
  /** 구인 마감일. 근무일·납기와 다르다. */
  deadline: FuzzyDate;
  postedRegion: string | null;
  /** 이 공고가 발견된 탐색 지역 (게시 지역과 다를 수 있음) */
  foundIn: string[];
  recommendation: Recommendation;
  /** 규칙 기반 우선순위. 수주 확률이 아니다. */
  priority: { total: number | null; unknownFactors: number };
  reasons: Reason[];
  userMark: UserMark;
  salesStage: SalesStage;
  hasMemo: boolean;
  activeJob: 'recheck' | 'reanalyze' | null;
}

export interface Evidence {
  id: string;
  quote: string;
  /** bodyText 기준 문자 위치. 없으면 인용만 표시 */
  span: { start: number; end: number } | null;
  basis: EvidenceBasis;
  confidence: Confidence | null;
  observedAt: IsoDateTime;
  sourceRecordId: string;
  version: string | null;
  note: string | null;
}

export type EvidenceKey =
  | 'demandIntent'
  | 'engagementType'
  | 'workMode'
  | 'collaborationMode'
  | 'applicantScope'
  | 'pay'
  | 'sourceStatus'
  | 'deadline';

export interface ScoreFactor {
  key: string;
  label: string;
  max: number;
  /** null = 근거 부족으로 평가하지 않음 (0점 처리와 구분) */
  score: number | null;
  reason: string;
}

export interface ScoreBreakdown {
  total: number | null;
  factors: ScoreFactor[];
  riskPenalty: { score: number; reasons: string[] };
  ruleVersion: string;
}

export interface RiskSignal {
  id: string;
  label: string;
  quote: string | null;
  span: { start: number; end: number } | null;
}

export interface AnalysisView {
  status: AnalysisStatus;
  summary: string | null;
  fit: string[];
  unfit: string[];
  uncertain: string[];
  deliverables: string[];
  techRequirements: string[];
  questions: string[];
  nextAction: string | null;
  /** 이미 존재하는 개발 의뢰가 아닌, 제안 가능성. 의뢰로 집계하지 않는다. */
  conversionOpportunity: string | null;
  summaryEngine: 'rules' | 'ai' | null;
  engine: string | null;
  version: string | null;
  analyzedAt: IsoDateTime | null;
  failure: { code: string; message: string } | null;
}

export type ScenarioKey = 'conservative' | 'base' | 'optimistic';

export interface ProfitScenario {
  key: ScenarioKey;
  contractValue: number | null;
  directCost: number | null;
  hours: number | null;
  /** 예상 기여액 = 계약 대가 - 직접 비용 */
  contribution: number | null;
  /** 시간당 실효 수익 = 예상 기여액 / 총 투입 시간 */
  effectiveHourly: number | null;
  /** 목표 시간가치 반영 잔여액 */
  residualAfterTarget: number | null;
}

export interface Profitability {
  status: 'calculated' | 'hypothesis' | 'not_calculated';
  /** 계산 기준 설명 (예: 게시 예산, 제안 견적 가설) */
  basis: string | null;
  /** 계산하지 않은 이유 */
  reason: string | null;
  assumptions: string[];
  scenarios: ProfitScenario[];
  revenueType: 'one_time' | 'recurring' | 'unknown';
  targetHourly: number | null;
}

export type OutcomeKind = 'contract_confirmed' | 'payment_received' | 'refund' | 'direct_cost';

export interface Outcome {
  id: string;
  kind: OutcomeKind;
  amount: number | null;
  currency: string;
  /** YYYY-MM-DD (한국 날짜) */
  occurredOn: string;
  note: string | null;
  evidenceRef: string | null;
  recordedAt: IsoDateTime;
}

export interface RelatedRecord {
  id: string;
  leadId: string | null;
  sourceId: string;
  title: string;
  url: string | null;
  relation: 'duplicate' | 'repost' | 'similar';
  basis: string | null;
  seenAt: IsoDateTime;
}

export interface DiscoveryPathView {
  regionScope: string;
  regionLabel: string | null;
  queryGroup: string;
  firstSeenAt: IsoDateTime;
  lastSeenAt: IsoDateTime;
  timesSeen: number;
}

export interface Draft {
  text: string;
  generatedAt: IsoDateTime | null;
  editedByUser: boolean;
}

export interface UserFeedbackState {
  remote: 'confirmed' | 'denied' | null;
  realRequest: 'yes' | 'no' | null;
}

export interface LeadDetail extends LeadSummary {
  /** 보존 기간이 지나면 null */
  bodyText: string | null;
  bodyRetainedUntil: IsoDateTime | null;
  originalUrl: string | null;
  contactChannel: string | null;
  workplace: string | null;
  applicantRegion: string | null;
  sourceUpdated: FuzzyDate;
  lastSeenAt: IsoDateTime | null;
  workPeriod: { start: FuzzyDate; end: FuzzyDate };
  evidence: Partial<Record<EvidenceKey, Evidence[]>>;
  score: ScoreBreakdown | null;
  analysis: AnalysisView;
  risks: RiskSignal[];
  profitability: Profitability | null;
  draft: Draft | null;
  memo: string;
  outcomes: Outcome[];
  related: RelatedRecord[];
  discoveryPaths: DiscoveryPathView[];
  activity: { at: IsoDateTime; text: string }[];
  feedback: UserFeedbackState;
  parserVersion: string | null;
}

// ── 소스 · 수집 ─────────────────────────────────────────────────────
export type PolicyStatus = 'permission_pending' | 'allowed' | 'restricted' | 'blocked' | 'not_required';
export type SourceHealth = 'ok' | 'degraded' | 'blocked' | 'error' | 'paused' | 'unknown';
export type Support = 'supported' | 'unsupported' | 'unverified';

export interface Coverage {
  /** 탐색 목표 (예: 전국). 실제 확인 범위와 별개 */
  targetLabel: string;
  /** 공개 지역 목록의 전체성 확인 여부 */
  regionListStatus: 'verified' | 'unverified' | 'not_applicable' | 'unknown';
  unitLabel: string;
  planned: number | null;
  completed: number;
  pending: number;
  blocked: number;
  failed: number;
  scanCycle: number | null;
  lastVisitedAt: IsoDateTime | null;
  nextUp: string | null;
  depthLimit: number | null;
  budget: { used: number; limit: number | null; unitLabel: string } | null;
  /** 원천 전체 모집단을 모르면 'unknown' */
  marketCoverage: 'unknown';
  /** 전국 목표 단위(17개 시·도) 중 지역 목록이 확보된 수 */
  targetUnitsTotal: number | null;
  targetUnitsCovered: number | null;
  estimatedCycleDays: number | null;
  notes: string[];
}

export interface SourceInfo {
  id: string;
  name: string;
  kind: 'site' | 'manual';
  scopeNote: string | null;
  adapterVersion: string;
  policy: {
    status: PolicyStatus;
    basis: string | null;
    reviewedAt: IsoDateTime | null;
    note: string | null;
    robotsStatus: string;
    robotsSummary: string | null;
  };
  autoCollect: { enabled: boolean; intervalMinutes: number | null };
  health: { status: SourceHealth; checkedAt: IsoDateTime | null; message: string | null; code: string | null };
  stoppedReason: string | null;
  capabilities: { key: string; label: string; support: Support; note: string | null }[];
  coverage: Coverage | null;
  /** 사이트 조사 완료 여부 — 미완료면 자동 탐색을 실행할 수 없다 */
  research: { ready: boolean; missing: string[] };
  lastRunId: string | null;
}

export type RunState = 'queued' | 'running' | 'paused' | 'succeeded' | 'partial' | 'failed' | 'cancelled';
export type RunKind =
  | 'discovery'
  | 'recheck_recent'
  | 'recheck_stale'
  | 'recheck_lead'
  | 'reanalyze_lead'
  | 'reanalyze_all'
  | 'import'
  | 'export';

export interface RunCounts {
  requests: number;
  detailsFetched: number;
  created: number;
  updated: number;
  duplicates: number;
  excluded: number;
  parseFailures: number;
  fetchFailures: number;
  policyStops: number;
  aiFailures: number;
}

export interface RunInfo {
  id: string;
  sourceId: string | null;
  leadId: string | null;
  kind: RunKind;
  state: RunState;
  trigger: 'manual' | 'schedule';
  startedAt: IsoDateTime | null;
  finishedAt: IsoDateTime | null;
  progress: { done: number; total: number | null; label: string | null };
  counts: RunCounts;
  error: { code: string; message: string; retryAfter: IsoDateTime | null } | null;
  note: string | null;
  scanCycle: number | null;
  result: Record<string, unknown>;
}

export interface JobAccepted {
  jobId: string;
  runId: string | null;
}

// ── 설정 · 지표 ─────────────────────────────────────────────────────
export interface QueryGroup {
  id: string;
  label: string;
  enabled: boolean;
  keywords: string[];
}

export interface SettingsView {
  profile: {
    services: string[];
    skills: string;
    excludedWork: string;
    minContract: number | null;
    targetHourly: number | null;
    weeklyHours: number | null;
    onsite: 'no' | 'first_meeting' | 'yes';
    allowShortTermEmployment: boolean;
  };
  recheck: { ttlHours: number };
  notifications: {
    newRecommended: boolean;
    meaningfulChange: boolean;
    sourceIssue: boolean;
    quietHours: { enabled: boolean; start: string; end: string };
  };
  ai: {
    enabled: boolean;
    externalTransferConsent: boolean;
    engineLabel: string | null;
    monthlyCostCap: number | null;
    dailyCostCap: number | null;
    keyConfigured: boolean;
    providerAvailable: boolean;
  };
  retention: { rawDays: number };
  queryGroups: { version: number; groups: QueryGroup[] };
}

export interface SalesSummary {
  periodDays: number;
  reviewed: number;
  contacted: number;
  negotiating: number;
  won: number;
  contractsConfirmed: number;
  /** 계약 확인 금액 합계 (수금 아님) */
  contractAmount: number | null;
  /** 실제 수금 기록 합계 (won 표시만으로 늘지 않음) */
  collected: number;
  refunded: number;
  directCost: number;
}

export interface Bootstrap {
  mode: 'demo' | 'live';
  appVersion: string;
  contractVersion: string | null;
  categories: { id: string; label: string }[];
  queues: { id: QueueId; label: string; description: string }[];
  recheckTtlHours: number;
  eventSeq: number;
}

// ── 목록 조회 ─────────────────────────────────────────────────────
export type RemoteFilter = 'any' | 'confirmed' | 'confirmed_or_inferred' | 'unknown' | 'onsite';
export type RecruitFilter = 'not_closed' | 'open_fresh' | 'recheck' | 'unknown' | 'closed' | 'access_issue' | 'any';
export type IntentFilter = 'any' | 'buyer' | 'hiring' | 'seller' | 'other';
export type SortKey = 'priority' | 'published' | 'checked';

export interface LeadFilter {
  keyword: string;
  sourceId: string | null;
  category: string | null;
  remote: RemoteFilter;
  recruit: RecruitFilter;
  intent: IntentFilter;
  sort: SortKey;
}

/**
 * 검토 대기열. 정의는 백엔드가 소유한다 (docs/INTEGRATION.md).
 * - recommended / needs_review / auto_excluded: 자동 판정별, 내가 제외하지 않았고 진행 전(new·reviewing)
 * - interested: 관심 표시 + 진행 전
 * - active: 연락함·협상 중·수주·보류
 * - dismissed: 내가 제외
 */
export type QueueId = 'recommended' | 'needs_review' | 'interested' | 'active' | 'dismissed' | 'auto_excluded' | 'all';

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
  hasMore: boolean;
}

export interface LeadPatch {
  userMark?: UserMark;
  salesStage?: SalesStage;
  memo?: string;
  draftText?: string;
}

export interface LeadFeedback {
  kind: 'remote' | 'real_request';
  value: 'confirmed' | 'denied' | 'yes' | 'no' | 'clear';
}

export interface OutcomeInput {
  kind: OutcomeKind;
  amount: number | null;
  occurredOn: string;
  note: string | null;
  evidenceRef: string | null;
}

export interface ExportInput {
  kind: 'leads_csv' | 'leads_json' | 'diagnostics';
}

export interface ManualImportInput {
  format: 'text' | 'csv' | 'json';
  content: string;
  fileName: string | null;
  originalUrl: string | null;
}

// ── 이벤트 ───────────────────────────────────────────────────────
interface EventBase {
  id: string;
  seq: number;
  at: IsoDateTime;
}

export type WorkleadEvent =
  | (EventBase & { type: 'run.progress'; runId: string; sourceId: string | null; progress: RunInfo['progress'] })
  | (EventBase & { type: 'run.state_changed'; runId: string; sourceId: string | null; state: RunState })
  | (EventBase & { type: 'lead.created'; leadId: string })
  | (EventBase & { type: 'lead.updated'; leadId: string })
  | (EventBase & { type: 'source.health_changed'; sourceId: string; health: SourceHealth })
  | (EventBase & { type: 'analysis.completed'; leadId: string; status: AnalysisStatus })
  | (EventBase & {
      type: 'notification.created';
      kind: 'new_lead' | 'lead_changed' | 'source_issue';
      title: string;
      leadId: string | null;
      sourceId: string | null;
      /** 조용한 시간 등으로 네이티브 알림을 억제한 이유 */
      suppressedReason: string | null;
    })
  /** 이어받기 보존 범위를 벗어났을 때: 전체 재조회 필요 */
  | (EventBase & { type: 'stream.reset' });

export type StreamStatus = 'connecting' | 'open' | 'retrying' | 'closed';
