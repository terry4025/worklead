import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { qk } from '../app/queries';
import { MockPort } from '../data/mock/mockPort';
import type { RunInfo } from '../domain/model';
import { CollectionPanel } from '../features/collection/CollectionPanel';
import { openExternal } from '../platform/external';
import { ToastProvider } from '../ui/Toasts';

vi.mock('../platform/external', async (importOriginal) => ({
  ...await importOriginal<typeof import('../platform/external')>(),
  openExternal: vi.fn().mockResolvedValue('opened'),
}));

const run = (patch: Partial<RunInfo> = {}): RunInfo => ({
  id: 'coverage-run', sourceId: 'daangn-alba', leadId: null, kind: 'discovery', state: 'succeeded', trigger: 'schedule',
  startedAt: '2026-09-30T10:00:00Z', finishedAt: '2026-09-30T10:01:00Z', progress: { done: 1, total: 1, label: null },
  counts: { requests: 12, detailsFetched: 8, created: 1, updated: 0, duplicates: 7, excluded: 0, skipped: 0, parseFailures: 0, fetchFailures: 0, policyStops: 0, aiFailures: 0 },
  error: null, note: null, scanCycle: 1, result: {}, ...patch,
});

const discovery = {
  listed_urls: 2_000, title_candidates: 74, core_candidates: 74, exploration_candidates: 23,
  exploration_requests: 12, exploration_checked: 10, exploration_deferred: 13,
  exploration_limit: 10, exploration_reserve: 30,
};

const clients: QueryClient[] = [];
function show(records: RunInfo[], tab: 'sources' | 'runs' = 'sources') {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  vi.spyOn(port, 'listRuns').mockImplementation(async (request) => records.filter((r) =>
    (!request.kinds || request.kinds.includes(r.kind)) && (!request.states || request.states.includes(r.state)),
  ));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(qc);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}><ToastProvider>{children}</ToastProvider></PortProvider>
    </QueryClientProvider>
  );
  return { ...render(<CollectionPanel open onClose={() => undefined} initialTab={tab} onOpenLead={() => undefined} />, { wrapper }), port, qc };
}

beforeEach(() => {
  vi.mocked(openExternal).mockReset().mockResolvedValue('opened');
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
});
afterEach(() => {
  for (const qc of clients.splice(0)) qc.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});

describe('제목 선별과 추가 후보 본문 확인 범위', () => {
  it('최근 결과의 기존 공개 목록과 실제 신규 수를 유지하고 후보·추가 요청·보류를 구분한다', async () => {
    show([run({ result: { discovery } })]);
    const recent = await screen.findByLabelText('최근 전국 탐색 결과');
    const coverage = within(recent).getByLabelText('제목 선별과 추가 확인 범위');
    expect(within(recent).getByText('공개 목록 2,000개 주소에서 관련 제목 74개 확인')).toBeTruthy();
    expect(within(recent).getByText('신규 1 · 기존 중복 7 · 요청 실패 0 · 해석 실패 0')).toBeTruthy();
    expect(within(coverage).getByText('핵심 후보 74개 · 추가 확인 후보 23개 · 추가 상세 요청 12회 · 다음 탐색 보류 13개')).toBeTruthy();
    expect(coverage.textContent).toContain('전체 본문 검색은 아닙니다');
    expect(coverage.textContent).toContain('공개 목록에 없는 글이나 의뢰 표현이 본문에만 있는 글은 누락될 수 있습니다');
    expect(coverage.textContent).toContain('후보 수는 적합한 의뢰 수와 다릅니다');
    expect(coverage.textContent).toContain('정책 확인·주소 이동 요청도 포함합니다');
    expect(coverage.textContent).toContain('다음 확인 여부는 탐색 예산과 원문 상태에 따라 달라집니다');
  });

  it('실행 기록에서도 처리 후보 수가 아닌 실제 추가 HTTP 수를 보여 준다', async () => {
    show([run({ result: { discovery } })], 'runs');
    const coverage = await screen.findByLabelText('제목 선별과 추가 확인 범위');
    expect(coverage.textContent).toContain('추가 상세 요청 12회');
    expect(coverage.textContent).not.toContain('추가 상세 요청 10회');
  });

  it('진행 중 실행의 0건도 숨기거나 미확인으로 바꾸지 않는다', async () => {
    show([run({ state: 'running', finishedAt: null, result: { discovery: {
      core_candidates: 0, exploration_candidates: 0, exploration_requests: 0, exploration_deferred: 0,
    } } })]);
    const active = await screen.findByLabelText('진행 중 전국 탐색');
    expect(within(active).getByText('핵심 후보 0개 · 추가 확인 후보 0개 · 추가 상세 요청 0회 · 다음 탐색 보류 0개')).toBeTruthy();
  });

  it('이전 실행의 공개 목록과 관련 제목 0개를 유지하고 새 집계를 만들지 않는다', async () => {
    show([run({ result: { discovery: { listed_urls: 0, title_candidates: 0 } } })]);
    const recent = await screen.findByLabelText('최근 전국 탐색 결과');
    expect(within(recent).getByText('공개 목록 0개 주소에서 관련 제목 0개 확인')).toBeTruthy();
    expect(within(recent).queryByLabelText('제목 선별과 추가 확인 범위')).toBeNull();
  });

  it.each([undefined, null, [], '<img src=x onerror=alert(1)>'])('잘못됐거나 없는 discovery 결과 %j를 표시하지 않는다', async (value) => {
    show([run({ result: value === undefined ? undefined : { discovery: value } })]);
    const recent = await screen.findByLabelText('최근 전국 탐색 결과');
    expect(within(recent).queryByLabelText('제목 선별과 추가 확인 범위')).toBeNull();
    expect(recent.textContent).not.toContain('onerror');
    expect(recent.textContent).toContain('신규 1');
  });

  it('문자열·음수·NaN·Infinity를 출력하지 않고 유효한 수치만 남긴다', async () => {
    show([run({ result: { discovery: {
      core_candidates: '<script>alert(1)</script>', exploration_candidates: -4,
      exploration_requests: Number.NaN, exploration_deferred: Number.POSITIVE_INFINITY,
    } } })]);
    const recent = await screen.findByLabelText('최근 전국 탐색 결과');
    expect(within(recent).queryByLabelText('제목 선별과 추가 확인 범위')).toBeNull();
    for (const invalid of ['<script>', 'alert(1)', '-4', 'NaN', 'Infinity']) expect(recent.textContent).not.toContain(invalid);
  });

  it('일부 필드가 잘못돼도 유효한 0을 표시하고 잘못된 수치는 0으로 보정하지 않는다', async () => {
    show([run({ result: { discovery: {
      core_candidates: Number.MAX_SAFE_INTEGER + 1, exploration_candidates: 0,
      exploration_requests: '12', exploration_deferred: 1.5,
    } } })]);
    const coverage = await screen.findByLabelText('제목 선별과 추가 확인 범위');
    expect(within(coverage).getByText('추가 확인 후보 0개')).toBeTruthy();
    expect(coverage.textContent).not.toMatch(/핵심 후보|추가 상세 요청 \d|다음 탐색 보류/);
  });

  it('재확인 실행에 섞인 discovery metadata는 전국 탐색 결과로 표시하지 않는다', async () => {
    show([run({ kind: 'recheck_recent', result: { discovery } })], 'runs');
    await screen.findByText(/요청 12 · 상세 8/);
    expect(screen.queryByLabelText('제목 선별과 추가 확인 범위')).toBeNull();
  });

  it.each([10, 50])('실행 기록 자체의 추가 요청 한도 %i회를 표시한다', async (limit) => {
    show([run({ result: { discovery: { ...discovery, exploration_limit: limit } } })], 'runs');
    const coverage = await screen.findByLabelText('제목 선별과 추가 확인 범위');
    expect(coverage.textContent).toContain(`이번 실행 추가 요청 한도 ${limit}회`);
    expect(coverage.textContent).toContain('다른 작업에 남길 요청 예산 30회');
    expect(coverage.textContent).not.toContain(`이번 실행 추가 요청 한도 ${limit === 10 ? 50 : 10}회`);
  });

  it('빈번한 진행 이벤트 중에도 집계·상세 결과를 조회하고 완료 뒤 빠른 조회를 멈춘다', async () => {
    vi.useFakeTimers();
    const initial = run({ state: 'running', finishedAt: null });
    initial.counts = { ...initial.counts, created: 0 };
    const records = [initial];
    const { port, qc } = show(records);
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(screen.getByLabelText('진행 중 전국 탐색').textContent).toContain('신규 저장 0건');
    const initialRequests = vi.mocked(port.listRuns).mock.calls.length;
    records[0] = run({
      state: 'running', finishedAt: null,
      counts: { ...initial.counts, created: 6, excluded: 5 },
      result: { discovery: { ...discovery, exploration_requests: 14, exploration_deferred: 37 } },
    });
    for (let step = 0; step < 4; step += 1) {
      await act(async () => {
        // Same cache-only progress patch as useLiveEvents; no counts/result payload.
        qc.setQueriesData<RunInfo[]>({ queryKey: qk.runs }, (cached) => cached?.map((record) => ({
          ...record, progress: { done: step + 1, total: 20, label: '상세 확인 중' },
        })));
        await vi.advanceTimersByTimeAsync(1000);
      });
    }
    const active = screen.getByLabelText('진행 중 전국 탐색');
    expect(vi.mocked(port.listRuns).mock.calls.length).toBeGreaterThan(initialRequests);
    expect(active.textContent).toContain('신규 저장 6건 · 자동 제외 5건 · 검토할 후보 1건');
    expect(active.textContent).toContain('추가 상세 요청 14회 · 다음 탐색 보류 37개');

    records[0] = { ...records[0]!, state: 'succeeded', finishedAt: '2026-09-30T10:02:00Z' };
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.queryByLabelText('진행 중 전국 탐색')).toBeNull();
    expect(screen.getByLabelText('최근 전국 탐색 결과').textContent).toContain('신규 저장 6건');
    const completedRequests = vi.mocked(port.listRuns).mock.calls.length;
    // The shared 10-second status fallback remains; the active-only 3-second timer stops.
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(vi.mocked(port.listRuns).mock.calls.length).toBe(completedRequests);
  });

  it('수집 패널을 닫으면 진행 중 실행의 빠른 조회를 정리한다', async () => {
    vi.useFakeTimers();
    const { port, rerender } = show([run({ state: 'running', finishedAt: null })]);
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    const requests = vi.mocked(port.listRuns).mock.calls.length;
    rerender(<CollectionPanel open={false} onClose={() => undefined} onOpenLead={() => undefined} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(vi.mocked(port.listRuns).mock.calls.length).toBe(requests);
  });

  it.each(['50', -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1, null])('잘못된 실행 한도·남길 예산 %j를 추측하지 않는다', async (value) => {
    show([run({ result: { discovery: { ...discovery, exploration_limit: value, exploration_reserve: value } } })]);
    const coverage = await screen.findByLabelText('제목 선별과 추가 확인 범위');
    expect(coverage.textContent).not.toMatch(/이번 실행 추가 요청 한도|다른 작업에 남길 요청 예산/);
  });

  it('한도가 없는 과거 기록에 현재 한도를 덧붙이지 않는다', async () => {
    show([run({ result: { discovery: { core_candidates: 12 } } })]);
    const coverage = await screen.findByLabelText('제목 선별과 추가 확인 범위');
    expect(coverage.textContent).not.toMatch(/이번 실행 추가 요청 한도|다른 작업에 남길 요청 예산/);
  });

  it.each(['sources', 'runs'] as const)('%s 화면에서 신규 저장·자동 제외·검토할 후보를 구분한다', async (tab) => {
    const original = run();
    show([run({ counts: { ...original.counts, created: 40, excluded: 37 } })], tab);
    const summary = await screen.findByLabelText('신규 저장 분류');
    expect(summary.textContent).toContain('신규 저장 40건 · 자동 제외 37건 · 검토할 후보 3건');
    expect(summary.textContent).toContain('건당 지급·원격 가능·모집 여부는 원문에서 확인하세요');
    expect(summary.textContent).not.toMatch(/유효 리드|적합한 의뢰 3/);
  });

  it('모두 자동 제외된 신규 저장은 검토 후보 0건으로 표시한다', async () => {
    const original = run();
    show([run({ counts: { ...original.counts, created: 6, excluded: 6 } })]);
    const summary = await screen.findByLabelText('신규 저장 분류');
    expect(summary.textContent).toContain('신규 저장 6건 · 자동 제외 6건 · 검토할 후보 0건');
  });

  it('제외가 신규 저장보다 큰 잘못된 집계에서 검토 후보 수를 만들지 않는다', async () => {
    const original = run();
    show([run({ counts: { ...original.counts, created: 1, excluded: 2 } })]);
    await screen.findByLabelText('최근 전국 탐색 결과');
    expect(screen.queryByLabelText('신규 저장 분류')).toBeNull();
  });

  it('다른 사이트는 자동 연결 없이 명시적 링크 클릭으로만 연다', async () => {
    show([]);
    const manual = await screen.findByRole('region', { name: '다른 외주 사이트에서 직접 찾기' });
    expect(manual.textContent).toContain('자동 수집 미연결');
    expect(manual.textContent).toContain('원격 목록에도 월급 공고가 있으므로');
    expect(openExternal).not.toHaveBeenCalled();
    const urls = [
      ['위시켓 공고 열기', 'https://www.wishket.com/project/'],
      ['프리모아 공고 열기', 'https://www.freemoa.net/m4/s41'],
      ['이랜서 턴키 공고 열기', 'https://www.elancer.co.kr/list-partner?pf=%ED%84%B4%ED%82%A4'],
    ];
    for (const [label, url] of urls) {
      const link = within(manual).getByRole('link', { name: label });
      expect(link.getAttribute('href')).toBe(url);
      fireEvent.click(link);
      expect(openExternal).toHaveBeenLastCalledWith(url);
    }
    expect(openExternal).toHaveBeenCalledTimes(3);
  });

  it.each([
    ['copied', '사이트 주소를 복사했습니다. 브라우저에 붙여 넣으세요.'],
    ['rejected', '사이트 주소를 열지 못했습니다.'],
  ] as const)('외부 링크의 %s 결과를 사용자에게 설명한다', async (result, message) => {
    vi.mocked(openExternal).mockResolvedValue(result);
    show([]);
    fireEvent.click(await screen.findByRole('link', { name: '위시켓 공고 열기' }));
    expect(await screen.findByText(message)).toBeTruthy();
  });
});
