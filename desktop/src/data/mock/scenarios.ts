/**
 * 데모 시나리오: 같은 합성 리드에 소스·실행 상태를 바꿔 화면 상태를 확인한다.
 * 실제 수집 결과가 아니다. 소스·실행 데이터 형태는 contracts/fixtures/scenarios/* 를 따른다.
 */
import type { LeadDetail, RunInfo, SourceInfo } from '../../domain/model';
import { buildDemoLeads } from './fixtures';

export type ScenarioId = 'normal' | 'partial' | 'blocked' | 'ai_failure' | 'empty' | 'first_run' | 'offline';

export const SCENARIOS: { id: ScenarioId; label: string; note: string }[] = [
  { id: 'normal', label: '정상', note: '최근 수집 완료 (데모 가정: 권한 확인됨)' },
  { id: 'partial', label: '부분 수집', note: '요청 제한(429)으로 일부 지역만 확인' },
  { id: 'blocked', label: '접근 차단', note: '403 응답으로 소스 정지' },
  { id: 'ai_failure', label: 'AI 실패', note: 'AI 분석 실패 — 규칙 결과만 표시' },
  { id: 'empty', label: '빈 결과', note: '수집은 정상, 조건에 맞는 글 0건' },
  { id: 'first_run', label: '첫 실행', note: '권한 확인·사이트 조사 전 (실제 현재 상태)' },
  { id: 'offline', label: '연결 실패', note: '로컬 서비스에 연결할 수 없음' },
];

const HOUR = 3_600_000;

export interface ScenarioData {
  leads: LeadDetail[];
  sources: SourceInfo[];
  runs: RunInfo[];
}

const COUNTS_ZERO = {
  requests: 0,
  detailsFetched: 0,
  created: 0,
  updated: 0,
  duplicates: 0,
  excluded: 0,
  parseFailures: 0,
  fetchFailures: 0,
  policyStops: 0,
  aiFailures: 0,
};

const iso = (now: number, offsetMs: number) => new Date(now + offsetMs).toISOString();

const CAPS_UNVERIFIED: SourceInfo['capabilities'] = [
  { key: 'nationwide_search', label: '전국 단일 검색', support: 'unverified', note: '전국 단일 검색 지원 여부 미확인' },
  { key: 'region_search', label: '지역별 검색', support: 'unverified', note: null },
  { key: 'remote_filter', label: '재택 전용 필터', support: 'unverified', note: null },
  { key: 'pagination', label: '페이지 이동', support: 'unverified', note: null },
  { key: 'detail', label: '상세 조회', support: 'unverified', note: null },
  { key: 'revalidate', label: '상태 재확인', support: 'unverified', note: null },
  { key: 'structured_data', label: '구조화 데이터', support: 'unverified', note: 'JobPosting JSON-LD 제공 여부 미확인' },
];

const CAPS_DEMO: SourceInfo['capabilities'] = CAPS_UNVERIFIED.map((c) =>
  c.key === 'nationwide_search' || c.key === 'remote_filter' ? c : { ...c, support: 'supported', note: '데모 가정' },
);

function manualSource(): SourceInfo {
  return {
    id: 'manual',
    name: '수동 입력',
    kind: 'manual',
    scopeNote: '직접 확보한 글을 붙여넣거나 CSV·JSON 파일로 입력',
    adapterVersion: '0.1.0',
    policy: { status: 'not_required', basis: null, reviewedAt: null, note: null, robotsStatus: 'unchecked', robotsSummary: null },
    autoCollect: { enabled: false, intervalMinutes: null },
    health: { status: 'ok', checkedAt: null, message: null, code: null },
    stoppedReason: null,
    capabilities: [
      { key: 'import_text', label: '텍스트 붙여넣기', support: 'supported', note: null },
      { key: 'import_csv', label: 'CSV 가져오기', support: 'supported', note: '최대 1000행, 2MB' },
      { key: 'import_json', label: 'JSON 가져오기', support: 'supported', note: null },
      { key: 'revalidate', label: '상태 재확인', support: 'unsupported', note: '원문을 대신 내려받지 않습니다' },
    ],
    coverage: null,
    research: { ready: true, missing: [] },
    lastRunId: null,
  };
}

function daangn(now: number, scenario: ScenarioId): SourceInfo {
  const base: SourceInfo = {
    id: 'daangn-alba',
    name: '당근알바',
    kind: 'site',
    scopeNote: '허용된 공개 구인글만 대상 · 중고거래·동네생활·비즈프로필·채팅은 범위 밖',
    adapterVersion: '0.1.0',
    policy: {
      status: 'allowed',
      basis: '데모 가정 — 실제 허용 여부는 SOURCE_RESEARCH.md 확인 전',
      reviewedAt: iso(now, -72 * HOUR),
      note: null,
      robotsStatus: 'allowed',
      robotsSummary: '데모 가정',
    },
    autoCollect: { enabled: true, intervalMinutes: 360 },
    health: { status: 'ok', checkedAt: iso(now, -2 * HOUR), message: null, code: null },
    stoppedReason: null,
    capabilities: CAPS_DEMO,
    coverage: {
      targetLabel: '전국',
      regionListStatus: 'unverified',
      unitLabel: '지역×검색어',
      planned: 51,
      completed: 51,
      pending: 0,
      blocked: 0,
      failed: 0,
      scanCycle: 4,
      lastVisitedAt: iso(now, -2 * HOUR),
      nextUp: '강원 춘천시 · 홈페이지 웹사이트 랜딩페이지',
      depthLimit: 2,
      budget: { used: 212, limit: 300, unitLabel: '요청/일' },
      marketCoverage: 'unknown',
      targetUnitsTotal: 17,
      targetUnitsCovered: 17,
      estimatedCycleDays: 0.5,
      notes: ['지역 목록의 전체성이 확인되지 않아 부분 탐색으로 표시합니다'],
    },
    research: { ready: true, missing: [] },
    lastRunId: 'run-demo-1',
  };
  if (scenario === 'partial' && base.coverage) {
    base.health = { status: 'degraded', checkedAt: iso(now, -1 * HOUR), message: '요청 제한 응답(429) — Retry-After 까지 유예합니다', code: 'rate_limited' };
    base.coverage = { ...base.coverage, completed: 34, pending: 14, failed: 3, estimatedCycleDays: 1.2, nextUp: '경북 포항시 · 홈페이지 웹사이트 랜딩페이지' };
  }
  if (scenario === 'blocked' && base.coverage) {
    base.health = { status: 'blocked', checkedAt: iso(now, -30 * 60_000), message: '접근 차단 응답(403) — 소스를 정지합니다. 우회하지 않습니다.', code: 'http_403' };
    base.stoppedReason = 'http_403: 접근 차단 응답(403) — 소스를 정지합니다. 우회하지 않습니다.';
    base.coverage = { ...base.coverage, completed: 12, pending: 38, blocked: 1 };
  }
  if (scenario === 'first_run') {
    base.policy = { status: 'permission_pending', basis: null, reviewedAt: null, note: null, robotsStatus: 'unchecked', robotsSummary: null };
    base.autoCollect = { enabled: false, intervalMinutes: 360 };
    base.health = { status: 'unknown', checkedAt: null, message: '조사 미완료: 검색 URL 형식, 공개 지역 목록, 상세 링크 형식, 상세 해석 방식', code: 'research_incomplete' };
    base.capabilities = CAPS_UNVERIFIED;
    base.research = { ready: false, missing: ['조사 검증 완료 표시(verified)', '검색 URL 형식', '공개 지역 목록', '상세 링크 형식', '상세 해석 방식'] };
    base.coverage = {
      targetLabel: '전국',
      regionListStatus: 'unknown',
      unitLabel: '지역×검색어',
      planned: null,
      completed: 0,
      pending: 0,
      blocked: 0,
      failed: 0,
      scanCycle: null,
      lastVisitedAt: null,
      nextUp: null,
      depthLimit: null,
      budget: { used: 0, limit: 300, unitLabel: '요청/일' },
      marketCoverage: 'unknown',
      targetUnitsTotal: 17,
      targetUnitsCovered: null,
      estimatedCycleDays: null,
      notes: ['사이트 조사 미완료 — 탐색 범위를 계획할 수 없습니다'],
    };
    base.lastRunId = null;
  }
  return base;
}

function run(now: number, scenario: ScenarioId): RunInfo | null {
  if (scenario === 'first_run') return null;
  const r: RunInfo = {
    id: 'run-demo-1',
    sourceId: 'daangn-alba',
    leadId: null,
    kind: 'discovery',
    state: 'succeeded',
    trigger: 'schedule',
    startedAt: iso(now, -2.4 * HOUR),
    finishedAt: iso(now, -2 * HOUR),
    progress: { done: 51, total: 51, label: null },
    counts: { ...COUNTS_ZERO, requests: 212, detailsFetched: 38, created: 6, updated: 3, duplicates: 29, excluded: 4 },
    error: null,
    note: '탐색 범위가 전국 전체로 확인되지 않았습니다 (부분 탐색)',
    scanCycle: 4,
    result: {},
  };
  if (scenario === 'partial') {
    return {
      ...r,
      state: 'partial',
      finishedAt: iso(now, -1 * HOUR),
      progress: { done: 37, total: 51, label: null },
      counts: { ...r.counts, requests: 160, fetchFailures: 3 },
      error: { code: 'rate_limited', message: '요청 제한 응답(429) — Retry-After 까지 유예합니다', retryAfter: iso(now, 1 * HOUR) },
      note: null,
    };
  }
  if (scenario === 'blocked') {
    return {
      ...r,
      state: 'failed',
      trigger: 'schedule',
      finishedAt: iso(now, -0.5 * HOUR),
      progress: { done: 13, total: 51, label: null },
      counts: { ...COUNTS_ZERO, requests: 41, detailsFetched: 9, created: 1, duplicates: 8, policyStops: 1 },
      error: { code: 'http_403', message: '접근 차단 응답(403) — 소스를 정지합니다. 우회하지 않습니다.', retryAfter: null },
      note: null,
    };
  }
  if (scenario === 'ai_failure') return { ...r, counts: { ...r.counts, aiFailures: 4 } };
  if (scenario === 'empty') return { ...r, counts: { ...COUNTS_ZERO, requests: 118, detailsFetched: 0 }, note: '정상 완료 — 조건에 맞는 새 글 0건' };
  return r;
}

export function buildScenario(id: ScenarioId, now: number): ScenarioData {
  let leads = buildDemoLeads(now);
  if (id === 'empty' || id === 'first_run') leads = [];
  if (id === 'ai_failure') {
    const failIds = new Set(['L-1003', 'L-1006', 'L-1009', 'L-1010']);
    leads = leads.map((l) =>
      failIds.has(l.id)
        ? {
            ...l,
            analysisStatus: 'failed',
            analysis: {
              ...l.analysis,
              status: 'failed',
              summaryEngine: 'rules',
              engine: '규칙 (AI 실패)',
              failure: l.analysis.failure ?? { code: 'ai_budget_exceeded', message: '월 AI 비용 상한에 도달해 AI 분석을 건너뛰었습니다. 규칙 결과는 그대로 사용합니다.' },
            },
            reasons: [...l.reasons.slice(0, 2), { tone: 'negative', text: 'AI 분석 실패 — 규칙 결과만' }],
          }
        : l,
    );
  }
  if (id === 'blocked') {
    // 접근 차단은 마감이 아니다: 마지막으로 알려진 상태는 유지하고 확인 필요로 내린다 (백엔드 규칙과 동일)
    leads = leads.map((l) =>
      l.id === 'L-1001' || l.id === 'L-1004'
        ? {
            ...l,
            accessStatus: 'blocked',
            recheckDue: true,
            recommendation: 'needs_review',
            reasons: [{ tone: 'negative', text: '재확인 중 접근 차단(403)' }, ...l.reasons.slice(0, 2)],
          }
        : l,
    );
  }
  const r = run(now, id);
  return { leads, sources: [daangn(now, id), manualSource()], runs: r ? [r] : [] };
}
