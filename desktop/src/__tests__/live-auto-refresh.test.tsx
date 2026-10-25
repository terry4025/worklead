import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, cleanup, render, renderHook, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { qk } from '../app/queries';
import { useLiveEvents } from '../app/useLiveEvents';
import { MockPort } from '../data/mock/mockPort';
import type { RunInfo, StreamStatus, WorkleadEvent } from '../domain/model';
import { VIEWS } from '../domain/queues';
import { LeadList } from '../features/leads/LeadList';
import { DEFAULT_FILTER } from '../features/leads/FilterBar';
import { PortError } from '../data/port';

const releases: (() => void)[] = [];
afterEach(() => { cleanup(); releases.splice(0).forEach((release) => release()); vi.restoreAllMocks(); vi.useRealTimers(); });
async function tick(ms = 300) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }

function harness() {
  vi.useFakeTimers();
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  let emit!: (event: WorkleadEvent) => void;
  let status!: (state: StreamStatus) => void;
  const unsubscribe = vi.fn();
  vi.spyOn(port, 'subscribe').mockImplementation((ev, st) => { emit = ev; status = st; return unsubscribe; });
  vi.spyOn(port, 'listRuns').mockResolvedValue([]);
  const startRun = vi.spyOn(port, 'startRun');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  releases.push(() => { qc.clear(); port.dispose(); });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  const event = (patch: object) => emit({ id: 'event', seq: 1, at: new Date().toISOString(), ...patch } as WorkleadEvent);
  return { port, qc, wrapper, event, status: (s: StreamStatus) => status(s), unsubscribe, startRun };
}

it('새 리드 연속 이벤트는 F5 없이 한 번에 반영하고 비활성 필터도 오래된 것으로 표시한다', async () => {
  const h = harness();
  let rows = ['old'];
  const request = vi.fn(async () => rows);
  h.qc.setQueryData(['leads', 'inactive'], ['cached']);
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: rows }), { wrapper: h.wrapper });
  const live = renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  await tick(10);
  rows = ['newest', 'old'];
  act(() => { for (let i = 0; i < 40; i++) h.event({ type: 'lead.created', leadId: `new-${i}`, recommendation: 'needs_review' }); });
  expect(live.result.current.listStale).toBe(true);
  await tick();
  expect(request).toHaveBeenCalledOnce();
  expect(list.result.current.data).toEqual(rows);
  expect(h.qc.getQueryState(['leads', 'inactive'])?.isInvalidated).toBe(true);
  expect(live.result.current.listStale).toBe(false);
  expect(live.result.current.newLeads).toBe(0);
});

it.each(['lead.updated', 'analysis.completed'])('%s로 점수·소속 대기열이 바뀌면 활성 목록을 자동 재조회한다', async (type) => {
  const h = harness();
  const request = vi.fn(async () => ['moved-out']);
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'needs_review'], queryFn: request, initialData: ['before'] }), { wrapper: h.wrapper });
  renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  act(() => h.event({ type, leadId: 'changed' }));
  await tick();
  expect(list.result.current.data).toEqual(['moved-out']);
});

it.each(['succeeded', 'partial', 'failed', 'cancelled'])('탐색 %s 때 저장된 부분 결과도 자동 반영한다', async (state) => {
  const h = harness();
  const request = vi.fn(async () => ['saved-result']);
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: ['before'] }), { wrapper: h.wrapper });
  renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  act(() => h.event({ type: 'run.state_changed', runId: 'scan', state, kind: 'discovery', sourceId: 'daangn-alba' }));
  await tick();
  expect(list.result.current.data).toEqual(['saved-result']);
});

it('완료 이벤트를 놓쳐도 실행 상태 폴링의 변경으로 목록을 갱신한다', async () => {
  const h = harness();
  const request = vi.fn(async () => ['found-by-poll']);
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: ['before'] }), { wrapper: h.wrapper });
  renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  await tick(10);
  act(() => h.qc.setQueryData([...qk.runs, 'status'], [{ id: 'scan', state: 'partial' }] as RunInfo[]));
  await tick(10); await tick();
  expect(list.result.current.data).toEqual(['found-by-poll']);
});

it('연결 복구와 끊긴 동안 30초 로컬 조회로 누락을 보완한다', async () => {
  const h = harness();
  let rows = ['first'];
  const request = vi.fn(async () => rows);
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: ['before'] }), { wrapper: h.wrapper });
  renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  act(() => h.status('retrying'));
  await tick(); expect(list.result.current.data).toEqual(['first']);
  rows = ['second']; await tick(30_100);
  expect(list.result.current.data).toEqual(['second']);
  rows = ['reconnected']; act(() => h.status('open')); await tick();
  expect(list.result.current.data).toEqual(['reconnected']);
  expect(h.startRun).not.toHaveBeenCalled();
});

it('조회 중 도착한 이벤트는 완료 후 다시 조회하고 이전 완료로 최신 표시를 지우지 않는다', async () => {
  const h = harness();
  let finish!: (rows: string[]) => void;
  const request = vi.fn().mockImplementationOnce(() => new Promise<string[]>((resolve) => { finish = resolve; })).mockResolvedValue(['latest']);
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: ['before'] }), { wrapper: h.wrapper });
  const live = renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  act(() => h.event({ type: 'lead.created', leadId: 'first', recommendation: 'recommended' })); await tick();
  act(() => h.event({ type: 'lead.created', leadId: 'second', recommendation: 'recommended' }));
  await act(async () => finish(['older-response']));
  expect(live.result.current.listStale).toBe(true);
  await tick();
  expect(request).toHaveBeenCalledTimes(2);
  expect(list.result.current.data).toEqual(['latest']);
  expect(live.result.current.listStale).toBe(false);
});

it('일시 오류에도 기존 목록을 보존하고 수동 입력 없이 재시도한다', async () => {
  const h = harness();
  const request = vi.fn().mockRejectedValueOnce(new Error('temporary')).mockResolvedValue(['recovered']);
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: ['before'] }), { wrapper: h.wrapper });
  const live = renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  act(() => h.event({ type: 'run.progress', runId: 'scan', progress: { done: 1, total: 2, label: null } }));
  await tick();
  expect(list.result.current.data).toEqual(['before']); expect(live.result.current.listStale).toBe(true);
  await tick(30_100);
  expect(list.result.current.data).toEqual(['recovered']); expect(live.result.current.listStale).toBe(false);
});

it('화면 종료 시 예약된 자동 조회와 구독을 정리한다', async () => {
  const h = harness();
  const request = vi.fn(async () => ['after']);
  renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: ['before'] }), { wrapper: h.wrapper });
  const live = renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  act(() => h.event({ type: 'lead.created', leadId: 'new', recommendation: 'recommended' }));
  live.unmount(); await tick(31_000);
  expect(request).not.toHaveBeenCalled(); expect(h.unsubscribe).toHaveBeenCalledOnce();
});

it('자동 갱신은 이미 진행 중인 느린 목록 조회를 취소하고 다시 시작하지 않는다', async () => {
  const h = harness();
  let finish!: (rows: string[]) => void;
  let aborted = 0;
  const request = vi.fn(({ signal }: { signal: AbortSignal }) => {
    signal.addEventListener('abort', () => { aborted += 1; });
    return new Promise<string[]>((resolve) => { finish = resolve; });
  });
  const list = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request, initialData: ['cached'] }), { wrapper: h.wrapper });
  const live = renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  act(() => { void h.qc.refetchQueries({ queryKey: ['leads', 'all'] }); });
  await tick(10);
  act(() => h.event({ type: 'run.progress', runId: 'scan', progress: { done: 1, total: 5, label: null } }));
  await tick();
  expect(request).toHaveBeenCalledOnce(); expect(aborted).toBe(0);
  await act(async () => finish(['latest'])); await tick(10);
  expect(list.result.current.data).toEqual(['latest']); expect(live.result.current.listStale).toBe(false);
});

it('늦게 재생된 진행 이벤트로 이미 완료한 실행이 계속 실행 중으로 되돌아가지 않는다', async () => {
  const h = harness();
  renderHook(() => useLiveEvents(() => undefined), { wrapper: h.wrapper });
  await tick(10);
  act(() => h.qc.setQueryData([...qk.runs, 'status'], [{ id: 'finished', state: 'succeeded', progress: { done: 5, total: 5, label: null } }] as RunInfo[]));
  await tick(10);
  act(() => h.event({ type: 'run.progress', runId: 'finished', progress: { done: 1, total: 5, label: null } }));
  expect(h.qc.getQueryData<RunInfo[]>([...qk.runs, 'status'])?.[0]?.state).toBe('succeeded');
});

it('한 대기열이 지연되고 다른 대기열이 실패해도 로딩 화면으로 오류를 가리지 않으며 자동 복구한다', async () => {
  const h = harness();
  let recovered = false;
  vi.spyOn(h.port, 'listLeads').mockImplementation(async ({ queue }) => {
    if (recovered) return { items: [], hasMore: false, nextCursor: null };
    if (queue === 'recommended') return new Promise((_resolve, reject) => setTimeout(() => reject(new PortError({ kind: 'connection', code: 'request_timeout', message: '느린 조회 시간 초과', retryable: true })), 15_000));
    throw new PortError({ kind: 'connection', code: 'request_timeout', message: '목록 조회 응답이 늦어 다시 확인합니다', retryable: true });
  });
  render(<LeadList view={VIEWS.find(view => view.id === 'review')!} filter={DEFAULT_FILTER} counts={null} selectedId={null} onSelect={() => undefined} onOpen={() => undefined} onSnapshot={() => undefined} categoryLabel={x => x} empty={<p>자동 복구 후 빈 목록</p>} now={new Date()} />, { wrapper: h.wrapper });
  await tick(10);
  expect(screen.getByRole('alert').textContent).toContain('request_timeout');
  expect(document.querySelector('.skeleton-list')).toBeNull();
  recovered = true;
  // The slow queue times out at 15 seconds and both queries recover through
  // their own retry timers. No manual refresh/cancellation is involved.
  await tick(46_000);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText('자동 복구 후 빈 목록')).toBeTruthy();
});
