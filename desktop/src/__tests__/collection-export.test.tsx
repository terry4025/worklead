import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetDatabaseQueries } from '../app/databaseCache';
import { PortProvider } from '../app/PortContext';
import { MockPort } from '../data/mock/mockPort';
import { PortError } from '../data/port';
import type { JobAccepted, RunInfo } from '../domain/model';
import { CollectionPanel } from '../features/collection/CollectionPanel';
import { ToastProvider } from '../ui/Toasts';

const activeStates = ['queued', 'running', 'paused'];
const fileName = 'worklead-leads-owned-export.json';
const clients: QueryClient[] = [];

function makeRun(state: RunInfo['state'] = 'running', result: Record<string, unknown> = {}): RunInfo {
  return {
    id: 'owned-export', sourceId: null, leadId: null, kind: 'export', state, trigger: 'manual',
    startedAt: '2026-09-30T09:00:00Z', finishedAt: activeStates.includes(state) ? null : '2026-09-30T09:00:12Z',
    progress: { done: 1, total: 2, label: '내보내기 1/2건' },
    counts: { requests: 0, detailsFetched: 0, created: 0, updated: 0, duplicates: 0, excluded: 0, skipped: 0, parseFailures: 0, fetchFailures: 0, policyStops: 0, aiFailures: 0 },
    error: null, note: null, scanCycle: null, result,
  };
}

function harness(records: () => RunInfo[] = () => [], configure?: (port: MockPort) => void) {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const list = vi.spyOn(port, 'listRuns').mockImplementation(async (request) => records().filter((run) =>
    (!request.kinds || request.kinds.includes(run.kind)) && (!request.states || request.states.includes(run.state)),
  ));
  const start = vi.spyOn(port, 'exportData').mockResolvedValue({ jobId: 'owned-export', runId: 'owned-export' });
  const get = vi.spyOn(port, 'getRun').mockImplementation(async (id) => {
    const found = records().find((run) => run.id === id);
    if (!found) throw new PortError({ kind: 'api', code: 'not_found', message: '실행을 찾을 수 없습니다' });
    return found;
  });
  configure?.(port);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  clients.push(qc);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}>
        <ToastProvider>{children}</ToastProvider>
      </PortProvider>
    </QueryClientProvider>
  );
  const view = render(<CollectionPanel open onClose={() => undefined} initialTab="export" onOpenLead={() => undefined} />, { wrapper });
  return { port, start, list, get, qc, view };
}

async function tick(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function elapse(ms: number) {
  for (let elapsed = 0; elapsed < ms; elapsed += 250) await tick(Math.min(250, ms - elapsed));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-30T09:00:00Z'));
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

describe('비동기 내보내기 완료와 복구', () => {
  it('8초를 넘긴 작업을 실패로 바꾸지 않고 12초 뒤 파일을 표시한다', async () => {
    let accepted = false;
    const startedAt = Date.now();
    const { start, get } = harness(() => accepted ? [makeRun(Date.now() - startedAt >= 12_000 ? 'succeeded' : 'running', { file_name: fileName, rows: 2 })] : []);
    start.mockImplementation(async () => { accepted = true; return { jobId: 'owned-export', runId: 'owned-export' }; });
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await tick();
    await elapse(9000);
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText(/내보내기가 오래 걸립니다/)).toBeNull();
    await elapse(4000);
    expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
    expect(start).toHaveBeenCalledOnce();
    const reads = get.mock.calls.length;
    await elapse(2500);
    expect(get.mock.calls.length).toBe(reads);
  });

  it('상태 조회 연결 오류가 나도 작업을 재제출하지 않고 같은 ID로 다시 조회한다', async () => {
    let accepted = false;
    let complete = false;
    const { start, get } = harness(() => accepted ? [makeRun(complete ? 'succeeded' : 'running', { file_name: fileName })] : []);
    start.mockImplementation(async () => { accepted = true; return { jobId: 'owned-export', runId: 'owned-export' }; });
    get.mockRejectedValueOnce(new PortError({ kind: 'connection', code: 'connection_failed', message: '일시적인 연결 오류' }));
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await tick();
    await elapse(1000);
    expect(screen.getByText(/상태를 확인하지 못했습니다/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    complete = true;
    fireEvent.click(screen.getByRole('button', { name: '상태 다시 확인' }));
    await tick();
    expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
    expect(start).toHaveBeenCalledOnce();
    expect(get.mock.calls.every(([id]) => id === 'owned-export')).toBe(true);
  });

  it.each(['cancelled', 'partial', 'failed'] as const)('%s 상태를 즉시 종료 상태로 표시한다', async (state) => {
    let accepted = false;
    const record = makeRun(state, state === 'partial' ? { file_name: fileName } : {});
    if (state === 'failed') record.error = { code: 'export_write_failed', message: '파일을 저장하지 못했습니다', retryAfter: null };
    const { start } = harness(() => accepted ? [record] : []);
    start.mockImplementation(async () => { accepted = true; return { jobId: record.id, runId: record.id }; });
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await elapse(1500);
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(false);
    if (state === 'partial') {
      expect(screen.getByText(/부분 완료/)).toBeTruthy();
      expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
    } else if (state === 'cancelled') expect(screen.getByText(/내보내기가 취소되었습니다/)).toBeTruthy();
    else expect(screen.getByText(/파일을 저장하지 못했습니다/)).toBeTruthy();
  });

  it('탭 전환과 창 재열기 후 진행 중 작업을 유지한다', async () => {
    let accepted = false;
    let complete = false;
    const { start, view } = harness(() => accepted ? [makeRun(complete ? 'succeeded' : 'running', { file_name: fileName })] : []);
    start.mockImplementation(async () => { accepted = true; return { jobId: 'owned-export', runId: 'owned-export' }; });
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await elapse(500);
    fireEvent.click(screen.getByRole('tab', { name: '실행 기록' }));
    await tick();
    fireEvent.click(screen.getByRole('tab', { name: '내보내기' }));
    await tick();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    view.rerender(<CollectionPanel open={false} onClose={() => undefined} initialTab="export" onOpenLead={() => undefined} />);
    await tick();
    view.rerender(<CollectionPanel open onClose={() => undefined} initialTab="export" onOpenLead={() => undefined} />);
    await tick();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    complete = true;
    await elapse(1500);
    expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
    expect(start).toHaveBeenCalledOnce();
  });

  it('다른 화면에서 시작한 기존 활성 내보내기를 발견하면 새 요청을 막는다', async () => {
    const { start, list } = harness(() => [makeRun('paused')]);
    await tick();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/일시정지/)).toBeTruthy();
    expect(list).toHaveBeenCalledWith({ kinds: ['export'], states: ['queued', 'running', 'paused'], limit: 100 });
    expect(start).not.toHaveBeenCalled();
  });

  it('실행 기록에서 내보내기만 따로 조회하고 완료 파일명을 표시한다', async () => {
    const { list } = harness(() => [makeRun('succeeded', { file_name: fileName, rows: 0 })]);
    await tick();
    fireEvent.click(screen.getByRole('tab', { name: '실행 기록' }));
    await tick();
    fireEvent.change(screen.getByRole('combobox', { name: '실행 기록 종류' }), { target: { value: 'export' } });
    await tick();
    expect(list).toHaveBeenCalledWith({ limit: 20, kinds: ['export'] });
    expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
    expect(screen.getByText(/내보낸 리드 0건/)).toBeTruthy();
  });

  it('복원 전에 보낸 요청의 늦은 accepted 응답을 새 데이터 작업으로 표시하지 않는다', async () => {
    let finish!: (value: JobAccepted) => void;
    const { start, get, qc } = harness();
    start.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await tick();
    await act(async () => { await resetDatabaseQueries(qc); });
    await tick();
    await act(async () => { finish({ jobId: 'pre-restore-export', runId: 'pre-restore-export' }); });
    await elapse(1500);
    expect(get).not.toHaveBeenCalledWith('pre-restore-export');
    expect(screen.queryByText(/저장됨/)).toBeNull();
  });

  it('시작 직전 다른 활성 작업이 발견되면 그 작업을 조회하고 POST를 하지 않는다', async () => {
    let active = false;
    const { start, get } = harness(() => active ? [makeRun('running')] : []);
    await tick();
    active = true;
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await tick();
    expect(start).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledWith('owned-export');
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('연속 클릭은 한 번만 제출하고 요청 중에는 이전 완료 파일을 숨긴다', async () => {
    let finish!: (value: JobAccepted) => void;
    const { start } = harness(() => [makeRun('succeeded', { file_name: fileName })]);
    start.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    await tick();
    expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
    const button = screen.getByRole('button', { name: '리드 JSON' });
    fireEvent.click(button);
    fireEvent.click(button);
    await tick();
    expect(start).toHaveBeenCalledOnce();
    expect(screen.queryByText(`저장됨: ${fileName}`)).toBeNull();
    await act(async () => { finish({ jobId: 'owned-export', runId: 'owned-export' }); });
  });

  it('새 시작 요청이 실패한 뒤 이전 완료 파일을 이번 요청 결과로 표시하지 않는다', async () => {
    const { start } = harness(() => [makeRun('succeeded', { file_name: fileName })]);
    start.mockRejectedValue(new PortError({ kind: 'api', code: 'export_rejected', message: '내보내기 요청 거절' }));
    await tick();
    expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await tick();
    expect(screen.getByText(/내보내기를 요청하지 못했습니다 — 내보내기 요청 거절/)).toBeTruthy();
    expect(screen.queryByText(`저장됨: ${fileName}`)).toBeNull();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('시작 요청 응답이 유실되면 자동 재제출 없이 기록을 조회해 진행 작업을 복구한다', async () => {
    let active = false;
    const { start } = harness(() => active ? [makeRun('running')] : []);
    start.mockRejectedValue(new PortError({ kind: 'connection', code: 'response_lost', message: '요청 응답 유실' }));
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '리드 JSON' }));
    await tick();
    expect(screen.getByText(/내보내기 요청 응답을 확인하지 못했습니다/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    await elapse(2000);
    expect(start).toHaveBeenCalledOnce();
    active = true;
    fireEvent.click(screen.getByRole('button', { name: '내보내기 실행 기록' }));
    await tick();
    expect((screen.getByRole('combobox', { name: '실행 기록 종류' }) as HTMLSelectElement).value).toBe('export');
    fireEvent.click(screen.getByRole('tab', { name: '내보내기' }));
    await tick();
    expect(screen.queryByText(/내보내기 요청 응답을 확인하지 못했습니다/)).toBeNull();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    expect(start).toHaveBeenCalledOnce();
  });

  it('기존 작업 조회가 실패하면 새 제출을 막고 조회 재시도만 한다', async () => {
    const { start } = harness(() => [], (port) => {
      vi.mocked(port.listRuns).mockRejectedValueOnce(new PortError({ kind: 'connection', code: 'connection_failed', message: '기록 조회 연결 오류' }));
    });
    await tick();
    expect(screen.getByText(/상태를 확인하지 못했습니다/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '상태 다시 확인' }));
    await tick();
    expect((screen.getByRole('button', { name: '리드 JSON' }) as HTMLButtonElement).disabled).toBe(false);
    expect(start).not.toHaveBeenCalled();
  });

  it('복원 전 상태 조회의 늦은 완료 파일을 표시하지 않는다', async () => {
    let finish!: (value: RunInfo) => void;
    let beforeRestore = true;
    const { get, qc } = harness(() => beforeRestore ? [makeRun('running')] : []);
    get.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    await tick();
    beforeRestore = false;
    await act(async () => { await resetDatabaseQueries(qc); });
    await tick();
    await act(async () => { finish(makeRun('succeeded', { file_name: 'pre-restore-file.json' })); });
    await tick();
    expect(screen.queryByText('저장됨: pre-restore-file.json')).toBeNull();
  });

  it.each(['', { invalid: true }, '../outside.json'])('저장 파일명이 잘못된 완료 결과(%j)를 성공 파일처럼 표시하지 않는다', async (invalid) => {
    harness(() => [makeRun('succeeded', { file_name: invalid })]);
    await tick();
    expect(screen.getByText(/완료 기록에 저장 파일명이 없습니다/)).toBeTruthy();
    expect(screen.queryByText(/^저장됨:/)).toBeNull();
  });

  it('부분 완료 사유와 보존된 완료 파일을 함께 표시한다', async () => {
    const record = makeRun('partial', { file_name: fileName });
    record.error = { code: 'partial_export', message: '일부 항목을 내보내지 못했습니다', retryAfter: null };
    harness(() => [record]);
    await tick();
    expect(screen.getByText(/부분 완료 — 일부 항목을 내보내지 못했습니다/)).toBeTruthy();
    expect(screen.getByText(`저장됨: ${fileName}`)).toBeTruthy();
  });
});
