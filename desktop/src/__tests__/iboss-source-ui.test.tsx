import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { filterToQuery, mapDetail, mapSource } from '../data/live/mapper';
import { MockPort } from '../data/mock/mockPort';
import type { RunInfo, SourceInfo } from '../domain/model';
import { CollectionPanel } from '../features/collection/CollectionPanel';
import { LeadDetailPane } from '../features/detail/LeadDetailPane';
import { TopBar } from '../features/header/TopBar';
import { deriveIssues } from '../features/header/IssueStrip';
import { DEFAULT_FILTER, FilterBar } from '../features/leads/FilterBar';
import { openExternal } from '../platform/external';
import { ToastProvider } from '../ui/Toasts';

vi.mock('../platform/external', async (original) => ({
  ...await original<typeof import('../platform/external')>(), openExternal: vi.fn().mockResolvedValue('opened'),
}));

const read = (name: string) => JSON.parse(readFileSync(join(__dirname, '../../../contracts/fixtures', name), 'utf-8'));
const rawSource = () => read('scenarios/normal/sources.json').items.find((item: { kind: string }) => item.kind === 'site');
const daangn = () => mapSource({ ...rawSource(), id: 'daangn-alba', name: '당근알바' });
const iboss = (): SourceInfo => {
  const raw = rawSource();
  return mapSource({
    ...raw, id: 'iboss', name: '아이보스', scope_note: '공개 대행의뢰 전체 분류',
    policy: { ...raw.policy, status: 'permission_pending' }, auto_collect: { enabled: false, interval_minutes: 360 },
    research: { ready: true, missing: [] },
    capabilities: [{ key: 'full_board_scan', label: '공개 대행의뢰 목록 순환', support: 'supported', note: null }],
    coverage: {
      ...raw.coverage, target_label: '아이보스 공개 대행의뢰 전체 분류', region_list_status: 'not_applicable',
      unit_label: '공개 의뢰 목록', depth_limit: 0, target_units_total: null, target_units_covered: null,
      next_up: '공개 목록 4페이지', notes: [],
    },
  });
};
const discovery = {
  strategy: 'public_board_fulltext', body_filter: 'all_public', page: 3, next_page: 4, has_next: true,
  listed_posts: 15, pages_completed: 2, posts_considered: 30, resumed: true,
  batch_request_limit: 73, continuation_pending: true,
};
const run = (patch: Partial<RunInfo> = {}): RunInfo => ({
  id: 'iboss-run', sourceId: 'iboss', leadId: null, kind: 'discovery', state: 'partial', trigger: 'manual',
  startedAt: '2026-09-30T10:00:00Z', finishedAt: '2026-09-30T10:10:00Z', progress: { done: 2, total: null, label: null },
  counts: { requests: 73, detailsFetched: 25, created: 10, updated: 0, duplicates: 15, excluded: 9, skipped: 0, parseFailures: 0, fetchFailures: 0, policyStops: 0, aiFailures: 0 },
  error: null, note: null, scanCycle: 1, result: { discovery, continuation_pending: true }, ...patch,
});

const resources: { qc: QueryClient; port: MockPort }[] = [];
function harness(sources = [daangn(), iboss()], records: RunInfo[] = []) {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  vi.spyOn(port, 'listSources').mockResolvedValue(sources);
  vi.spyOn(port, 'listRuns').mockImplementation(async (req) => records.filter((record) =>
    (!req.sourceId || record.sourceId === req.sourceId) && (!req.kinds || req.kinds.includes(record.kind)) && (!req.states || req.states.includes(record.state)),
  ));
  vi.spyOn(port, 'startRun').mockResolvedValue({ jobId: 'iboss-started', runId: 'iboss-started' });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  resources.push({ qc, port });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}><ToastProvider>{children}</ToastProvider></PortProvider></QueryClientProvider>;
  return { port, wrapper };
}
function panel(sources = [daangn(), iboss()], records: RunInfo[] = [], tab: 'sources' | 'runs' = 'sources') {
  const h = harness(sources, records);
  return { ...h, ...render(<CollectionPanel open onClose={() => undefined} initialTab={tab} onOpenLead={() => undefined} />, { wrapper: h.wrapper }) };
}
beforeEach(() => {
  vi.mocked(openExternal).mockClear();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => {
  for (const { qc, port } of resources.splice(0)) { qc.clear(); port.dispose(); }
  vi.restoreAllMocks();
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
});

describe('아이보스 수집 소스 표시', () => {
  it('기존 계약 매퍼가 사이트별 범위와 정책 상태를 그대로 보존한다', () => {
    const source = iboss();
    expect(source.id).toBe('iboss');
    expect(source.policy.status).toBe('permission_pending');
    expect(source.autoCollect.enabled).toBe(false);
    expect(source.coverage).toMatchObject({ targetLabel: '아이보스 공개 대행의뢰 전체 분류', regionListStatus: 'not_applicable', unitLabel: '공개 의뢰 목록', depthLimit: 0, targetUnitsTotal: null });
  });

  it('당근과 병존하며 아이보스에는 전국·제목 선별 설명을 붙이지 않는다', async () => {
    panel(undefined, [run(), run({ id: 'daangn-run', sourceId: 'daangn-alba', state: 'succeeded', result: { discovery: { listed_urls: 2000, title_candidates: 50, core_candidates: 50 } } })]);
    const board = await screen.findByRole('article', { name: '아이보스' });
    expect(board.textContent).toContain('활성 공고(마감 제외)를 제목으로 거르지 않고 공개 원문에서 확인');
    expect(board.textContent).toContain('AI 없이 규칙으로 건당·프로젝트 개발·제작 의뢰');
    expect(board.textContent).toContain('공개 목록 끝까지');
    expect(board.textContent).not.toMatch(/전국|시·도|관련 제목|제목 선별|사이트맵|sitemap/i);
    expect(within(board).getByLabelText('최근 공개 목록 탐색 결과').textContent).toContain('검토할 후보 1건');
    const original = screen.getByRole('article', { name: '당근알바' });
    expect(within(original).getByLabelText('최근 전국 탐색 결과').textContent).toContain('관련 제목 50개');
  });

  it.each(['sources', 'runs'] as const)('%s 화면에서 공개 목록 누적·이어보기·실행별 한도를 표시한다', async (tab) => {
    panel([iboss()], [run()], tab);
    const coverage = await screen.findByLabelText('공개 대행의뢰 목록 확인 범위');
    expect(coverage.textContent).toContain('현재 목록 3페이지 · 현재 목록 공고 15건 · 완료한 목록 2페이지 · 이번 실행 확인 대상 30건 · 이번 실행 요청 한도 73회');
    expect(coverage.textContent).toContain('저장된 목록 페이지에서 이어서');
    expect(coverage.textContent).toContain('남은 공개 목록은 다음 실행에서 이어서');
    expect(coverage.textContent).toContain('다음 목록 4페이지');
    expect(coverage.textContent).toContain('목록 공고 수는 적합한 의뢰 수와 다릅니다');
    expect(screen.queryByLabelText('제목 선별과 추가 확인 범위')).toBeNull();
    if (tab === 'runs') expect(await screen.findByText('아이보스')).toBeTruthy();
  });

  it.each(['sources', 'runs'] as const)('%s 화면에서 진행 중인 목록은 완료·다음 실행으로 표시하지 않는다', async (tab) => {
    panel([iboss()], [run({ state: 'running', finishedAt: null })], tab);
    const coverage = await screen.findByLabelText('공개 대행의뢰 목록 확인 범위');
    expect(coverage.textContent).toContain('현재 목록을 확인한 뒤 다음 페이지로 계속 진행합니다.');
    expect(coverage.textContent).toContain('저장된 목록 페이지에서 이어서 확인 중입니다.');
    expect(coverage.textContent).not.toMatch(/이번 실행의 확인을 마쳤습니다|다음 실행에서 이어서/);
  });

  it.each(['queued', 'paused', 'succeeded', 'failed', 'cancelled'] as const)('%s 기록의 남은 페이지 metadata만으로 정상 배치 종료를 추측하지 않는다', async (state) => {
    panel([iboss()], [run({ state })], 'runs');
    const coverage = await screen.findByLabelText('공개 대행의뢰 목록 확인 범위');
    expect(coverage.textContent).not.toMatch(/현재 목록을 확인한 뒤|이번 실행의 확인을 마쳤습니다|다음 실행에서 이어서|확인 중입니다/);
  });

  it('partial이라도 실행의 배치 종료 표시가 없으면 다음 실행 안내를 만들지 않는다', async () => {
    panel([iboss()], [run({ result: { discovery } })]);
    const coverage = await screen.findByLabelText('공개 대행의뢰 목록 확인 범위');
    expect(coverage.textContent).not.toMatch(/이번 실행의 확인을 마쳤습니다|다음 실행에서 이어서/);
  });

  it.each(['error', 'detailFailure'] as const)('partial 실행의 %s가 있으면 정상 배치 종료라고 표시하지 않는다', async (failure) => {
    const record = run();
    if (failure === 'error') record.error = { code: 'http_error', message: '요청 실패', retryAfter: null };
    else record.counts.fetchFailures = 1;
    panel([iboss()], [record]);
    const coverage = await screen.findByLabelText('공개 대행의뢰 목록 확인 범위');
    expect(coverage.textContent).not.toMatch(/이번 실행의 확인을 마쳤습니다|다음 실행에서 이어서/);
  });

  it('진행 중인 마지막 페이지에서는 존재하지 않는 다음 페이지를 약속하지 않는다', async () => {
    panel([iboss()], [run({ state: 'running', finishedAt: null, result: { discovery: { ...discovery, has_next: false, next_page: null } } })]);
    const coverage = await screen.findByLabelText('공개 대행의뢰 목록 확인 범위');
    expect(coverage.textContent).not.toMatch(/다음 페이지로 계속|다음 목록|다음 실행/);
  });

  it('잘못된 목록 수치·문자열 boolean에서 진행 상태를 추측하지 않는다', async () => {
    panel([iboss()], [run({ result: { discovery: { page: '3', listed_posts: -1, pages_completed: Number.NaN, posts_considered: 1.5, batch_request_limit: Number.POSITIVE_INFINITY, next_page: 0, has_next: 'true', resumed: 'true', continuation_pending: 'true' } } })]);
    const coverage = await screen.findByLabelText('공개 대행의뢰 목록 확인 범위');
    expect(coverage.textContent).not.toMatch(/현재 목록|완료한 목록|확인 대상|요청 한도|다음 목록|저장된 목록|다음 실행/);
    expect(coverage.textContent).not.toMatch(/NaN|Infinity|-1/);
  });

  it('요청 한도에서 정상 이어보기는 실패로 표시하지 않고 실제 오류는 보존한다', () => {
    const source = iboss(); source.policy.status = 'allowed';
    const options = { sources: [source], runs: [run()], stream: 'open' as const, mode: 'live' as const, openCollection: () => undefined, now: new Date('2026-09-30T10:00:00Z') };
    const continuation = deriveIssues(options);
    expect(continuation.find((issue) => issue.key === 'run-iboss-run')?.text).toContain('남은 공개 목록은 다음 실행에서 이어서 확인');
    expect(continuation.map((issue) => issue.text).join(' ')).not.toContain('실패');
    const error = deriveIssues({ ...options, runs: [run({ error: { code: 'http_error', message: '요청 실패', retryAfter: null } })] });
    expect(error.find((issue) => issue.key === 'run-iboss-run')?.text).toContain('요청 실패');
  });

  it.each(['permission_pending', 'blocked', 'not_required'] as const)('정책 %s 상태에서 아이보스를 자동 활성화하거나 실행하지 않는다', async (status) => {
    const source = iboss(); source.policy.status = status;
    const { port } = panel([source]);
    const board = await screen.findByRole('article', { name: '아이보스' });
    const start = within(board).getByRole('button', { name: '지금 탐색' }) as HTMLButtonElement;
    const automatic = within(board).getByRole('checkbox', { name: '자동 수집 (앱 실행 중에만)' }) as HTMLInputElement;
    expect(start.disabled).toBe(true);
    expect(automatic.disabled).toBe(true);
    expect(automatic.checked).toBe(false);
    fireEvent.click(start);
    expect(port.startRun).not.toHaveBeenCalled();
  });

  it('정책이 허용돼도 사이트 조사가 미완료면 실행을 막는다', async () => {
    const source = iboss(); source.policy.status = 'allowed'; source.research = { ready: false, missing: ['공개 목록 구조'] };
    panel([source]);
    const board = await screen.findByRole('article', { name: '아이보스' });
    expect((within(board).getByRole('button', { name: '지금 탐색' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each(['allowed', 'restricted'] as const)('조사 완료·정책 %s일 때 명시적 실행은 아이보스에만 전달한다', async (status) => {
    const source = iboss(); source.policy.status = status;
    const { port } = panel([daangn(), source]);
    const board = await screen.findByRole('article', { name: '아이보스' });
    expect(port.startRun).not.toHaveBeenCalled();
    expect((within(board).getByRole('checkbox', { name: '자동 수집 (앱 실행 중에만)' }) as HTMLInputElement).checked).toBe(false);
    fireEvent.click(within(board).getByRole('button', { name: '지금 탐색' }));
    await waitFor(() => expect(port.startRun).toHaveBeenCalledExactlyOnceWith({ sourceId: 'iboss', kind: 'discovery' }));
  });
});

describe('아이보스 필터·원문·상단 상태', () => {
  it('동적 사이트 필터는 아이보스 ID를 전달하고 검색은 저장된 본문 대상이라고 설명한다', () => {
    const change = vi.fn();
    render(<FilterBar filter={DEFAULT_FILTER} onChange={change} sources={[daangn(), iboss()]} categories={[]} />);
    fireEvent.change(screen.getByRole('combobox', { name: '사이트' }), { target: { value: 'iboss' } });
    expect(change).toHaveBeenLastCalledWith({ ...DEFAULT_FILTER, sourceId: 'iboss' });
    expect(filterToQuery({ ...DEFAULT_FILTER, sourceId: 'iboss', keyword: '자동화' })).toMatchObject({ source_id: 'iboss', keyword: '자동화' });
    const search = screen.getByRole('searchbox', { name: '검색' });
    expect(search.getAttribute('placeholder')).toContain('저장된 제목·본문 검색');
    expect(search.title).toContain('외부 사이트 검색이나 추가 수집을 실행하지 않습니다');
  });

  it('상세 매퍼와 원문 열기는 아이보스 이름·주소를 보존한다', async () => {
    const input = read('lead-details/01.json');
    input.source_id = 'iboss'; input.original_url = 'https://www.i-boss.co.kr/ab-1958-88584'; input.contact_channel = '원문에서 확인';
    const lead = mapDetail(input);
    expect(lead.sourceId).toBe('iboss');
    expect(lead.originalUrl).toBe(input.original_url);
    const { port, wrapper } = harness();
    vi.spyOn(port, 'getLead').mockResolvedValue(lead);
    render(<LeadDetailPane leadId={lead.id} sources={[daangn(), iboss()]} categoryLabel={(id) => id} now={new Date('2026-09-30T10:00:00Z')} overlay={false} onClose={() => undefined} onDismissed={() => undefined} />, { wrapper });
    expect(await screen.findByText('아이보스')).toBeTruthy();
    expect(openExternal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /원문 열기/ }));
    expect(openExternal).toHaveBeenCalledExactlyOnceWith(input.original_url);
  });

  it('상단은 여러 소스 범위를 구분하고 진행 실행에 소스 이름을 붙인다', () => {
    const props = { mode: 'live' as const, scenario: null, onScenario: () => undefined, sources: [daangn(), iboss()], runs: [] as RunInfo[], stream: 'open' as const, onCollection: () => undefined, onSettings: () => undefined, onHelp: () => undefined, now: new Date('2026-09-30T10:00:00Z') };
    const view = render(<TopBar {...props} />);
    const summary = screen.getByRole('button', { name: '수집 소스 2개 · 소스별 범위 확인' });
    expect(summary.title).toContain('당근알바:');
    expect(summary.title).toContain('아이보스: 아이보스 공개 대행의뢰 전체 분류 · 권한 확인 대기');
    view.rerender(<TopBar {...props} sources={[iboss()]} />);
    expect(screen.getByRole('button', { name: /아이보스 · 이번 주기/ }).title).not.toContain('전국');
    view.rerender(<TopBar {...props} runs={[run({ state: 'running', finishedAt: null })]} />);
    expect(screen.getByRole('button', { name: /아이보스 · 신규 탐색 진행 중/ })).toBeTruthy();
  });
});
