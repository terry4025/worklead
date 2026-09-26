/**
 * 데모 전용 합성 데이터. 실제 게시글·수집 결과가 아니다.
 * 원문 URL 을 만들지 않는다 (추측한 실제 주소로 연결되지 않도록 originalUrl = null).
 */
import type {
  ApplicantScope,
  CollaborationMode,
  DemandIntent,
  EngagementType,
  Evidence,
  EvidenceBasis,
  EvidenceKey,
  FuzzyDate,
  LeadDetail,
  Outcome,
  Pay,
  Profitability,
  Reason,
  Recommendation,
  RelatedRecord,
  RiskSignal,
  SalesStage,
  ScoreFactor,
  SourceStatus,
  AccessStatus,
  AnalysisStatus,
  AnalysisView,
  UserMark,
  WorkMode,
  Draft,
} from '../../domain/model';

export const DEMO_CATEGORIES = [
  { id: 'website', label: '웹사이트' },
  { id: 'landing', label: '랜딩페이지' },
  { id: 'shop', label: '쇼핑몰' },
  { id: 'fullstack', label: '풀스택·웹 서비스' },
  { id: 'software', label: '프로그램·앱' },
  { id: 'vba', label: '엑셀·VBA·매크로' },
  { id: 'automation', label: '업무 자동화·연동' },
  { id: 'data', label: '데이터 정리' },
  { id: 'other', label: '기타' },
];

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

type EvidenceSpec = [quote: string, basis: EvidenceBasis, note?: string];

interface Judged<T> {
  value: T;
  basis: EvidenceBasis | null;
  ev?: EvidenceSpec[];
}

interface LeadSpec {
  id: string;
  sourceId?: string;
  title: string;
  body: (d: DateText) => string;
  categories: string[];
  region: string | null;
  workplace?: string | null;
  applicantRegion?: string | null;
  intent: Judged<DemandIntent>;
  engagement: Judged<EngagementType>;
  work: Judged<WorkMode>;
  collab: Judged<CollaborationMode>;
  scope: Judged<ApplicantScope>;
  pay: Partial<Pay> & { ev?: EvidenceSpec[] };
  status: SourceStatus;
  statusEv?: EvidenceSpec[];
  access?: AccessStatus;
  analysisStatus?: AnalysisStatus;
  checkedAgoH: number | null;
  publishedAgoH: number | null;
  publishedPrecision?: FuzzyDate['precision'];
  publishedRaw?: string | null;
  firstSeenAgoH: number;
  deadlineInDays?: number | null;
  deadlineEv?: EvidenceSpec[];
  workStart?: { inDays: number | null; precision: FuzzyDate['precision']; raw: string | null };
  recommendation: Recommendation;
  factors: [work: number | null, intent: number | null, remote: number | null, fresh: number | null, clarity: number | null];
  factorReasons: [string, string, string, string, string];
  riskPenalty?: { score: number; reasons: string[] };
  reasons: Reason[];
  analysis?: Partial<AnalysisView>;
  risks?: Omit<RiskSignal, 'span'>[];
  profit?: Profitability;
  mark?: UserMark;
  stage?: SalesStage;
  memo?: string;
  draft?: Omit<Draft, 'generatedAt'> & { generatedAgoH: number | null };
  outcomes?: (Omit<Outcome, 'recordedAt' | 'occurredOn' | 'currency'> & { daysAgo: number })[];
  related?: (Omit<RelatedRecord, 'seenAt'> & { seenAgoH: number })[];
  foundIn?: string[];
  contactChannel?: string | null;
}

/** 본문에 들어가는 날짜 문구를 데모 기준 시각에서 만든다 */
export interface DateText {
  md(daysFromNow: number): string;
}

const ruleVersion = 'rules-demo-0.1';

function iso(now: number, offsetMs: number): string {
  return new Date(now + offsetMs).toISOString();
}

function kstMonthDay(ms: number): string {
  const f = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric' });
  const parts = f.formatToParts(new Date(ms));
  const m = parts.find((p) => p.type === 'month')?.value ?? '';
  const d = parts.find((p) => p.type === 'day')?.value ?? '';
  return `${m}월 ${d}일`;
}

/** KST 기준 해당 날짜 자정(UTC ISO) */
function kstDayIso(ms: number): string {
  const ymd = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(
    new Date(ms),
  );
  return new Date(`${ymd}T00:00:00+09:00`).toISOString();
}

function kstYmd(ms: number): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(
    new Date(ms),
  );
}

const noDate: FuzzyDate = { at: null, precision: 'unknown', raw: null };

const factorMeta: { key: string; label: string; max: number }[] = [
  { key: 'work_fit', label: '업무 적합성', max: 30 },
  { key: 'buyer_intent', label: '구매 의도', max: 25 },
  { key: 'remote_fit', label: '원격 적합성', max: 20 },
  { key: 'freshness', label: '최신성', max: 15 },
  { key: 'scope_clarity', label: '범위 명확성', max: 10 },
];

const baseAnalysis: AnalysisView = {
  status: 'complete',
  summary: null,
  summaryEngine: 'ai',
  fit: [],
  unfit: [],
  uncertain: [],
  deliverables: [],
  techRequirements: [],
  questions: [],
  nextAction: null,
  conversionOpportunity: null,
  engine: '규칙 + AI (데모)',
  version: 'analysis-demo-0.1',
  analyzedAt: null,
  failure: null,
};

function buildLead(spec: LeadSpec, now: number): LeadDetail {
  const dt: DateText = { md: (days) => kstMonthDay(now + days * DAY) };
  const bodyText = spec.body(dt);
  const sourceRecordId = `rec-${spec.id}`;
  let evSeq = 0;

  const toEvidence = (specs: EvidenceSpec[] | undefined): Evidence[] =>
    (specs ?? []).map(([quote, basis, note]) => {
      const start = bodyText.indexOf(quote);
      const inTitle = start < 0 && spec.title.includes(quote);
      if (start < 0 && !inTitle) {
        throw new Error(`데모 근거 인용이 본문에 없습니다: ${spec.id} "${quote}"`);
      }
      evSeq += 1;
      return {
        id: `${spec.id}-ev${evSeq}`,
        quote,
        span: start >= 0 ? { start, end: start + quote.length } : null,
        basis,
        confidence: basis === 'explicit' ? 'high' : basis === 'inferred' ? 'medium' : null,
        observedAt: iso(now, -(spec.checkedAgoH ?? spec.firstSeenAgoH) * HOUR),
        sourceRecordId,
        version: ruleVersion,
        note: note ?? (inTitle ? '제목에서 확인' : null),
      };
    });

  const evidence: LeadDetail['evidence'] = {};
  const put = (key: EvidenceKey, specs: EvidenceSpec[] | undefined) => {
    const list = toEvidence(specs);
    if (list.length) evidence[key] = list;
  };
  put('demandIntent', spec.intent.ev);
  put('engagementType', spec.engagement.ev);
  put('workMode', spec.work.ev);
  put('collaborationMode', spec.collab.ev);
  put('applicantScope', spec.scope.ev);
  put('pay', spec.pay.ev);
  put('sourceStatus', spec.statusEv);
  put('deadline', spec.deadlineEv);

  const factors: ScoreFactor[] = factorMeta.map((m, i) => ({
    ...m,
    score: spec.factors[i] ?? null,
    reason: spec.factorReasons[i] ?? '',
  }));
  const known = factors.filter((f) => f.score !== null);
  const penalty = spec.riskPenalty ?? { score: 0, reasons: [] };
  const total = known.length ? Math.max(0, known.reduce((a, f) => a + (f.score ?? 0), 0) - penalty.score) : null;
  const unknownFactors = factors.length - known.length;

  const pay: Pay = {
    raw: spec.pay.raw ?? null,
    currency: spec.pay.currency ?? 'KRW',
    min: spec.pay.min ?? null,
    max: spec.pay.max ?? null,
    unit: spec.pay.unit ?? 'unknown',
    negotiable: spec.pay.negotiable ?? false,
  };

  const risks: RiskSignal[] = (spec.risks ?? []).map((r) => {
    const start = r.quote ? bodyText.indexOf(r.quote) : -1;
    return { ...r, span: start >= 0 && r.quote ? { start, end: start + r.quote.length } : null };
  });

  const analysisStatus = spec.analysisStatus ?? 'complete';
  const lastCheckedAt = spec.checkedAgoH === null ? null : iso(now, -spec.checkedAgoH * HOUR);

  return {
    id: spec.id,
    sourceId: spec.sourceId ?? 'daangn-alba',
    title: spec.title,
    categories: spec.categories,
    demandIntent: { value: spec.intent.value, basis: spec.intent.basis },
    engagementType: { value: spec.engagement.value, basis: spec.engagement.basis },
    workMode: { value: spec.work.value, basis: spec.work.basis },
    collaborationMode: { value: spec.collab.value, basis: spec.collab.basis },
    applicantScope: { value: spec.scope.value, basis: spec.scope.basis },
    pay,
    sourceStatus: spec.status,
    accessStatus: spec.access ?? 'accessible',
    analysisStatus,
    recheckDue: false,
    lastCheckedAt,
    published:
      spec.publishedAgoH === null
        ? { at: null, precision: 'unknown', raw: spec.publishedRaw ?? null }
        : {
            at:
              spec.publishedPrecision === 'day' || spec.publishedPrecision === 'approximate'
                ? kstDayIso(now - spec.publishedAgoH * HOUR)
                : iso(now, -spec.publishedAgoH * HOUR),
            precision: spec.publishedPrecision ?? 'exact',
            raw: spec.publishedRaw ?? null,
          },
    firstSeenAt: iso(now, -spec.firstSeenAgoH * HOUR),
    deadline:
      spec.deadlineInDays === undefined || spec.deadlineInDays === null
        ? noDate
        : { at: kstDayIso(now + spec.deadlineInDays * DAY), precision: 'day', raw: `${dt.md(spec.deadlineInDays)}까지` },
    postedRegion: spec.region,
    foundIn: spec.foundIn ?? (spec.region ? [spec.region] : []),
    recommendation: spec.recommendation,
    priority: { total, unknownFactors },
    reasons: spec.reasons,
    userMark: spec.mark ?? null,
    salesStage: spec.stage ?? 'new',
    hasMemo: Boolean(spec.memo),
    activeJob: null,

    bodyText,
    bodyRetainedUntil: iso(now, 30 * DAY),
    originalUrl: null,
    contactChannel: spec.contactChannel ?? (spec.sourceId === 'manual' ? null : '당근알바 채팅 (원문에서 직접)'),
    workplace: spec.workplace ?? null,
    applicantRegion: spec.applicantRegion ?? null,
    sourceUpdated: noDate,
    lastSeenAt: lastCheckedAt,
    workPeriod: {
      start: spec.workStart
        ? {
            at: spec.workStart.inDays === null ? null : kstDayIso(now + spec.workStart.inDays * DAY),
            precision: spec.workStart.precision,
            raw: spec.workStart.raw,
          }
        : noDate,
      end: noDate,
    },
    evidence,
    score: { total, factors, riskPenalty: penalty, ruleVersion },
    analysis: {
      ...baseAnalysis,
      status: analysisStatus,
      analyzedAt: iso(now, -(spec.checkedAgoH ?? spec.firstSeenAgoH) * HOUR),
      ...spec.analysis,
    },
    risks,
    profitability: spec.profit ?? {
      status: 'not_calculated',
      basis: null,
      reason: pay.min === null && pay.max === null ? '예산 미기재 — 예상 기여액을 계산하지 않았습니다.' : '계산 근거가 부족합니다.',
      assumptions: [],
      scenarios: [],
      revenueType: 'unknown',
      targetHourly: 40_000,
    },
    draft: spec.draft
      ? {
          text: spec.draft.text,
          editedByUser: spec.draft.editedByUser,
          generatedAt: spec.draft.generatedAgoH === null ? null : iso(now, -spec.draft.generatedAgoH * HOUR),
        }
      : null,
    memo: spec.memo ?? '',
    outcomes: (spec.outcomes ?? []).map(({ daysAgo, ...o }) => ({
      ...o,
      currency: 'KRW',
      occurredOn: kstYmd(now - daysAgo * DAY),
      recordedAt: iso(now, -daysAgo * DAY),
    })),
    related: (spec.related ?? []).map(({ seenAgoH, ...r }) => ({ ...r, seenAt: iso(now, -seenAgoH * HOUR) })),
    discoveryPaths: (spec.foundIn ?? (spec.region ? [spec.region] : [])).map((label) => ({
      regionScope: label,
      regionLabel: label,
      queryGroup: 'direct_build',
      firstSeenAt: iso(now, -spec.firstSeenAgoH * HOUR),
      lastSeenAt: lastCheckedAt ?? iso(now, -spec.firstSeenAgoH * HOUR),
      timesSeen: 1,
    })),
    parserVersion: 'demo-ui',
    activity: [
      { at: iso(now, -spec.firstSeenAgoH * HOUR), text: spec.sourceId === 'manual' ? '수동 입력으로 추가' : '신규 탐색에서 발견' },
      ...(lastCheckedAt ? [{ at: lastCheckedAt, text: '모집 상태 확인' }] : []),
    ],
    feedback: { remote: null, realRequest: null },
  };
}

function scenarios(
  target: number,
  rows: [key: 'conservative' | 'base' | 'optimistic', value: number, cost: number, hours: number][],
): Profitability['scenarios'] {
  return rows.map(([key, value, cost, hours]) => {
    const contribution = value - cost;
    return {
      key,
      contractValue: value,
      directCost: cost,
      hours,
      contribution,
      effectiveHourly: hours > 0 ? Math.round(contribution / hours) : null,
      residualAfterTarget: contribution - hours * target,
    };
  });
}

const TARGET = 40_000;

const specs: LeadSpec[] = [
  {
    id: 'L-1001',
    title: '스마트스토어 주문 엑셀 정리 매크로(VBA) 만들어주실 분',
    body: (d) =>
      `스마트스토어에서 내려받는 주문 엑셀을 매일 손으로 정리하고 있습니다.
주문 파일을 넣으면 택배 송장 양식과 일별 매출 시트로 자동 정리되는 매크로를 만들어주실 분 구합니다.

- 엑셀 2019 사용 중, 파일 샘플 제공 가능
- 작업은 전 과정 원격으로 진행합니다. 자료는 메일과 카톡으로 드려요.
- 지역 상관없이 지원 가능합니다.
- 예산: 50~80만원 (범위 확인 후 조정 가능)
- ${d.md(7)}까지 지원 받습니다.

비슷한 작업 경험이 있으시면 간단히 알려주세요.`,
    categories: ['vba', 'automation'],
    region: '경기 성남시 분당구',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['매크로를 만들어주실 분 구합니다', 'explicit']] },
    engagement: { value: 'project', basis: 'inferred', ev: [['예산: 50~80만원', 'inferred', '건 단위 예산 기재로 프로젝트 계약 추정']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['작업은 전 과정 원격으로 진행합니다.', 'explicit']] },
    collab: { value: 'online_only', basis: 'explicit', ev: [['자료는 메일과 카톡으로 드려요.', 'explicit']] },
    scope: { value: 'nationwide', basis: 'explicit', ev: [['지역 상관없이 지원 가능합니다.', 'explicit']] },
    pay: {
      raw: '50~80만원 (범위 확인 후 조정 가능)',
      min: 500_000,
      max: 800_000,
      unit: 'project',
      negotiable: true,
      ev: [['예산: 50~80만원 (범위 확인 후 조정 가능)', 'explicit']],
    },
    status: 'open',
    statusEv: [['지원 받습니다', 'explicit', '마감일 이전이며 마감 표시 없음 (최근 확인)']],
    checkedAgoH: 2,
    publishedAgoH: 26,
    firstSeenAgoH: 20,
    deadlineInDays: 7,
    deadlineEv: [['지원 받습니다', 'explicit']],
    recommendation: 'recommended',
    factors: [27, 23, 19, 12, 7],
    factorReasons: [
      '엑셀 VBA 자동화 — 제공 서비스와 일치',
      '"만들어주실 분 구합니다" 구매 표현 명시',
      '전 과정 원격 · 지역 무관 명시',
      '26시간 전 게시, 2시간 전 모집 확인',
      '입출력 형식은 명확, 송장 양식 수는 불명확',
    ],
    reasons: [
      { tone: 'positive', text: '구매 의뢰 명시' },
      { tone: 'positive', text: '전 과정 원격·지역 무관' },
      { tone: 'positive', text: '예산 50~80만원 기재' },
    ],
    analysis: {
      summary: '스마트스토어 주문 엑셀을 택배 송장 양식과 일별 매출 시트로 자동 정리하는 VBA 매크로 제작.',
      fit: ['엑셀·VBA 제공 서비스와 일치', '입력 파일 샘플 제공 가능'],
      uncertain: ['택배사 송장 양식 종류 수', '향후 양식 변경 시 유지보수 포함 여부'],
      deliverables: ['매크로 포함 엑셀 파일(.xlsm)', '사용 방법 안내'],
      techRequirements: ['Excel VBA', '스마트스토어 주문 파일 형식'],
      questions: ['송장 양식은 어느 택배사 기준인가요?', '하루 주문 건수와 파일 형식(xlsx/csv)은 어떻게 되나요?', '양식이 바뀔 때 수정도 포함하길 원하시나요?'],
      nextAction: '샘플 파일을 요청해 범위를 확정한 뒤 견적 제시',
    },
    profit: {
      status: 'calculated',
      basis: '게시 예산 50~80만원',
      reason: null,
      assumptions: ['투입 시간 = 요구 확인 + 개발 + 테스트·수정 + 소통 + 전달 후 지원', '확인된 직접 비용 없음', '목표 시간당 가치 40,000원 (내 설정)'],
      scenarios: scenarios(TARGET, [
        ['conservative', 500_000, 0, 23],
        ['base', 650_000, 0, 17],
        ['optimistic', 800_000, 0, 13],
      ]),
      revenueType: 'one_time',
      targetHourly: TARGET,
    },
  },
  {
    id: 'L-1002',
    title: '카페 브랜드 소개 랜딩페이지 제작 의뢰 (반응형 1페이지)',
    body: () =>
      `새로 여는 카페 브랜드 소개용 랜딩페이지 1페이지 제작을 의뢰합니다.
메뉴 소개, 매장 위치 지도, 인스타그램 연결, 예약 문의 폼 정도 필요합니다.

사진과 문구는 저희가 준비해 두었습니다.
미팅 없이 온라인으로만 진행하고 싶고, 재택 작업 가능합니다.
전국 어디서나 연락 주세요.

예산은 150만원 생각하고 있습니다. 도메인·호스팅 비용은 저희가 따로 부담합니다.
오픈 일정 때문에 이번 달 안에 시작하면 좋겠습니다.`,
    categories: ['landing', 'website'],
    region: '부산 수영구',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['제작을 의뢰합니다', 'explicit']] },
    engagement: { value: 'project', basis: 'explicit', ev: [['랜딩페이지 1페이지 제작을 의뢰합니다', 'explicit']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['재택 작업 가능합니다', 'explicit']] },
    collab: { value: 'online_only', basis: 'explicit', ev: [['미팅 없이 온라인으로만 진행하고 싶고', 'explicit']] },
    scope: { value: 'nationwide', basis: 'explicit', ev: [['전국 어디서나 연락 주세요.', 'explicit']] },
    pay: { raw: '150만원', min: 1_500_000, max: 1_500_000, unit: 'project', ev: [['예산은 150만원 생각하고 있습니다.', 'explicit']] },
    status: 'open',
    statusEv: [['제작을 의뢰합니다', 'inferred', '마감 표시 없음 + 5시간 전 목록 노출 확인']],
    checkedAgoH: 5,
    publishedAgoH: 30,
    firstSeenAgoH: 29,
    workStart: { inDays: null, precision: 'unknown', raw: '이번 달 안에 시작' },
    recommendation: 'recommended',
    factors: [25, 22, 20, 9, 8],
    factorReasons: [
      '랜딩페이지 제작 — 제공 서비스와 일치',
      '"제작을 의뢰합니다" 명시',
      '재택·온라인 전용·전국 명시',
      '30시간 전 게시',
      '필요 섹션 목록 명확',
    ],
    reasons: [
      { tone: 'positive', text: '미팅 없이 온라인 진행' },
      { tone: 'positive', text: '예산 150만원' },
      { tone: 'positive', text: '필요 기능 명확' },
    ],
    analysis: {
      summary: '카페 브랜드 소개용 반응형 랜딩페이지 1페이지 (메뉴·지도·인스타·예약 문의 폼).',
      fit: ['랜딩페이지 제공 서비스와 일치', '콘텐츠(사진·문구) 준비됨'],
      uncertain: ['예약 문의 폼의 수신 방식(메일/카톡)', '수정 횟수'],
      deliverables: ['반응형 랜딩페이지 1개', '문의 폼 연동', '배포 지원'],
      techRequirements: ['HTML/CSS 또는 정적 사이트', '지도 임베드', '폼 수신 연동'],
      questions: ['예약 문의는 어디로 받길 원하시나요?', '디자인 시안 수정은 몇 회를 생각하시나요?', '오픈 날짜가 정해져 있나요?'],
      nextAction: '폼 수신 방식과 오픈 일정 확인 후 일정 포함 견적 회신',
    },
    profit: {
      status: 'calculated',
      basis: '게시 예산 150만원 (도메인·호스팅은 의뢰자 부담)',
      reason: null,
      assumptions: ['투입 시간 = 요구 확인 + 디자인 적용 + 개발 + 수정 2회 + 배포', '확인된 직접 비용 없음', '목표 시간당 가치 40,000원 (내 설정)'],
      scenarios: scenarios(TARGET, [
        ['conservative', 1_500_000, 0, 30],
        ['base', 1_500_000, 0, 24],
        ['optimistic', 1_500_000, 0, 18],
      ]),
      revenueType: 'one_time',
      targetHourly: TARGET,
    },
    mark: 'interested',
    stage: 'reviewing',
    foundIn: ['부산 수영구', '부산 해운대구'],
    memo: '사진 퀄리티 좋음. 예약 폼은 네이버 예약 링크로 대체 가능한지 물어볼 것.',
    related: [
      {
        id: 'R-1002-b',
        sourceId: 'daangn-alba',
        title: '카페 브랜드 소개 랜딩페이지 제작 의뢰 (반응형 1페이지)',
        url: null,
        leadId: null,
        relation: 'duplicate',
        basis: '같은 공고가 다른 지역 검색에서 발견됨 (원천 ID 동일 — 발견 경로만 추가)',
        seenAgoH: 28,
      },
    ],
  },
  {
    id: 'L-1003',
    title: '구글 시트 주문 내역 → 카카오 알림톡 자동 발송 연동',
    body: () =>
      `구글 스프레드시트에 주문이 들어오면 고객에게 카카오 알림톡이 자동으로 나가도록 연동해 주실 분을 찾습니다.
현재는 직원이 하나씩 복사해서 보내고 있어요.

알림톡 발송 대행사 계정은 이미 있습니다(API 키 보유).
작업은 원격으로 진행하고, 화면 공유로 설명드릴 수 있습니다.
금액은 작업 범위 보고 협의하고 싶습니다. 견적 부탁드립니다.`,
    categories: ['automation'],
    region: '서울 송파구',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['연동해 주실 분을 찾습니다', 'explicit']] },
    engagement: { value: 'project', basis: 'inferred', ev: [['견적 부탁드립니다', 'inferred']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['작업은 원격으로 진행하고', 'explicit']] },
    collab: { value: 'online_only', basis: 'inferred', ev: [['화면 공유로 설명드릴 수 있습니다', 'inferred']] },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '작업 범위 보고 협의', unit: 'negotiable', negotiable: true, ev: [['금액은 작업 범위 보고 협의하고 싶습니다.', 'explicit']] },
    status: 'open',
    checkedAgoH: 1,
    publishedAgoH: 8,
    firstSeenAgoH: 7,
    recommendation: 'recommended',
    factors: [27, 22, 17, 14, 6],
    factorReasons: [
      'API 연동 자동화 — 제공 서비스와 일치',
      '"연동해 주실 분을 찾습니다" 명시',
      '원격 진행 명시, 진행 방식은 추정',
      '8시간 전 게시, 1시간 전 확인',
      '연동 대상은 명확, 알림 조건·건수 불명확',
    ],
    reasons: [
      { tone: 'positive', text: '구매 의뢰 명시' },
      { tone: 'positive', text: '원격 진행 명시' },
      { tone: 'caution', text: '금액 협의 — 견적 필요' },
    ],
    analysis: {
      summary: '구글 시트 주문 행 추가 시 카카오 알림톡 자동 발송 (기존 대행사 API 사용).',
      fit: ['API 연동 자동화', '의뢰자가 API 키 보유'],
      uncertain: ['발송 대행사 종류와 API 문서', '월 발송량', '실패 재시도 요구'],
      deliverables: ['Apps Script 또는 서버 함수', '설정 방법 안내'],
      techRequirements: ['Google Apps Script', '알림톡 대행사 API'],
      questions: ['어느 발송 대행사를 쓰시나요?', '주문이 하루 몇 건 정도인가요?', '발송 실패 시 알림이 필요하신가요?'],
      nextAction: '대행사 API 문서 확인 요청 후 범위별 견적 제시',
    },
    profit: {
      status: 'hypothesis',
      basis: '제안 견적 가설 — 게시 예산 아님',
      reason: '예산이 기재되지 않아 예상 기여액은 계산하지 않았습니다. 아래는 예상 시간 × 목표 시간가치로 만든 조정 가능한 견적 가설입니다.',
      assumptions: ['예상 시간 12~20시간 (연동·테스트·안내 포함)', '목표 시간당 가치 40,000원 (내 설정)'],
      scenarios: [
        { key: 'conservative', contractValue: 800_000, directCost: null, hours: 20, contribution: null, effectiveHourly: null, residualAfterTarget: null },
        { key: 'base', contractValue: 640_000, directCost: null, hours: 16, contribution: null, effectiveHourly: null, residualAfterTarget: null },
        { key: 'optimistic', contractValue: 480_000, directCost: null, hours: 12, contribution: null, effectiveHourly: null, residualAfterTarget: null },
      ],
      revenueType: 'one_time',
      targetHourly: TARGET,
    },
  },
  {
    id: 'L-1004',
    title: '쇼핑몰 상세페이지 수정·기존 사이트 유지보수 (월 단위)',
    body: () =>
      `카페24로 운영 중인 쇼핑몰의 상세페이지 수정과 간단한 유지보수를 맡아주실 분 구합니다.
한 달에 5~8건 정도 수정 요청이 있고, 급한 건은 이틀 안에 처리해 주시면 됩니다.

완전 재택이며 소통은 슬랙으로 합니다.
월 30만원 고정, 3개월 후 재계약 여부 결정합니다.
전국 누구나 지원 가능.`,
    categories: ['shop', 'website'],
    region: '대구 중구',
    intent: { value: 'buyer_ongoing', basis: 'explicit', ev: [['유지보수를 맡아주실 분 구합니다', 'explicit']] },
    engagement: { value: 'project', basis: 'inferred', ev: [['월 30만원 고정, 3개월 후 재계약 여부 결정합니다.', 'inferred']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['완전 재택이며', 'explicit']] },
    collab: { value: 'online_only', basis: 'explicit', ev: [['소통은 슬랙으로 합니다.', 'explicit']] },
    scope: { value: 'nationwide', basis: 'explicit', ev: [['전국 누구나 지원 가능.', 'explicit']] },
    pay: { raw: '월 30만원 고정', min: 300_000, max: 300_000, unit: 'month', ev: [['월 30만원 고정', 'explicit']] },
    status: 'open',
    checkedAgoH: 9,
    publishedAgoH: 50,
    publishedPrecision: 'approximate',
    publishedRaw: '2일 전',
    firstSeenAgoH: 44,
    recommendation: 'recommended',
    factors: [22, 21, 20, 8, 7],
    factorReasons: ['쇼핑몰 유지보수 — 부분 일치', '지속 의뢰 명시', '완전 재택·전국 명시', '약 2일 전 게시', '월 요청 건수 명시'],
    reasons: [
      { tone: 'positive', text: '완전 재택·전국' },
      { tone: 'positive', text: '월 30만원 지속 의뢰' },
      { tone: 'caution', text: '반복 매출 — 월 투입 시간 확인' },
    ],
    analysis: {
      summary: '카페24 쇼핑몰 상세페이지 수정·유지보수, 월 5~8건, 월 30만원 고정.',
      fit: ['원격 유지보수', '지속 매출'],
      uncertain: ['건당 수정 규모', '긴급 대응 시간대'],
      deliverables: ['월간 수정 작업'],
      techRequirements: ['카페24', 'HTML/CSS'],
      questions: ['수정 1건의 평균 규모는 어느 정도인가요?', '긴급 요청은 주말에도 있나요?'],
      nextAction: '최근 수정 요청 예시 3건을 받아 월 투입 시간 추정',
    },
    profit: {
      status: 'not_calculated',
      basis: null,
      reason: '월 단위 보수입니다. 월 투입 시간이 확인되지 않아 프로젝트 매출이나 시간당 수익으로 환산하지 않았습니다.',
      assumptions: [],
      scenarios: [],
      revenueType: 'recurring',
      targetHourly: TARGET,
    },
  },
  {
    id: 'L-1005',
    title: '학원 수강생·출결 관리 웹 프로그램 개발 외주',
    body: () =>
      `수학학원에서 쓸 수강생 관리, 출결, 수납 내역 확인용 웹 프로그램 개발 외주 맡기려고 합니다.
강사 5명, 학생 약 200명 규모입니다.

개발은 재택으로 하셔도 되지만, 첫 미팅은 학원에서 대면으로 한 번 하고 싶습니다(강남역 인근).
이후 진행은 온라인으로 합니다.
예산은 300~500만원 사이로 생각하고 있으며 기능 범위에 따라 조정 가능합니다.`,
    categories: ['fullstack'],
    region: '서울 강남구',
    workplace: '첫 미팅: 강남역 인근 학원',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['개발 외주 맡기려고 합니다', 'explicit']] },
    engagement: { value: 'project', basis: 'explicit', ev: [['개발 외주 맡기려고 합니다', 'explicit']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['개발은 재택으로 하셔도 되지만', 'explicit']] },
    collab: {
      value: 'onsite_required',
      basis: 'explicit',
      ev: [['첫 미팅은 학원에서 대면으로 한 번 하고 싶습니다(강남역 인근).', 'explicit', '재택 개발이어도 대면 1회 필요']],
    },
    scope: { value: 'unknown', basis: null },
    pay: {
      raw: '300~500만원 (기능 범위에 따라 조정)',
      min: 3_000_000,
      max: 5_000_000,
      unit: 'project',
      negotiable: true,
      ev: [['예산은 300~500만원 사이로 생각하고 있으며 기능 범위에 따라 조정 가능합니다.', 'explicit']],
    },
    status: 'open',
    checkedAgoH: 3,
    publishedAgoH: 18,
    firstSeenAgoH: 16,
    recommendation: 'needs_review',
    factors: [28, 24, 12, 13, 7],
    factorReasons: ['웹 관리 프로그램 — 제공 서비스와 일치', '외주 명시', '재택 개발 명시, 대면 미팅 1회 필요', '18시간 전 게시', '기능 목록·규모 명시'],
    reasons: [
      { tone: 'caution', text: '첫 미팅 대면 1회 (강남)' },
      { tone: 'positive', text: '예산 300~500만원' },
      { tone: 'positive', text: '외주 명시' },
    ],
    analysis: {
      summary: '학원용 수강생·출결·수납 관리 웹 프로그램 (강사 5명, 학생 약 200명).',
      fit: ['풀스택 웹 서비스', '예산 범위 충분'],
      unfit: ['대면 미팅 1회 — 내 설정(방문 불가)과 충돌'],
      uncertain: ['수납 내역의 결제 연동 필요 여부', '호스팅·운영 주체'],
      deliverables: ['웹 관리자 화면', '강사·학생 계정', '출결·수납 조회'],
      techRequirements: ['웹 프런트·백엔드', 'DB', '권한 관리'],
      questions: ['첫 미팅을 화상으로 대체할 수 있을까요?', '수납은 기록만 하나요, 결제 연동도 필요한가요?', '운영 서버는 어느 쪽이 관리하나요?'],
      nextAction: '첫 미팅 화상 대체 가능 여부를 먼저 확인',
    },
    profit: {
      status: 'calculated',
      basis: '게시 예산 300~500만원',
      reason: null,
      assumptions: ['투입 시간에 대면 미팅 이동 3시간 포함', '호스팅 비용은 의뢰자 부담으로 가정 (미확인)', '목표 시간당 가치 40,000원 (내 설정)'],
      scenarios: scenarios(TARGET, [
        ['conservative', 3_000_000, 0, 100],
        ['base', 4_000_000, 0, 80],
        ['optimistic', 5_000_000, 0, 65],
      ]),
      revenueType: 'one_time',
      targetHourly: TARGET,
    },
  },
  {
    id: 'L-1006',
    title: '워드프레스 홈페이지 리뉴얼 해주실 분 구합니다',
    body: () =>
      `인테리어 업체 홈페이지(워드프레스)가 오래되어 리뉴얼하려고 합니다.
시공 사례 게시판과 견적 문의 폼이 필요해요.
기존 자료는 메일로 전달드릴 수 있습니다.
관심 있으신 분 연락 주세요.`,
    categories: ['website'],
    region: '광주 서구',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['리뉴얼 해주실 분 구합니다', 'explicit']] },
    engagement: { value: 'project', basis: 'inferred' },
    work: {
      value: 'fully_remote',
      basis: 'inferred',
      ev: [['기존 자료는 메일로 전달드릴 수 있습니다.', 'inferred', '재택 언급은 없음 — 자료 전달 방식으로만 추정']],
    },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: {},
    status: 'open',
    checkedAgoH: 20,
    publishedAgoH: 70,
    publishedPrecision: 'approximate',
    publishedRaw: '3일 전',
    firstSeenAgoH: 60,
    recommendation: 'needs_review',
    factors: [26, 20, 8, 7, 5],
    factorReasons: ['워드프레스 리뉴얼 — 일치', '구매 표현 명시', '재택 명시 없음, 메일 전달로 추정', '약 3일 전 게시', '필요 기능 일부만 명시'],
    reasons: [
      { tone: 'positive', text: '구매 의뢰' },
      { tone: 'caution', text: '재택 추정 — 명시 없음' },
      { tone: 'caution', text: '예산 미기재' },
    ],
    analysis: {
      summary: '인테리어 업체 워드프레스 홈페이지 리뉴얼 (시공 사례 게시판, 견적 문의 폼).',
      fit: ['웹사이트 리뉴얼'],
      uncertain: ['원격 진행 가능 여부', '예산', '기존 호스팅 접근 권한'],
      deliverables: ['리뉴얼 사이트', '게시판·폼'],
      techRequirements: ['WordPress'],
      questions: ['원격으로만 진행해도 괜찮을까요?', '생각하시는 예산 범위가 있으신가요?', '기존 호스팅 관리자 계정을 받을 수 있나요?'],
      nextAction: '원격 가능 여부와 예산 범위 먼저 확인',
    },
  },
  {
    id: 'L-1007',
    title: '엑셀 데이터 정리 단기 알바 (재택 가능)',
    body: () =>
      `거래처 목록 엑셀 파일 약 3,000행을 정리해 주실 분을 구합니다.
중복 제거, 주소 형식 통일, 담당자 연락처 분리 작업입니다.

재택 가능하며 파일로 주고받습니다.
시급 12,000원, 하루 4시간 정도로 1~2주 예상합니다.
엑셀 기본 함수 사용 가능하신 분.`,
    categories: ['data'],
    region: '인천 연수구',
    intent: { value: 'employee_hiring', basis: 'inferred', ev: [['시급 12,000원, 하루 4시간 정도로 1~2주 예상합니다.', 'inferred', '시급·근무시간 기재 — 단기 고용으로 판단']] },
    engagement: { value: 'part_time', basis: 'explicit', ev: [['시급 12,000원, 하루 4시간 정도로 1~2주 예상합니다.', 'explicit']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['재택 가능하며 파일로 주고받습니다.', 'explicit']] },
    collab: { value: 'online_only', basis: 'inferred', ev: [['파일로 주고받습니다', 'inferred']] },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '시급 12,000원', min: 12_000, max: 12_000, unit: 'hour', ev: [['시급 12,000원', 'explicit']] },
    status: 'open',
    checkedAgoH: 6,
    publishedAgoH: 12,
    firstSeenAgoH: 11,
    recommendation: 'needs_review',
    factors: [14, 8, 18, 13, 8],
    factorReasons: ['데이터 정리 — 개발 업무는 아님', '단기 고용 (외주 구매 아님)', '재택 명시', '12시간 전 게시', '작업 범위 명확'],
    reasons: [
      { tone: 'caution', text: '단기 고용 — 개발 외주 아님' },
      { tone: 'positive', text: '재택 명시' },
      { tone: 'caution', text: '자동화 제안 기회 (의뢰 아님)' },
    ],
    analysis: {
      summary: '거래처 엑셀 3,000행 정리 단기 알바 (시급 12,000원, 하루 4시간, 1~2주).',
      fit: ['재택 가능'],
      unfit: ['개발 외주가 아닌 시급 고용'],
      uncertain: ['매크로 납품 방식 수용 여부'],
      deliverables: ['정리된 엑셀 파일'],
      techRequirements: ['엑셀'],
      questions: ['정리 작업을 매크로로 자동화해 건 단위로 납품하는 방식도 괜찮으신가요?'],
      nextAction: '자동화 납품 제안 여부 판단',
      conversionOpportunity: '반복 정리 작업이라 정리 매크로 납품을 제안할 여지가 있습니다. 이미 존재하는 개발 의뢰로 집계하지 않습니다.',
    },
    profit: {
      status: 'not_calculated',
      basis: null,
      reason: '시급 고용입니다. 시급은 그대로 표시하고 프로젝트 매출로 환산하지 않았습니다.',
      assumptions: [],
      scenarios: [],
      revenueType: 'unknown',
      targetHourly: TARGET,
    },
  },
  {
    id: 'L-1008',
    title: '앱·웹 개발자 구합니다 (프리랜서)',
    body: () =>
      `스타트업에서 앱·웹 개발 가능한 프리랜서 구합니다.
React, Node.js 경험자 우대.
자세한 내용은 연락 주시면 설명드리겠습니다.`,
    categories: ['fullstack'],
    region: '대전 유성구',
    intent: { value: 'unknown', basis: null, ev: [['프리랜서 구합니다', 'inferred', '외주 의뢰인지 고용인지 구분할 근거 부족']] },
    engagement: { value: 'unknown', basis: null },
    work: { value: 'unknown', basis: null, ev: [['프리랜서 구합니다', 'inferred', '"프리랜서"만으로 완전 재택을 확정하지 않음']] },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: {},
    status: 'open',
    checkedAgoH: 74,
    publishedAgoH: 100,
    publishedPrecision: 'approximate',
    publishedRaw: '4일 전',
    firstSeenAgoH: 96,
    recommendation: 'needs_review',
    factors: [22, null, null, 5, 2],
    factorReasons: ['앱·웹 개발 — 일치', '근거 부족: 의뢰/채용 구분 불가', '근거 부족: 재택 언급 없음', '약 4일 전 게시', '업무 내용 거의 없음'],
    reasons: [
      { tone: 'caution', text: '의뢰·채용 구분 불명확' },
      { tone: 'caution', text: '재택 미확인' },
      { tone: 'caution', text: '3일 전 확인 — 재확인 필요' },
    ],
    analysis: {
      summary: '스타트업 앱·웹 개발 프리랜서 모집. 업무 범위·조건 미기재.',
      uncertain: ['외주 프로젝트인지 상주·고용인지', '재택 여부', '보수'],
      questions: ['프로젝트 단위 외주인가요, 기간제 고용인가요?', '원격 근무가 가능한가요?', '예상 기간과 예산이 있나요?'],
      nextAction: '재확인 후 조건 문의',
    },
  },
  {
    id: 'L-1009',
    title: '네일샵 예약 페이지 만들어주세요',
    body: () =>
      `네일샵 예약 페이지를 만들고 싶어요.
날짜랑 시간 선택하면 예약되고, 사장님한테 알림 가는 정도면 됩니다.`,
    categories: ['website'],
    region: '제주 제주시',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['예약 페이지 만들어주세요', 'explicit']] },
    engagement: { value: 'project', basis: 'inferred' },
    work: { value: 'unknown', basis: null },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: {},
    status: 'unknown',
    statusEv: [['네일샵 예약 페이지를 만들고 싶어요.', 'inferred', '마감 표시는 없지만 모집 상태 필드를 확인하지 못함 — 모집 중으로 확정하지 않음']],
    checkedAgoH: 4,
    publishedAgoH: null,
    publishedRaw: null,
    firstSeenAgoH: 4,
    recommendation: 'needs_review',
    factors: [25, 21, null, 8, 4],
    factorReasons: ['예약 페이지 — 일치', '"만들어주세요" 구매 표현', '근거 부족: 재택·온라인 언급 없음', '게시일 미확인, 4시간 전 최초 발견', '기능 1줄만 명시'],
    reasons: [
      { tone: 'positive', text: '구매 의뢰' },
      { tone: 'caution', text: '모집 상태 미확인' },
      { tone: 'caution', text: '재택 미확인 · 예산 미기재' },
    ],
    analysis: {
      summary: '네일샵 날짜·시간 선택 예약 페이지와 사장님 알림.',
      uncertain: ['원격 진행 가능 여부', '예산', '기존 예약 도구 사용 여부'],
      questions: ['원격으로 진행해도 될까요?', '네이버 예약 등 기존 서비스 대신 자체 페이지가 필요한 이유가 있나요?', '예산 범위가 있으신가요?'],
      nextAction: '모집 여부부터 원문에서 확인',
    },
  },
  {
    id: 'L-1010',
    title: '거래처 발주서 PDF → 엑셀 자동 변환 프로그램 제작',
    body: () =>
      `거래처에서 오는 PDF 발주서(양식 3종)를 엑셀로 자동 변환하는 프로그램이 필요합니다.
매주 100건 정도 들어옵니다.
제작해 주실 수 있는 분 견적 보내주세요.
윈도우 PC에서 돌아가면 됩니다.`,
    categories: ['software', 'automation'],
    region: '경남 창원시',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['제작해 주실 수 있는 분 견적 보내주세요.', 'explicit']] },
    engagement: { value: 'project', basis: 'explicit', ev: [['제작해 주실 수 있는 분 견적 보내주세요.', 'explicit']] },
    work: { value: 'unknown', basis: null },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: {},
    status: 'open',
    checkedAgoH: 2,
    publishedAgoH: 5,
    firstSeenAgoH: 4,
    analysisStatus: 'failed',
    recommendation: 'needs_review',
    factors: [27, 23, null, 14, 6],
    factorReasons: ['PDF→엑셀 변환 프로그램 — 일치', '견적 요청 명시', '근거 부족: 재택 언급 없음', '5시간 전 게시', '양식 수·건수 명시'],
    reasons: [
      { tone: 'positive', text: '구매 의뢰 · 견적 요청' },
      { tone: 'caution', text: '재택 미확인' },
      { tone: 'negative', text: 'AI 분석 실패 — 규칙 결과만' },
    ],
    analysis: {
      engine: '규칙 (AI 실패)',
      summary: null,
      fit: ['업무 자동화 프로그램 (규칙 판정)'],
      uncertain: ['원격 진행 가능 여부', '예산'],
      questions: ['원격으로 진행 가능한가요?', 'PDF 양식 샘플을 받을 수 있나요?'],
      failure: { code: 'ai_timeout', message: '외부 AI 응답 시간 초과 (60초). 같은 입력으로 자동 재요청하지 않았습니다.' },
    },
  },
  {
    id: 'L-1011',
    title: '치과 홈페이지 제작 의뢰 (기존 도메인 있음)',
    body: () =>
      `치과 홈페이지 새로 제작 의뢰드립니다. 기존 도메인은 있고 호스팅은 새로 알아보려 합니다.
진료 안내, 의료진 소개, 온라인 상담 신청 페이지 필요합니다.
원격 작업 가능. 예산 200만원.`,
    categories: ['website'],
    region: '울산 남구',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['새로 제작 의뢰드립니다', 'explicit']] },
    engagement: { value: 'project', basis: 'explicit', ev: [['새로 제작 의뢰드립니다', 'explicit']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['원격 작업 가능.', 'explicit']] },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '예산 200만원', min: 2_000_000, max: 2_000_000, unit: 'project', ev: [['예산 200만원', 'explicit']] },
    status: 'open',
    access: 'blocked',
    checkedAgoH: 50,
    publishedAgoH: 80,
    publishedPrecision: 'day',
    firstSeenAgoH: 76,
    recommendation: 'needs_review',
    factors: [26, 22, 17, 6, 7],
    factorReasons: ['홈페이지 제작 — 일치', '의뢰 명시', '원격 작업 명시', '약 2일 전 마지막 확인', '필요 페이지 명시'],
    reasons: [
      { tone: 'negative', text: '재확인 중 접근 차단(403)' },
      { tone: 'positive', text: '원격 · 예산 200만원' },
    ],
    analysis: {
      summary: '치과 홈페이지 신규 제작 (진료 안내, 의료진, 상담 신청).',
      fit: ['홈페이지 제작', '원격 명시'],
      uncertain: ['현재 모집 상태 (접근 차단으로 확인 불가)'],
      questions: ['아직 제작자를 찾고 계신가요?'],
      nextAction: '원문을 직접 열어 모집 여부 확인',
    },
  },
  {
    id: 'L-1012',
    title: '홈페이지 제작해 드립니다 ✔ 저렴한 가격 빠른 제작',
    body: () =>
      `홈페이지·쇼핑몰·랜딩페이지 제작해 드립니다.
10년 경력, 30만원부터 가능합니다.
포트폴리오 보시고 연락 주세요.`,
    categories: ['website'],
    region: '서울 마포구',
    intent: { value: 'seller_service', basis: 'explicit', ev: [['제작해 드립니다.', 'explicit', '제작 서비스를 판매하는 글']] },
    engagement: { value: 'unknown', basis: null },
    work: { value: 'unknown', basis: null },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '30만원부터 (판매 가격)', min: 300_000, max: null, unit: 'project', ev: [['30만원부터 가능합니다.', 'explicit', '의뢰 예산이 아니라 판매자가 제시한 가격']] },
    status: 'open',
    checkedAgoH: 10,
    publishedAgoH: 30,
    firstSeenAgoH: 28,
    recommendation: 'excluded',
    factors: [20, 0, null, 10, 3],
    factorReasons: ['웹 제작 관련', '판매자 홍보 — 구매 수요 없음', '해당 없음', '30시간 전 게시', '해당 없음'],
    reasons: [{ tone: 'negative', text: '판매자 홍보 — 구매 의뢰 아님' }],
    analysis: { summary: '웹 제작 서비스 판매 홍보 글.', unfit: ['구매 수요가 아닌 판매 글'] },
  },
  {
    id: 'L-1013',
    title: '온라인 쇼핑몰 CS·상품등록 직원 (주 5일 출근)',
    body: () =>
      `온라인 쇼핑몰 상품 등록과 고객 문의 응대 업무입니다.
근무지: 경기 김포시 사무실 (주 5일 출근)
월급 230만원, 4대보험.`,
    categories: ['other'],
    region: '경기 김포시',
    workplace: '경기 김포시 사무실',
    intent: { value: 'employee_hiring', basis: 'explicit', ev: [['월급 230만원, 4대보험.', 'explicit']] },
    engagement: { value: 'full_time', basis: 'explicit', ev: [['주 5일 출근', 'explicit']] },
    work: { value: 'onsite', basis: 'explicit', ev: [['주 5일 출근', 'explicit', '"온라인 쇼핑몰"이어도 출근 근무']] },
    collab: { value: 'onsite_required', basis: 'explicit', ev: [['근무지: 경기 김포시 사무실', 'explicit']] },
    scope: { value: 'regional_restriction', basis: 'inferred' },
    pay: { raw: '월급 230만원', min: 2_300_000, max: 2_300_000, unit: 'month', ev: [['월급 230만원', 'explicit']] },
    status: 'open',
    checkedAgoH: 7,
    publishedAgoH: 40,
    firstSeenAgoH: 38,
    recommendation: 'excluded',
    factors: [4, 3, 0, 10, 8],
    factorReasons: ['개발 업무 아님', '일반 채용', '출근 필수', '40시간 전 게시', '업무 명확'],
    reasons: [
      { tone: 'negative', text: '출근 필수 일반 채용' },
      { tone: 'negative', text: '개발 업무 아님' },
    ],
    analysis: { summary: '온라인 쇼핑몰 CS·상품등록 상근직 (김포 출근).', unfit: ['출근 필수', '개발 업무 아님'] },
  },
  {
    id: 'L-1014',
    title: '필라테스 센터 오픈 이벤트 랜딩페이지 제작',
    body: () =>
      `[마감] 필라테스 센터 오픈 이벤트용 랜딩페이지 제작 의뢰합니다.
재택 작업 가능, 예산 80만원.`,
    categories: ['landing'],
    region: '경기 고양시',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['랜딩페이지 제작 의뢰합니다', 'explicit']] },
    engagement: { value: 'project', basis: 'explicit' },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['재택 작업 가능', 'explicit']] },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '예산 80만원', min: 800_000, max: 800_000, unit: 'project', ev: [['예산 80만원', 'explicit']] },
    status: 'closed',
    statusEv: [['[마감]', 'explicit', '원문 마감 표시']],
    checkedAgoH: 22,
    publishedAgoH: 140,
    publishedPrecision: 'day',
    firstSeenAgoH: 130,
    recommendation: 'excluded',
    factors: [25, 23, 18, 2, 5],
    factorReasons: ['랜딩페이지 — 일치', '의뢰 명시', '재택 명시', '마감', '범위 일부'],
    reasons: [{ tone: 'negative', text: '모집 마감 (원문 표시)' }],
    analysis: { summary: '필라테스 센터 오픈 이벤트 랜딩페이지 (마감).' },
  },
  {
    id: 'L-1015',
    title: '고수익 재택 부업! 누구나 가능한 데이터 입력',
    body: () =>
      `하루 1시간, 월 300만원 이상 고수익 보장!
재택으로 누구나 가능합니다.
시작 전 교육 자료비 5만원 입금 후 안내드립니다.`,
    categories: ['data'],
    region: '서울 중구',
    intent: { value: 'employee_hiring', basis: 'inferred' },
    engagement: { value: 'unknown', basis: null },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['재택으로 누구나 가능합니다.', 'explicit']] },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '월 300만원 이상 (보장 표현)', min: 3_000_000, max: null, unit: 'month', ev: [['월 300만원 이상 고수익 보장!', 'explicit']] },
    status: 'open',
    checkedAgoH: 12,
    publishedAgoH: 20,
    firstSeenAgoH: 19,
    recommendation: 'excluded',
    factors: [2, 3, 18, 12, 1],
    factorReasons: ['개발 업무 아님', '구매 의뢰 아님', '재택 표현 있음', '20시간 전 게시', '업무 내용 불명확'],
    riskPenalty: { score: 30, reasons: ['구직자 선입금 요구', '과장된 고수익 보장 표현'] },
    reasons: [{ tone: 'negative', text: '위험 신호: 선입금 요구 · 고수익 보장' }],
    risks: [
      { id: 'risk-1', label: '구직자에게 선입금 요구', quote: '교육 자료비 5만원 입금' },
      { id: 'risk-2', label: '과장된 고수익 보장 표현', quote: '월 300만원 이상 고수익 보장' },
    ],
    analysis: {
      summary: '데이터 입력 부업 모집. 원문 근거가 있는 위험 신호 2건.',
      unfit: ['개발 업무 아님'],
      nextAction: '검토 불필요 — 작성자를 단정하지 않고 위험 신호만 표시',
    },
  },
  {
    id: 'L-1016',
    title: '사내 재고관리 프로그램(C#) 개발자 — 재택 정규직',
    body: () =>
      `제조업체 사내 재고관리 프로그램(C#, WinForms) 유지보수·개발 정규직 채용합니다.
완전 재택 근무, 월 1회 본사(충북 청주) 회의 참석.
연봉 4,200만원 협의.`,
    categories: ['software'],
    region: '충북 청주시',
    intent: { value: 'employee_hiring', basis: 'explicit', ev: [['정규직 채용합니다.', 'explicit']] },
    engagement: { value: 'full_time', basis: 'explicit', ev: [['정규직 채용합니다.', 'explicit']] },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['완전 재택 근무', 'explicit']] },
    collab: { value: 'onsite_required', basis: 'explicit', ev: [['월 1회 본사(충북 청주) 회의 참석.', 'explicit']] },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '연봉 4,200만원 협의', min: 42_000_000, max: 42_000_000, unit: 'unknown', negotiable: true, ev: [['연봉 4,200만원 협의', 'explicit', '연 단위 — 지급 단위 목록에 없어 단위 미확인으로 보존']] },
    status: 'open',
    checkedAgoH: 8,
    publishedAgoH: 45,
    firstSeenAgoH: 44,
    recommendation: 'excluded',
    factors: [24, 4, 16, 10, 8],
    factorReasons: ['C# 프로그램 — 일치', '정규직 채용 (외주 아님)', '재택 명시, 월 1회 대면', '45시간 전 게시', '업무 명확'],
    reasons: [
      { tone: 'negative', text: '정규직 채용 — 외주 아님' },
      { tone: 'caution', text: '재택이지만 월 1회 대면' },
    ],
    analysis: { summary: 'C# 재고관리 프로그램 정규직 (재택, 월 1회 청주 회의).', unfit: ['정규직 고용 — 외주 계약과 구분'] },
  },
  {
    id: 'L-1017',
    title: '소규모 법률사무소 홈페이지 제작',
    body: () =>
      `변호사 2인 사무소 홈페이지 제작 의뢰합니다.
업무분야 소개, 상담 예약 폼, 블로그형 칼럼 게시판이 필요합니다.
전 과정 비대면 진행 원합니다. 예산 250만원.`,
    categories: ['website'],
    region: '서울 서초구',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['홈페이지 제작 의뢰합니다.', 'explicit']] },
    engagement: { value: 'project', basis: 'explicit' },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['전 과정 비대면 진행 원합니다.', 'explicit']] },
    collab: { value: 'online_only', basis: 'explicit', ev: [['전 과정 비대면 진행 원합니다.', 'explicit']] },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '예산 250만원', min: 2_500_000, max: 2_500_000, unit: 'project', ev: [['예산 250만원', 'explicit']] },
    status: 'open',
    checkedAgoH: 14,
    publishedAgoH: 60,
    firstSeenAgoH: 58,
    recommendation: 'recommended',
    factors: [26, 23, 20, 8, 8],
    factorReasons: ['홈페이지 — 일치', '의뢰 명시', '비대면 명시', '60시간 전 게시', '필요 기능 명시'],
    reasons: [
      { tone: 'positive', text: '비대면 진행 명시' },
      { tone: 'positive', text: '예산 250만원' },
    ],
    analysis: {
      summary: '변호사 2인 사무소 홈페이지 (업무분야, 상담 예약 폼, 칼럼 게시판).',
      questions: ['칼럼은 직접 작성·게시하실 예정인가요?', '상담 예약은 날짜 선택이 필요한가요?'],
      nextAction: '회신 대기',
    },
    mark: 'interested',
    stage: 'contacted',
    memo: '당근 채팅으로 문의 보냄. 회신 대기.',
    draft: {
      text: `안녕하세요, 홈페이지 제작 의뢰 글 보고 연락드립니다.
업무분야 소개·상담 예약 폼·칼럼 게시판 구성으로 비대면 진행 가능합니다.

확인하고 싶은 점이 있습니다.
1) 칼럼은 직접 작성·게시하실 예정인가요?
2) 상담 예약에 날짜·시간 선택이 필요한가요?

답변 주시면 일정과 견적을 정리해 드리겠습니다.`,
      editedByUser: true,
      generatedAgoH: 40,
    },
  },
  {
    id: 'L-1018',
    title: '플라워샵 꽃 정기구독 신청 랜딩페이지',
    body: () =>
      `꽃 정기구독 서비스 신청을 받는 랜딩페이지를 만들고 싶습니다.
결제는 스마트스토어 링크로 연결할 예정이에요.
온라인으로만 진행 가능하신 분, 재택 환영합니다. 예산 100만원 내외.`,
    categories: ['landing'],
    region: '경기 수원시',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['랜딩페이지를 만들고 싶습니다.', 'inferred']] },
    engagement: { value: 'project', basis: 'inferred' },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['재택 환영합니다.', 'explicit']] },
    collab: { value: 'online_only', basis: 'explicit', ev: [['온라인으로만 진행 가능하신 분', 'explicit']] },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '100만원 내외', min: 1_000_000, max: 1_000_000, unit: 'project', negotiable: true, ev: [['예산 100만원 내외.', 'explicit']] },
    status: 'open',
    checkedAgoH: 30,
    publishedAgoH: 120,
    publishedPrecision: 'day',
    firstSeenAgoH: 118,
    recommendation: 'recommended',
    factors: [25, 20, 20, 5, 7],
    factorReasons: ['랜딩페이지 — 일치', '구매 표현 (추정)', '온라인·재택 명시', '5일 전 게시', '결제 연결 방식 명시'],
    reasons: [
      { tone: 'positive', text: '온라인 전용 · 재택 환영' },
      { tone: 'positive', text: '예산 100만원 내외' },
    ],
    stage: 'negotiating',
    memo: '견적 120만원 제시, 상대는 100만원 희망. 수정 횟수 줄이는 안으로 조율 중.',
  },
  {
    id: 'L-1019',
    title: '견적서·거래명세서 엑셀 자동 작성 VBA',
    body: () =>
      `견적서와 거래명세서를 엑셀로 자동 작성하는 VBA를 제작해 주실 분 구합니다.
품목 DB 시트에서 선택하면 양식이 채워지고 PDF로 저장되면 좋겠습니다.
재택 진행, 예산 60만원.`,
    categories: ['vba'],
    region: '경북 구미시',
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['VBA를 제작해 주실 분 구합니다.', 'explicit']] },
    engagement: { value: 'project', basis: 'explicit' },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['재택 진행', 'explicit']] },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '예산 60만원', min: 600_000, max: 600_000, unit: 'project', ev: [['예산 60만원', 'explicit']] },
    status: 'closed',
    statusEv: [['재택 진행', 'inferred', '원문 모집 마감 표시 확인 (수주 후 마감됨 — 영업 결과와 무관)']],
    checkedAgoH: 48,
    publishedAgoH: 260,
    publishedPrecision: 'day',
    firstSeenAgoH: 250,
    recommendation: 'recommended',
    factors: [28, 24, 19, 1, 8],
    factorReasons: ['VBA — 일치', '의뢰 명시', '재택 명시', '마감', '기능 명확'],
    reasons: [{ tone: 'positive', text: '수주 — 계약 확인 60만원, 수금 30만원' }],
    stage: 'won',
    memo: '착수금 30만원 입금 확인. 잔금은 납품 후.',
    outcomes: [
      { id: 'o-1', kind: 'contract_confirmed', amount: 600_000, note: '채팅으로 금액·범위 합의', evidenceRef: '채팅 캡처 (로컬 보관)', daysAgo: 6 },
      { id: 'o-2', kind: 'payment_received', amount: 300_000, note: '착수금', evidenceRef: '계좌 입금 내역', daysAgo: 5 },
    ],
  },
  {
    id: 'L-1020',
    title: '동호회 회원 관리 구글 시트 자동화 (지인 소개)',
    sourceId: 'manual',
    body: () =>
      `지인 소개로 받은 의뢰 메모.
테니스 동호회 회원 명단·회비 납부를 구글 시트로 관리 중인데, 월별 미납자 자동 표시와 안내 문자 목록 추출을 원함.
원격 진행 가능, 예산 40만원 언급.`,
    categories: ['automation', 'vba'],
    region: null,
    intent: { value: 'buyer_project', basis: 'explicit', ev: [['자동 표시와 안내 문자 목록 추출을 원함', 'explicit']] },
    engagement: { value: 'project', basis: 'inferred' },
    work: { value: 'fully_remote', basis: 'explicit', ev: [['원격 진행 가능', 'explicit']] },
    collab: { value: 'unknown', basis: null },
    scope: { value: 'unknown', basis: null },
    pay: { raw: '예산 40만원 언급', min: 400_000, max: 400_000, unit: 'project', ev: [['예산 40만원 언급', 'explicit']] },
    status: 'unknown',
    checkedAgoH: null,
    publishedAgoH: null,
    firstSeenAgoH: 26,
    recommendation: 'needs_review',
    factors: [26, 22, 18, null, 7],
    factorReasons: ['시트 자동화 — 일치', '원하는 기능 명시', '원격 명시', '근거 부족: 수동 입력이라 게시·모집 시각 없음', '기능 명확'],
    reasons: [
      { tone: 'positive', text: '원격 · 예산 40만원' },
      { tone: 'caution', text: '수동 입력 — 모집 상태 확인 불가' },
    ],
    contactChannel: '지인 (직접 연락)',
    analysis: {
      engine: '규칙 (데모)',
      summary: '동호회 회원·회비 구글 시트에 미납자 자동 표시와 안내 문자 목록 추출.',
      questions: ['회원 수와 회비 주기는 어떻게 되나요?'],
    },
    analysisStatus: 'rules_only',
  },
];

/** 신규 탐색 데모 실행이 끝날 때 추가되는 리드 */
export const reserveSpec: LeadSpec = {
  id: 'L-1101',
  title: '사내 행사 신청 페이지 + 참가자 엑셀 자동 집계',
  body: () =>
    `사내 체육대회 신청을 받는 간단한 웹 페이지와 참가자 명단을 엑셀로 자동 집계하는 기능이 필요합니다.
만들어주실 분 구합니다. 전 과정 원격 진행, 지역 무관.
예산 70만원.`,
  categories: ['website', 'automation'],
  region: '세종 세종시',
  intent: { value: 'buyer_project', basis: 'explicit', ev: [['만들어주실 분 구합니다.', 'explicit']] },
  engagement: { value: 'project', basis: 'inferred' },
  work: { value: 'fully_remote', basis: 'explicit', ev: [['전 과정 원격 진행', 'explicit']] },
  collab: { value: 'online_only', basis: 'explicit', ev: [['전 과정 원격 진행', 'explicit']] },
  scope: { value: 'nationwide', basis: 'explicit', ev: [['지역 무관.', 'explicit']] },
  pay: { raw: '예산 70만원', min: 700_000, max: 700_000, unit: 'project', ev: [['예산 70만원.', 'explicit']] },
  status: 'open',
  checkedAgoH: 0,
  publishedAgoH: 1,
  firstSeenAgoH: 0,
  recommendation: 'recommended',
  factors: [26, 23, 20, 15, 7],
  factorReasons: ['웹 페이지 + 엑셀 집계 — 일치', '구매 표현 명시', '원격·지역 무관 명시', '1시간 전 게시', '기능 명확'],
  reasons: [
    { tone: 'positive', text: '방금 발견 · 원격·지역 무관' },
    { tone: 'positive', text: '예산 70만원' },
  ],
  analysis: {
    summary: '사내 행사 신청 웹 페이지와 참가자 엑셀 자동 집계.',
    questions: ['예상 참가 인원은 몇 명인가요?', '신청 마감 후 명단 형식이 정해져 있나요?'],
    nextAction: '신청 인원·마감 일정 확인',
  },
};

export function buildDemoLeads(now: number): LeadDetail[] {
  return specs.map((s) => buildLead(s, now));
}

export function buildReserveLead(now: number): LeadDetail {
  return buildLead(reserveSpec, now);
}

/** 수동 입력 데모: 붙여넣은 텍스트로 리드를 만든다 (분석은 규칙 대기 상태) */
export function buildImportedLead(now: number, id: string, text: string, url: string | null): LeadDetail {
  const firstLine = text.split('\n').map((l) => l.trim()).find(Boolean) ?? '수동 입력 글';
  const lead = buildLead(
    {
      id,
      sourceId: 'manual',
      title: firstLine.slice(0, 80),
      body: () => text,
      categories: ['other'],
      region: null,
      intent: { value: 'unknown', basis: null },
      engagement: { value: 'unknown', basis: null },
      work: { value: 'unknown', basis: null },
      collab: { value: 'unknown', basis: null },
      scope: { value: 'unknown', basis: null },
      pay: {},
      status: 'unknown',
      checkedAgoH: null,
      publishedAgoH: null,
      firstSeenAgoH: 0,
      recommendation: 'needs_review',
      factors: [null, null, null, null, null],
      factorReasons: ['분석 대기', '분석 대기', '분석 대기', '분석 대기', '분석 대기'],
      reasons: [{ tone: 'caution', text: '수동 입력 — 분석 대기' }],
      analysisStatus: 'pending',
      contactChannel: null,
    },
    now,
  );
  lead.originalUrl = url;
  lead.analysis = { ...lead.analysis, engine: null, analyzedAt: null };
  return lead;
}
