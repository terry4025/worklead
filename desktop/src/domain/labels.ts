import type {
  AccessStatus,
  AnalysisStatus,
  ApplicantScope,
  CollaborationMode,
  DemandIntent,
  EngagementType,
  EvidenceBasis,
  OutcomeKind,
  PayUnit,
  PolicyStatus,
  Recommendation,
  RunKind,
  RunState,
  SalesStage,
  ScenarioKey,
  SourceHealth,
  SourceStatus,
  Support,
  WorkMode,
} from './model';

export const workModeLabel: Record<WorkMode, string> = {
  fully_remote: '완전 재택',
  hybrid: '일부 재택',
  onsite: '출근',
  negotiable: '협의',
  unknown: '미확인',
};

export const collaborationLabel: Record<CollaborationMode, string> = {
  online_only: '온라인 진행',
  onsite_required: '대면 필요',
  negotiable: '대면 여부 협의',
  unknown: '진행 방식 미확인',
};

export const applicantScopeLabel: Record<ApplicantScope, string> = {
  nationwide: '전국 지원 가능',
  regional_restriction: '지원 지역 제한',
  unknown: '지원 지역 미확인',
};

export const intentLabel: Record<DemandIntent, string> = {
  buyer_project: '구매 의뢰',
  buyer_ongoing: '구매 의뢰 (지속)',
  short_gig: '프리랜서·단기 작업',
  employee_hiring: '일반 채용',
  seller_service: '판매자 홍보',
  job_seeker: '구직 글',
  information: '정보성 글',
  unknown: '유형 미확인',
};

/** 목록 열에 쓰는 짧은 표기 */
export const intentShortLabel: Record<DemandIntent, string> = {
  buyer_project: '구매 의뢰',
  buyer_ongoing: '지속 의뢰',
  short_gig: '단기·프리',
  employee_hiring: '일반 채용',
  seller_service: '판매자 홍보',
  job_seeker: '구직 글',
  information: '정보성',
  unknown: '미확인',
};

export const engagementLabel: Record<EngagementType, string> = {
  project: '건별 프로젝트',
  hourly_contract: '시간제 계약',
  part_time: '파트타임 고용',
  full_time: '정규·상근 고용',
  unknown: '계약 형태 미확인',
};

export const sourceStatusLabel: Record<SourceStatus, string> = {
  open: '모집 중',
  closed: '마감',
  deleted: '삭제됨',
  unknown: '모집 미확인',
};

export const accessStatusLabel: Record<AccessStatus, string> = {
  accessible: '접근 가능',
  login_required: '로그인 필요',
  blocked: '접근 차단',
  error: '접근 오류',
};

export const analysisStatusLabel: Record<AnalysisStatus, string> = {
  pending: '분석 대기',
  rules_only: '규칙 분석만',
  complete: '분석 완료',
  failed: 'AI 분석 실패',
  stale: '재분석 필요',
};

export const salesStageLabel: Record<SalesStage, string> = {
  new: '새 리드',
  reviewing: '검토 중',
  contacted: '연락함',
  negotiating: '협상 중',
  won: '수주',
  lost: '무산',
  on_hold: '보류',
  ignored: '무시',
};

/** 진행 상태 선택지. 'ignored' 는 제외 버튼(userMark)으로 다룬다. */
export const selectableStages: SalesStage[] = ['new', 'reviewing', 'contacted', 'negotiating', 'won', 'lost', 'on_hold'];

export const recommendationLabel: Record<Recommendation, string> = {
  recommended: '추천',
  needs_review: '확인 필요',
  excluded: '자동 제외',
};

export const payUnitLabel: Record<PayUnit, string> = {
  project: '건당',
  hour: '시급',
  day: '일급',
  week: '주급',
  month: '월',
  negotiable: '협의',
  unknown: '단위 미확인',
};

export const basisLabel: Record<EvidenceBasis, string> = {
  explicit: '원문 명시',
  inferred: '추정',
  user_confirmed: '내가 확인',
};

export const policyLabel: Record<PolicyStatus, string> = {
  permission_pending: '권한 확인 대기',
  allowed: '자동 수집 허용',
  restricted: '제한적 허용',
  blocked: '자동 수집 불가',
  not_required: '해당 없음',
};

export const healthLabel: Record<SourceHealth, string> = {
  ok: '정상',
  degraded: '일부 오류',
  blocked: '접근 차단',
  error: '오류',
  paused: '정지됨',
  unknown: '확인 전',
};

export const supportLabel: Record<Support, string> = {
  supported: '지원',
  unsupported: '미지원',
  unverified: '미확인',
};

export const runStateLabel: Record<RunState, string> = {
  queued: '대기',
  running: '실행 중',
  paused: '일시정지',
  succeeded: '완료',
  partial: '부분 완료',
  failed: '실패',
  cancelled: '취소됨',
};

export const runKindLabel: Record<RunKind, string> = {
  discovery: '신규 탐색',
  recheck_recent: '최근 후보 재확인',
  recheck_stale: '오래된 후보 재확인',
  recheck_lead: '리드 재확인',
  reanalyze_lead: '리드 재분석',
  reanalyze_all: '전체 재분석',
  import: '수동 입력',
  export: '내보내기',
};

export const outcomeKindLabel: Record<OutcomeKind, string> = {
  contract_confirmed: '계약 확인',
  payment_received: '수금',
  refund: '환불',
  direct_cost: '직접 비용',
};

export const scenarioLabel: Record<ScenarioKey, string> = {
  conservative: '보수적',
  base: '기준',
  optimistic: '낙관적',
};
