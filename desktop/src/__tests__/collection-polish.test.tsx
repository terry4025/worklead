import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { loadRunStatus, qk } from '../app/queries';
import { useLiveEvents } from '../app/useLiveEvents';
import { MockPort } from '../data/mock/mockPort';
import { mapEvent } from '../data/live/mapper';
import type { WorkleadPort } from '../data/port';
import { emptyCollectionMessage } from '../domain/collection';
import type { RunInfo, StreamStatus, WorkleadEvent } from '../domain/model';
import { CollectionPanel } from '../features/collection/CollectionPanel';
import { deriveIssues } from '../features/header/IssueStrip';

const port = () => new MockPort({ scenario: 'normal', latency: [0, 0] });

it('자동 제외 공고는 새 리드 알림 숫자에 포함하지 않는다', () => {
  const p = port();
  let event!: (e: WorkleadEvent) => void;
  vi.spyOn(p, 'subscribe').mockImplementation((ev) => { event = ev; return () => undefined; });
  const { qc, wrapper } = harness(p);
  const hook = renderHook(() => useLiveEvents(() => undefined), { wrapper });
  for (const recommendation of ['excluded', 'recommended', 'needs_review'] as const) {
    const mapped = mapEvent({ id: recommendation, seq: 1, at: new Date().toISOString(), type: 'lead.created', lead_id: recommendation, recommendation });
    expect(mapped?.type).toBe('lead.created');
    act(() => event(mapped!));
    if (recommendation === 'excluded') expect(hook.result.current.newLeads).toBe(0);
  }
  expect(hook.result.current.newLeads).toBe(2);
  hook.unmount(); qc.clear();
});
const run = (patch: Partial<RunInfo> = {}): RunInfo => ({
  id: 'older-discovery', sourceId: 'daangn-alba', leadId: null, kind: 'discovery', state: 'partial', trigger: 'schedule',
  startedAt: '2026-09-28T03:00:00Z', finishedAt: '2026-09-28T03:01:00Z', progress: { done: 1, total: 1, label: null },
  counts: { requests: 8, detailsFetched: 4, created: 4, updated: 0, duplicates: 41, excluded: 3, skipped: 0, parseFailures: 0, fetchFailures: 2, policyStops: 0, aiFailures: 0 },
  error: null, note: null, scanCycle: 1, result: {}, ...patch,
});
function harness(p: WorkleadPort) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port: p, mode: 'live', scenario: null, setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  return { qc, wrapper };
}
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal'); });

describe('수집 상태 가시성', () => {
  it('개별 작업 20개 밖에 있는 마지막 탐색과 진행 작업을 별도 조회한다', async () => {
    const p = port();
    const pending = run({ id: 'pending-old', kind: 'recheck_recent', state: 'queued', finishedAt: null });
    const list = vi.spyOn(p, 'listRuns').mockImplementation(async (req) => req.kinds ? [run()] : [pending]);
    const result = await loadRunStatus(p);
    expect(result.map((r) => r.id)).toEqual(['pending-old', 'older-discovery']);
    expect(list).toHaveBeenCalledWith({ states: ['queued', 'running', 'paused'], limit: 100 });
    expect(list).toHaveBeenCalledWith({ kinds: ['discovery'], states: ['succeeded', 'partial', 'failed', 'cancelled'], limit: 10 });
    const issues = deriveIssues({ sources: [], runs: result, stream: 'open', mode: 'live', openCollection: () => undefined, now: new Date() });
    expect(issues.some((i) => i.text.includes('부분 완료'))).toBe(true);
  });

  it('탐색 전·실패·부분 완료를 정상 완료로 표시하지 않는다', async () => {
    const site = (await port().listSources()).find((s) => s.kind === 'site')!;
    site.policy.status = 'allowed'; site.research.ready = true; site.stoppedReason = null;
    expect(emptyCollectionMessage(site, [])).toContain('아직 탐색 결과가 없습니다');
    for (const state of ['failed', 'partial', 'cancelled'] as const) {
      expect(emptyCollectionMessage(site, [run({ sourceId: site.id, state })])).toContain('끝까지 완료되지 않았습니다');
    }
    expect(emptyCollectionMessage(site, [run({ sourceId: site.id, state: 'succeeded' })])).toContain('탐색은 완료');
  });
});

describe('연결 복구', () => {
  it('끊긴 뒤 다시 연결되면 상태를 재조회하고 읽던 목록의 순서를 유지한다', async () => {
    const p = port();
    let status!: (s: StreamStatus) => void;
    let event!: (e: WorkleadEvent) => void;
    const unsubscribe = vi.fn();
    vi.spyOn(p, 'subscribe').mockImplementation((ev, st) => { event = ev; status = st; return unsubscribe; });
    const { qc, wrapper } = harness(p);
    qc.setQueryData(['leads', 'review'], ['reading-first', 'reading-second']);
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const refetch = vi.spyOn(qc, 'refetchQueries');
    const hook = renderHook(() => useLiveEvents(() => undefined), { wrapper });
    act(() => status('open'));
    expect(hook.result.current.listStale).toBe(false);
    act(() => { status('retrying'); status('open'); });
    expect(hook.result.current.listStale).toBe(true);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.sources });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.runs });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['lead'] });
    expect(qc.getQueryData(['leads', 'review'])).toEqual(['reading-first', 'reading-second']);
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: qk.leadsAll });
    act(() => event({ id: 'reset', seq: 1, at: new Date().toISOString(), type: 'stream.reset' }));
    await act(async () => { await hook.result.current.acknowledge(); });
    expect(refetch).toHaveBeenCalledWith({ queryKey: qk.leadsAll, type: 'active' }, { throwOnError: true });
    hook.unmount();
    expect(unsubscribe).toHaveBeenCalledOnce();
    qc.clear();
  });

  it('목록 재조회가 실패하면 새 결과 알림을 유지하고 성공한 뒤에만 지운다', async () => {
    const p = port();
    let status!: (s: StreamStatus) => void;
    vi.spyOn(p, 'subscribe').mockImplementation((_ev, st) => { status = st; return () => undefined; });
    const { qc, wrapper } = harness(p);
    const request = vi.fn().mockRejectedValueOnce(new Error('temporary')).mockResolvedValue(['new']);
    const list = renderHook(() => useQuery({ queryKey: ['leads', 'review'], queryFn: request, initialData: ['old'], staleTime: Infinity }), { wrapper });
    const hook = renderHook(() => useLiveEvents(() => undefined), { wrapper });
    act(() => { status('retrying'); status('open'); });
    expect(hook.result.current.listStale).toBe(true);
    let refreshed = true;
    await act(async () => { refreshed = await hook.result.current.acknowledge(); });
    expect(refreshed).toBe(false);
    expect(hook.result.current.listStale).toBe(true);
    expect(list.result.current.data).toEqual(['old']);
    await act(async () => { refreshed = await hook.result.current.acknowledge(); });
    expect(refreshed).toBe(true);
    expect(hook.result.current.listStale).toBe(false);
    await waitFor(() => expect(list.result.current.data).toEqual(['new']));
    hook.unmount(); list.unmount(); qc.clear();
  });
});

it('수동 입력이 5초 넘게 걸려도 완료까지 확인하고 중복 제출을 막는다', async () => {
  vi.useFakeTimers();
  // Native dialog is verified in the browser; jsdom only exercises data flow.
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  const p = port();
  vi.spyOn(p, 'importManual').mockResolvedValue({ jobId: 'slow-import', runId: 'slow-import' });
  let finished = false;
  const get = vi.spyOn(p, 'getRun').mockImplementation(async () => run({ id: 'slow-import', kind: 'import', state: finished ? 'succeeded' : 'running', result: finished ? { lead_ids: ['new-lead'] } : {} }));
  const { qc, wrapper } = harness(p);
  const openLead = vi.fn();
  const view = render(<CollectionPanel open onClose={() => undefined} initialTab="import" onOpenLead={openLead} />, { wrapper });
  fireEvent.change(screen.getByLabelText('입력 내용'), { target: { value: '홈페이지 제작 의뢰\n원격으로 제작해주세요' } });
  fireEvent.click(screen.getByRole('button', { name: '분석에 추가' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect((screen.getByRole('button', { name: '분석에 추가' }) as HTMLButtonElement).disabled).toBe(true);
  const before = get.mock.calls.length;
  finished = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
  expect(get.mock.calls.length).toBeGreaterThan(before);
  fireEvent.click(screen.getByRole('button', { name: '리드 보기' }));
  expect(openLead).toHaveBeenCalledWith('new-lead');
  expect(p.importManual).toHaveBeenCalledOnce();
  view.unmount(); qc.clear();
});
