/**
 * 데모 데이터 계층 (mode = 'demo'). 화면 개발·테스트용이며 운영 데이터와 섞지 않는다.
 * 대기열·필터 규칙은 백엔드(services/leads.py)와 같은 정의를 따른다.
 */
import type {
  Bootstrap,
  JobAccepted,
  LeadDetail,
  LeadFilter,
  LeadSummary,
  Page,
  QueueId,
  RunInfo,
  SalesSummary,
  SettingsView,
  SourceInfo,
  StreamStatus,
  WorkleadEvent,
} from '../../domain/model';
import { intentClass, recruitClass, remoteClass } from '../../domain/present';
import { inQueue } from '../../domain/queues';
import { toSummary } from '../../domain/summary';
import { PortError, type WorkleadPort } from '../port';
import { DEMO_CATEGORIES, buildImportedLead, buildReserveLead } from './fixtures';
import { buildScenario, type ScenarioId } from './scenarios';

const QUEUES: Bootstrap['queues'] = [
  { id: 'recommended', label: '추천', description: '자동 판정 추천 · 내가 제외하지 않음 · 진행 전' },
  { id: 'needs_review', label: '확인 필요', description: '유망하지만 재택·모집·외주 여부 등 확인 필요 · 진행 전' },
  { id: 'interested', label: '관심', description: '관심 표시 · 진행 전' },
  { id: 'active', label: '진행 중', description: '연락함·협상 중·수주·보류' },
  { id: 'dismissed', label: '내가 제외', description: '내가 제외한 리드' },
  { id: 'auto_excluded', label: '자동 제외', description: '마감·판매자 홍보·출근 필수·위험 신호 등' },
  { id: 'all', label: '전체', description: '모든 리드' },
];

export function matchesFilter(l: LeadDetail, f: LeadFilter): boolean {
  if (f.sourceId && l.sourceId !== f.sourceId) return false;
  if (f.category && !l.categories.includes(f.category)) return false;
  const kw = f.keyword.trim().toLowerCase();
  if (kw) {
    const hay = `${l.title}\n${l.bodyText ?? ''}`.toLowerCase();
    if (!kw.split(/\s+/).every((t) => hay.includes(t))) return false;
  }
  const rc = remoteClass(l);
  switch (f.remote) {
    case 'confirmed':
      if (rc !== 'confirmed') return false;
      break;
    case 'confirmed_or_inferred':
      if (rc !== 'confirmed' && rc !== 'inferred') return false;
      break;
    case 'unknown':
      if (rc !== 'unknown') return false;
      break;
    case 'onsite':
      if (rc !== 'onsite' && l.collaborationMode.value !== 'onsite_required') return false;
      break;
  }
  const cls = recruitClass(l);
  switch (f.recruit) {
    case 'not_closed':
      if (cls === 'closed' || cls === 'deleted') return false;
      break;
    case 'open_fresh':
      if (cls !== 'open') return false;
      break;
    case 'recheck':
      if (cls !== 'recheck') return false;
      break;
    case 'unknown':
      if (cls !== 'unknown') return false;
      break;
    case 'access_issue':
      if (cls !== 'access_issue') return false;
      break;
    case 'closed':
      if (cls !== 'closed' && cls !== 'deleted') return false;
      break;
  }
  if (f.intent !== 'any' && intentClass(l.demandIntent.value) !== f.intent) return false;
  return true;
}

function sortKey(l: LeadSummary, f: LeadFilter): number {
  if (f.sort === 'published') return l.published.at ? Date.parse(l.published.at) : -1;
  if (f.sort === 'checked') return l.lastCheckedAt ? Date.parse(l.lastCheckedAt) : -1;
  return l.priority.total ?? -1;
}

export interface MockOptions {
  scenario: ScenarioId;
  /** 응답 지연(ms). 테스트에서는 0 */
  latency?: [number, number];
  now?: () => number;
}

const DEFAULT_SETTINGS: SettingsView = {
  profile: {
    services: ['website', 'landing', 'shop', 'fullstack', 'software', 'vba', 'automation'],
    skills: '',
    excludedWork: '',
    minContract: null,
    targetHourly: 40_000,
    weeklyHours: null,
    onsite: 'no',
    allowShortTermEmployment: true,
    gigOnly: true,
    intro: '',
    portfolioUrl: null,
  },
  recheck: { ttlHours: 24 },
  notifications: { newRecommended: true, meaningfulChange: true, sourceIssue: true, quietHours: { enabled: true, start: '22:00', end: '08:00' } },
  ai: { enabled: false, externalTransferConsent: false, engineLabel: null, monthlyCostCap: null, dailyCostCap: null, keyConfigured: false, providerAvailable: false },
  retention: { rawDays: 30 },
  queryGroups: {
    version: 1,
    groups: [
      { id: 'direct_build', label: '직접 제작 수요', enabled: true, keywords: ['홈페이지', '웹사이트', '웹페이지', '사이트 제작', '랜딩페이지', '쇼핑몰 제작', '쇼핑몰 오픈', '자사몰', '카페24', '워드프레스', '앱 개발', '앱 제작', '어플', '웹앱', '프로그램 개발', '프로그램 제작', '프로그래머', '소프트웨어', '풀스택', '개발자', '웹 개발'] },
      { id: 'automation', label: '자동화 수요', enabled: true, keywords: ['엑셀', 'VBA', '매크로', '자동화', '크롤링', '파이썬', '챗봇', '구글 시트', '업무툴', 'API 연동'] },
      { id: 'buyer_intent', label: '구매 의도', enabled: true, keywords: ['만들어주실', '제작해주실', '개발해주실', '수정해주실', '의뢰', '외주', '견적', '구합니다'] },
      { id: 'problem', label: '문제 표현', enabled: false, keywords: ['반복 입력', '엑셀 취합', '수작업', '관리 페이지 필요', '기존 사이트 수정'] },
    ],
  },
};

export class MockPort implements WorkleadPort {
  readonly mode = 'demo' as const;
  private leads: LeadDetail[];
  private sources: SourceInfo[];
  private runs: RunInfo[];
  private settings: SettingsView = structuredClone(DEFAULT_SETTINGS);
  private listeners = new Set<(e: WorkleadEvent) => void>();
  private statusListeners = new Set<(s: StreamStatus) => void>();
  private seq = 100;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private reserveUsed = false;
  private importSeq = 0;
  readonly scenario: ScenarioId;
  private latency: [number, number];
  private now: () => number;

  constructor(opts: MockOptions) {
    this.scenario = opts.scenario;
    this.latency = opts.latency ?? [120, 320];
    this.now = opts.now ?? (() => Date.now());
    const data = buildScenario(opts.scenario, this.now());
    this.leads = data.leads;
    this.sources = data.sources;
    this.runs = data.runs;
  }

  dispose(): void {
    this.timers.forEach((t) => clearTimeout(t));
    this.timers.clear();
    this.listeners.clear();
  }

  // ── 공통 ──────────────────────────────────────────────────────────
  private async delay<T>(value: () => T): Promise<T> {
    if (this.scenario === 'offline') {
      await this.sleep(this.latency[1]);
      throw new PortError({ kind: 'connection', code: 'connection_failed', message: '로컬 서비스에 연결할 수 없습니다', retryable: true });
    }
    const [a, b] = this.latency;
    if (b > 0) await this.sleep(a + Math.random() * (b - a));
    return structuredClone(value());
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      if (ms <= 0) resolve();
      else setTimeout(resolve, ms);
    });
  }

  private later(ms: number, fn: () => void): void {
    const t = setTimeout(() => {
      this.timers.delete(t);
      fn();
    }, ms);
    this.timers.add(t);
  }

  private emit(e: Record<string, unknown> & { type: WorkleadEvent['type'] }): void {
    this.seq += 1;
    const evt = { id: `evt-${this.seq}`, seq: this.seq, at: new Date(this.now()).toISOString(), ...e } as WorkleadEvent;
    this.listeners.forEach((l) => l(evt));
  }

  private find(id: string): LeadDetail {
    const lead = this.leads.find((l) => l.id === id);
    if (!lead) throw new PortError({ kind: 'api', code: 'not_found', message: '리드를 찾을 수 없습니다' });
    return lead;
  }

  private withDerived(l: LeadDetail): LeadDetail {
    const ttl = this.settings.recheck.ttlHours * 3_600_000;
    const stale = l.lastCheckedAt === null || this.now() - Date.parse(l.lastCheckedAt) > ttl;
    return { ...l, recheckDue: l.recheckDue || (l.sourceStatus === 'open' && stale), hasMemo: Boolean(l.memo) };
  }

  private activity(l: LeadDetail, text: string): void {
    l.activity = [{ at: new Date(this.now()).toISOString(), text }, ...l.activity];
  }

  // ── 조회 ──────────────────────────────────────────────────────────
  bootstrap(): Promise<Bootstrap> {
    return this.delay(() => ({
      mode: 'demo' as const,
      appVersion: '0.1.0',
      contractVersion: '2026-09-26.1',
      categories: DEMO_CATEGORIES,
      queues: QUEUES,
      recheckTtlHours: this.settings.recheck.ttlHours,
      eventSeq: this.seq,
    }));
  }

  listLeads(req: { queue: QueueId; filter: LeadFilter; cursor: string | null; limit: number }): Promise<Page<LeadSummary>> {
    return this.delay(() => {
      const rows = this.leads
        .map((l) => this.withDerived(l))
        .filter((l) => inQueue(l, req.queue) && matchesFilter(l, req.filter))
        .sort((a, b) => sortKey(b, req.filter) - sortKey(a, req.filter) || (a.id < b.id ? 1 : -1));
      const start = req.cursor ? Number(req.cursor) : 0;
      const slice = rows.slice(start, start + req.limit);
      const next = start + req.limit < rows.length ? String(start + req.limit) : null;
      return { items: slice.map(toSummary), nextCursor: next, hasMore: next !== null };
    });
  }

  countQueues(filter: LeadFilter): Promise<Record<QueueId, number> | null> {
    return this.delay(() => {
      const rows = this.leads.map((l) => this.withDerived(l)).filter((l) => matchesFilter(l, filter));
      const out = {} as Record<QueueId, number>;
      for (const q of QUEUES) out[q.id] = rows.filter((l) => inQueue(l, q.id)).length;
      return out;
    });
  }

  getLead(id: string): Promise<LeadDetail> {
    return this.delay(() => this.withDerived(this.find(id)));
  }

  // ── 사용자 판단 ─────────────────────────────────────────────────────
  updateLead(id: string, patch: Parameters<WorkleadPort['updateLead']>[1]): Promise<LeadDetail> {
    return this.delay(() => {
      const l = this.find(id);
      if (patch.userMark !== undefined && patch.userMark !== l.userMark) {
        l.userMark = patch.userMark;
        this.activity(l, patch.userMark === 'interested' ? '관심 표시' : patch.userMark === 'dismissed' ? '제외 표시' : '관심·제외 해제');
      }
      if (patch.salesStage && patch.salesStage !== l.salesStage) {
        this.activity(l, `진행 상태 ${l.salesStage} → ${patch.salesStage}`);
        l.salesStage = patch.salesStage;
      }
      if (patch.memo !== undefined && patch.memo !== l.memo) {
        l.memo = patch.memo;
        this.activity(l, '메모 수정');
      }
      if (patch.draftText !== undefined) {
        l.draft = { text: patch.draftText, generatedAt: l.draft?.generatedAt ?? null, editedByUser: true };
      }
      this.emit({ type: 'lead.updated', leadId: id });
      return this.withDerived(l);
    });
  }

  sendFeedback(id: string, fb: Parameters<WorkleadPort['sendFeedback']>[1]): Promise<LeadDetail> {
    return this.delay(() => {
      const l = this.find(id);
      if (fb.kind === 'remote') l.feedback.remote = fb.value === 'clear' ? null : (fb.value as 'confirmed' | 'denied');
      else l.feedback.realRequest = fb.value === 'clear' ? null : (fb.value as 'yes' | 'no');
      this.activity(l, { confirmed: '재택 가능 확인', denied: '재택 불가 확인', yes: '실제 의뢰로 확인', no: '실제 의뢰 아님으로 표시', clear: '내 확인 취소' }[fb.value]);
      this.emit({ type: 'lead.updated', leadId: id });
      return this.withDerived(l);
    });
  }

  addOutcome(id: string, input: Parameters<WorkleadPort['addOutcome']>[1]): Promise<LeadDetail> {
    return this.delay(() => {
      if (input.amount !== null && input.amount < 0) throw new PortError({ kind: 'invalid_input', code: 'validation_error', message: '금액은 0 이상이어야 합니다' });
      const l = this.find(id);
      l.outcomes = [
        ...l.outcomes,
        { id: `o-${this.now()}`, currency: 'KRW', recordedAt: new Date(this.now()).toISOString(), ...input },
      ];
      this.activity(l, '영업 결과 기록');
      this.emit({ type: 'lead.updated', leadId: id });
      return this.withDerived(l);
    });
  }

  generateDraft(id: string): Promise<LeadDetail> {
    return this.delay(() => {
      const l = this.find(id);
      if (l.draft?.editedByUser) throw new PortError({ kind: 'api', code: 'draft_edited', message: '직접 수정한 초안이 있어 덮어쓰지 않았습니다' });
      const qs = l.analysis.questions.map((q, i) => `${i + 1}) ${q}`).join('\n');
      l.draft = {
        text: `안녕하세요, 올려주신 글 보고 연락드립니다.\n'${l.title}' 관련해 원격으로 작업 가능한지 여쭙고 싶습니다.\n\n[간단한 소개와 비슷한 작업 경험 — 직접 작성]\n\n${qs ? `진행 전에 몇 가지 확인하고 싶습니다.\n${qs}\n\n` : ''}답변 주시면 범위와 일정, 견적을 정리해 드리겠습니다.`,
        generatedAt: new Date(this.now()).toISOString(),
        editedByUser: false,
      };
      this.activity(l, '문의 초안 생성');
      return this.withDerived(l);
    });
  }

  recheckLead(id: string): Promise<JobAccepted> {
    return this.delay(() => {
      const l = this.find(id);
      const src = this.sources.find((s) => s.id === l.sourceId);
      if (!src || src.kind === 'manual') {
        throw new PortError({ kind: 'api', code: 'unsupported', message: '수동 입력 리드는 원문을 대신 확인하지 않습니다. 원문을 직접 열어 확인하세요.' });
      }
      if (src.policy.status !== 'allowed' && src.policy.status !== 'restricted') {
        throw new PortError({ kind: 'api', code: 'policy_not_allowed', message: '이 소스는 자동 접근 권한이 확인되지 않아 재확인할 수 없습니다. 원문을 직접 확인하세요.' });
      }
      l.activeJob = 'recheck';
      const jobId = `job-recheck-${this.now()}`;
      this.emit({ type: 'lead.updated', leadId: id });
      this.later(1400, () => {
        l.activeJob = null;
        if (src.stoppedReason) {
          l.accessStatus = 'blocked';
          this.activity(l, '재확인 실패 — 소스 정지 상태 (접근 차단은 마감으로 바꾸지 않음)');
        } else {
          l.lastCheckedAt = new Date(this.now()).toISOString();
          l.recheckDue = false;
          l.accessStatus = 'accessible';
          this.activity(l, '모집 상태 재확인');
        }
        this.emit({ type: 'lead.updated', leadId: id });
      });
      return { jobId, runId: jobId };
    });
  }

  reanalyzeLead(id: string): Promise<JobAccepted> {
    return this.delay(() => {
      const l = this.find(id);
      l.activeJob = 'reanalyze';
      this.emit({ type: 'lead.updated', leadId: id });
      this.later(1600, () => {
        l.activeJob = null;
        if (this.scenario === 'ai_failure') {
          this.activity(l, 'AI 재분석 실패 — 규칙 결과 유지');
        } else {
          l.analysisStatus = 'complete';
          l.analysis = { ...l.analysis, status: 'complete', failure: null, engine: '규칙 + AI (데모)' };
          l.reasons = l.reasons.filter((r) => !r.text.startsWith('AI 분석 실패'));
          this.activity(l, '재분석 완료');
        }
        this.emit({ type: 'analysis.completed', leadId: id, status: l.analysisStatus });
        this.emit({ type: 'lead.updated', leadId: id });
      });
      return { jobId: `job-reanalyze-${this.now()}`, runId: null };
    });
  }

  // ── 소스·실행 ─────────────────────────────────────────────────────
  listSources(): Promise<SourceInfo[]> {
    return this.delay(() => this.sources);
  }

  updateSource(id: string, patch: Parameters<WorkleadPort['updateSource']>[1]): Promise<SourceInfo> {
    return this.delay(() => {
      const s = this.sources.find((x) => x.id === id);
      if (!s) throw new PortError({ kind: 'api', code: 'not_found', message: '소스를 찾을 수 없습니다' });
      if (patch.policy) {
        if ((patch.policy.status === 'allowed' || patch.policy.status === 'restricted') && !patch.policy.basis?.trim()) {
          throw new PortError({ kind: 'invalid_input', code: 'policy_basis_required', message: '허용·제한 판단에는 근거(약관 조항·허가 문서 등)가 필요합니다' });
        }
        s.policy = { ...s.policy, status: patch.policy.status, basis: patch.policy.basis, note: patch.policy.note, reviewedAt: new Date(this.now()).toISOString() };
        if (patch.policy.status === 'permission_pending' || patch.policy.status === 'blocked') s.autoCollect.enabled = false;
      }
      if (patch.clearStop) {
        s.stoppedReason = null;
        s.health = { status: 'unknown', checkedAt: null, message: '사용자가 정지를 해제함 — 다음 실행에서 상태 확인', code: null };
      }
      if (patch.intervalMinutes) s.autoCollect.intervalMinutes = patch.intervalMinutes;
      if (patch.autoCollectEnabled !== undefined) {
        if (patch.autoCollectEnabled) {
          if (s.policy.status !== 'allowed' && s.policy.status !== 'restricted')
            throw new PortError({ kind: 'api', code: 'policy_not_allowed', message: `자동 수집 권한이 확인되지 않았습니다 (${s.policy.status})` });
          if (!s.research.ready) throw new PortError({ kind: 'api', code: 'research_incomplete', message: '사이트 조사가 끝나지 않았습니다', details: { missing: s.research.missing } });
          if (s.stoppedReason) throw new PortError({ kind: 'api', code: 'source_stopped', message: '정지된 소스입니다. 원인을 확인한 뒤 정지를 해제하세요.' });
        }
        s.autoCollect.enabled = patch.autoCollectEnabled;
      }
      this.emit({ type: 'source.health_changed', sourceId: id, health: s.health.status });
      return s;
    });
  }

  listRuns(req: { sourceId?: string; limit: number }): Promise<RunInfo[]> {
    return this.delay(() => this.runs.filter((r) => !req.sourceId || r.sourceId === req.sourceId).slice(0, req.limit));
  }

  getRun(id: string): Promise<RunInfo> {
    return this.delay(() => {
      const r = this.runs.find((x) => x.id === id);
      if (!r) throw new PortError({ kind: 'api', code: 'not_found', message: '실행을 찾을 수 없습니다' });
      return r;
    });
  }

  startRun(req: Parameters<WorkleadPort['startRun']>[0]): Promise<JobAccepted> {
    return this.delay(() => {
      const src = this.sources.find((s) => s.id === req.sourceId);
      if (!src) throw new PortError({ kind: 'api', code: 'not_found', message: '소스를 찾을 수 없습니다' });
      if (src.policy.status !== 'allowed' && src.policy.status !== 'restricted')
        throw new PortError({ kind: 'api', code: 'policy_not_allowed', message: `자동 수집 권한이 확인되지 않았습니다 (${src.policy.status})` });
      if (!src.research.ready) throw new PortError({ kind: 'api', code: 'research_incomplete', message: '사이트 조사가 끝나지 않아 탐색할 수 없습니다', details: { missing: src.research.missing } });
      if (src.stoppedReason) throw new PortError({ kind: 'api', code: 'source_stopped', message: `정지된 소스입니다: ${src.stoppedReason}` });
      const active = this.runs.find((r) => r.sourceId === src.id && r.kind === req.kind && ['queued', 'running', 'paused'].includes(r.state));
      if (active) throw new PortError({ kind: 'api', code: 'run_already_active', message: '같은 소스의 같은 종류 실행이 이미 진행 중입니다', details: { run_id: active.id } });
      const total = src.coverage?.planned ?? 10;
      const run: RunInfo = {
        id: `run-${this.now()}`,
        sourceId: src.id,
        leadId: null,
        kind: req.kind,
        state: 'queued',
        trigger: 'manual',
        startedAt: null,
        finishedAt: null,
        progress: { done: 0, total, label: null },
        counts: { requests: 0, detailsFetched: 0, created: 0, updated: 0, duplicates: 0, excluded: 0, parseFailures: 0, fetchFailures: 0, policyStops: 0, aiFailures: 0 },
        error: null,
        note: null,
        scanCycle: (src.coverage?.scanCycle ?? 0) + 1,
        result: {},
      };
      this.runs = [run, ...this.runs];
      this.emit({ type: 'run.state_changed', runId: run.id, sourceId: src.id, state: 'queued' });
      this.later(500, () => this.tickRun(run.id));
      return { jobId: run.id, runId: run.id };
    });
  }

  private tickRun(runId: string): void {
    const run = this.runs.find((r) => r.id === runId);
    if (!run || ['succeeded', 'partial', 'failed', 'cancelled', 'paused'].includes(run.state)) return;
    if (run.state === 'queued') {
      run.state = 'running';
      run.startedAt = new Date(this.now()).toISOString();
      this.emit({ type: 'run.state_changed', runId, sourceId: run.sourceId, state: 'running' });
    }
    const total = run.progress.total ?? 10;
    const step = Math.max(1, Math.round(total / 8));
    run.progress = { done: Math.min(total, run.progress.done + step), total, label: ['서울 강남구', '부산 수영구', '대구 중구', '광주 서구', '대전 유성구', '강원 춘천시', '전북 전주시', '제주 제주시'][Math.floor(Math.random() * 8)] + ' · 홈페이지' };
    run.counts = { ...run.counts, requests: run.counts.requests + step * 2, duplicates: run.counts.duplicates + Math.floor(step / 2) };
    this.emit({ type: 'run.progress', runId, sourceId: run.sourceId, progress: run.progress });
    if (run.progress.done >= total) {
      run.state = 'succeeded';
      run.finishedAt = new Date(this.now()).toISOString();
      run.note = '탐색 범위가 전국 전체로 확인되지 않았습니다 (부분 탐색)';
      const src = this.sources.find((s) => s.id === run.sourceId);
      if (src?.coverage) src.coverage = { ...src.coverage, lastVisitedAt: run.finishedAt, scanCycle: run.scanCycle };
      if (!this.reserveUsed) {
        this.reserveUsed = true;
        const lead = buildReserveLead(this.now());
        this.leads = [lead, ...this.leads];
        run.counts = { ...run.counts, created: run.counts.created + 1, detailsFetched: run.counts.detailsFetched + 1 };
        this.emit({ type: 'lead.created', leadId: lead.id });
        this.emit({ type: 'notification.created', kind: 'new_lead', title: `새 추천 리드: ${lead.title}`, leadId: lead.id, sourceId: run.sourceId, suppressedReason: null });
      }
      this.emit({ type: 'run.state_changed', runId, sourceId: run.sourceId, state: 'succeeded' });
      return;
    }
    this.later(700, () => this.tickRun(runId));
  }

  runAction(runId: string, action: 'pause' | 'resume' | 'cancel'): Promise<RunInfo> {
    return this.delay(() => {
      const run = this.runs.find((r) => r.id === runId);
      if (!run) throw new PortError({ kind: 'api', code: 'not_found', message: '실행을 찾을 수 없습니다' });
      if (action === 'pause' && (run.state === 'running' || run.state === 'queued')) run.state = 'paused';
      else if (action === 'resume' && run.state === 'paused') {
        run.state = 'queued';
        this.later(300, () => this.tickRun(runId));
      } else if (action === 'cancel' && ['running', 'queued', 'paused'].includes(run.state)) {
        run.state = 'cancelled';
        run.finishedAt = new Date(this.now()).toISOString();
      } else throw new PortError({ kind: 'api', code: 'invalid_state', message: `${run.state} 상태에서는 할 수 없습니다` });
      this.emit({ type: 'run.state_changed', runId, sourceId: run.sourceId, state: run.state });
      return run;
    });
  }

  importManual(input: Parameters<WorkleadPort['importManual']>[0]): Promise<JobAccepted> {
    return this.delay(() => {
      if (!input.content.trim()) throw new PortError({ kind: 'invalid_input', code: 'empty', message: '내용이 비어 있습니다' });
      if (new Blob([input.content]).size > 2 * 1024 * 1024) throw new PortError({ kind: 'invalid_input', code: 'too_large', message: '입력은 최대 2MB 입니다' });
      this.importSeq += 1;
      const id = `L-9${String(this.importSeq).padStart(3, '0')}`;
      const runId = `run-import-${this.now()}`;
      const run: RunInfo = {
        id: runId,
        sourceId: 'manual',
        leadId: null,
        kind: 'import',
        state: 'running',
        trigger: 'manual',
        startedAt: new Date(this.now()).toISOString(),
        finishedAt: null,
        progress: { done: 0, total: 1, label: null },
        counts: { requests: 0, detailsFetched: 0, created: 0, updated: 0, duplicates: 0, excluded: 0, parseFailures: 0, fetchFailures: 0, policyStops: 0, aiFailures: 0 },
        error: null,
        note: null,
        scanCycle: null,
        result: {},
      };
      this.runs = [run, ...this.runs];
      this.later(900, () => {
        if (input.format !== 'text') {
          run.state = 'failed';
          run.error = { code: 'import_invalid', message: '데모에서는 텍스트 붙여넣기만 처리합니다', retryAfter: null };
        } else {
          const lead = buildImportedLead(this.now(), id, input.content, input.originalUrl);
          this.leads = [lead, ...this.leads];
          run.state = 'succeeded';
          run.counts = { ...run.counts, created: 1 };
          run.result = { lead_ids: [id] };
          this.emit({ type: 'lead.created', leadId: id });
        }
        run.progress = { done: 1, total: 1, label: null };
        run.finishedAt = new Date(this.now()).toISOString();
        this.emit({ type: 'run.state_changed', runId, sourceId: 'manual', state: run.state });
      });
      return { jobId: runId, runId };
    });
  }

  exportData(): Promise<JobAccepted> {
    return this.delay(() => {
      throw new PortError({ kind: 'not_implemented', code: 'demo_only', message: '데모 모드에서는 파일로 내보내지 않습니다' });
    });
  }

  // ── 설정·지표 ─────────────────────────────────────────────────────
  getSettings(): Promise<SettingsView> {
    return this.delay(() => this.settings);
  }

  updateSettings(patch: Parameters<WorkleadPort['updateSettings']>[0]): Promise<SettingsView> {
    return this.delay(() => {
      const nextAi = { ...this.settings.ai, ...(patch.ai ?? {}) };
      if (nextAi.enabled && !nextAi.externalTransferConsent)
        throw new PortError({ kind: 'invalid_input', code: 'consent_required', message: '외부 AI 전송 고지에 동의해야 AI 분석을 켤 수 있습니다' });
      this.settings = {
        ...this.settings,
        ...(patch.profile ? { profile: patch.profile } : {}),
        ...(patch.recheck ? { recheck: patch.recheck } : {}),
        ...(patch.notifications ? { notifications: patch.notifications } : {}),
        ...(patch.retention ? { retention: patch.retention } : {}),
        ai: nextAi,
        ...(patch.queryGroups ? { queryGroups: { version: this.settings.queryGroups.version + 1, groups: patch.queryGroups } } : {}),
      };
      return this.settings;
    });
  }

  getSalesSummary(): Promise<SalesSummary> {
    return this.delay(() => {
      const outs = this.leads.flatMap((l) => l.outcomes);
      const contract = outs.filter((o) => o.kind === 'contract_confirmed');
      const count = (s: string) => this.leads.filter((l) => l.salesStage === s).length;
      return {
        periodDays: 30,
        reviewed: this.leads.filter((l) => l.userMark !== null || l.salesStage !== 'new').length,
        contacted: count('contacted'),
        negotiating: count('negotiating'),
        won: count('won'),
        contractsConfirmed: contract.length,
        contractAmount: contract.some((o) => o.amount === null) ? null : contract.reduce((a, o) => a + (o.amount ?? 0), 0),
        collected: outs.filter((o) => o.kind === 'payment_received').reduce((a, o) => a + (o.amount ?? 0), 0),
        refunded: outs.filter((o) => o.kind === 'refund').reduce((a, o) => a + (o.amount ?? 0), 0),
        directCost: outs.filter((o) => o.kind === 'direct_cost').reduce((a, o) => a + (o.amount ?? 0), 0),
      };
    });
  }

  subscribe(onEvent: (e: WorkleadEvent) => void, onStatus: (s: StreamStatus) => void): () => void {
    this.listeners.add(onEvent);
    this.statusListeners.add(onStatus);
    if (this.scenario === 'offline') {
      onStatus('retrying');
    } else {
      onStatus('connecting');
      this.later(200, () => onStatus('open'));
    }
    return () => {
      this.listeners.delete(onEvent);
      this.statusListeners.delete(onStatus);
    };
  }
}
