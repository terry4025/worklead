import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { MockPort } from '../data/mock/mockPort';
import type { RunInfo } from '../domain/model';
import { CollectionPanel } from '../features/collection/CollectionPanel';
import { openExternal } from '../platform/external';

vi.mock('../platform/external', async (original) => ({
  ...await original<typeof import('../platform/external')>(),
  openExternal: vi.fn().mockResolvedValue('opened'),
}));

const run = (patch: Partial<RunInfo> = {}): RunInfo => ({
  id: 'discovery-completed', sourceId: 'daangn-alba', leadId: null, kind: 'discovery', state: 'partial', trigger: 'schedule',
  startedAt: '2026-09-30T01:00:00Z', finishedAt: '2026-09-30T01:05:00Z', progress: { done: 1, total: 1, label: null },
  counts: { requests: 4, detailsFetched: 2, created: 0, updated: 0, duplicates: 3, excluded: 0, skipped: 1, parseFailures: 0, fetchFailures: 1, policyStops: 0, aiFailures: 0 },
  error: null, note: null, scanCycle: 1, result: {}, ...patch,
});

const issue = (patch: Record<string, unknown> = {}) => ({
  source_post_id: 'post-1', url: 'https://www.daangn.com/kr/jobs/example-post-1/', title: '홈페이지 제작 의뢰',
  code: 'http_404', message: '공고를 찾을 수 없습니다.', http_status: 404, retry_at: '2026-10-01T01:05:00Z', ...patch,
});

const clients: QueryClient[] = [];
function show(records: RunInfo[], tab: 'sources' | 'runs' = 'runs') {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  vi.spyOn(port, 'listRuns').mockImplementation(async (request) => records.filter((r) =>
    (!request.kinds || request.kinds.includes(r.kind)) && (!request.states || request.states.includes(r.state)),
  ));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(qc);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}>{children}</PortProvider>
    </QueryClientProvider>
  );
  return render(<CollectionPanel open onClose={() => undefined} initialTab={tab} onOpenLead={() => undefined} />, { wrapper });
}

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => {
  for (const qc of clients.splice(0)) qc.clear();
  vi.restoreAllMocks();
  vi.mocked(openExternal).mockClear();
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
});

describe('공고별 수집 진행과 오류 기록', () => {
  it('실행 기록에 상세 진행·유예·개별 오류와 안전한 원문 열기를 표시한다', async () => {
    show([run({ result: { detail_progress: { done: 8, total: 10, title: '현재 확인 중인 공고' }, deferred_details: 2, detail_issues: [issue()] } })]);
    expect(await screen.findByText('상세 후보 진행 8/10 · 현재 확인 중인 공고')).toBeTruthy();
    expect(screen.getByLabelText('상세 후보 진행 8/10').firstElementChild?.getAttribute('style')).toContain('80%');
    expect(screen.getByText(/재조회 유예 2건/)).toBeTruthy();
    const summary = screen.getByText('공고별 오류·수집 제외 1건');
    (summary.parentElement as HTMLDetailsElement).open = true;
    expect(screen.getByText(/공고를 찾을 수 없습니다.*HTTP 404/)).toBeTruthy();
    expect(screen.getByText(/10월 1일 10:05 이후 재시도/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '원문 열기' }));
    expect(openExternal).toHaveBeenCalledWith('https://www.daangn.com/kr/jobs/example-post-1/');
  });

  it('소스 화면에서 최근 탐색과 진행 중 탐색을 각각 표시한다', async () => {
    show([
      run({ result: { detail_progress: { done: 10, total: 10 }, deferred_details: 1, detail_issues: [issue({ code: 'collection_disallowed', message: '공개 수집 대상에서 제외된 공고입니다.', http_status: null, retry_at: null })] } }),
      run({ id: 'discovery-active', state: 'running', finishedAt: null, result: { detail_progress: { done: 3, total: 12, title: '새 후보' } } }),
    ], 'sources');
    const recent = await screen.findByLabelText('최근 전국 탐색 결과');
    expect(within(recent).getByText('상세 후보 진행 10/10')).toBeTruthy();
    expect(within(recent).getByText(/재조회 유예 1건/)).toBeTruthy();
    expect(within(recent).getByText(/공개 수집 대상에서 제외/)).toBeTruthy();
    const active = screen.getByLabelText('진행 중 전국 탐색');
    expect(within(active).getByText('상세 후보 진행 3/12 · 새 후보')).toBeTruthy();
  });

  it('수치·날짜·URL이 잘못된 결과를 안전하게 처리하고 유효한 이유는 남긴다', async () => {
    show([run({ progress: { done: 1, total: 1, label: '상세 확인 중' }, result: {
      detail_progress: { done: 'all', total: -2, title: {} }, deferred_details: -4,
      detail_issues: [null, [], 'invalid', { code: {}, message: 'invalid' }, issue({ url: 'javascript:alert(1)', retry_at: 'invalid-date', http_status: 999 })],
    } })]);
    expect(await screen.findByText('상세 확인 중')).toBeTruthy();
    expect(screen.queryByText(/상세 후보 진행/)).toBeNull();
    expect(screen.queryByText(/재조회 유예/)).toBeNull();
    expect(screen.getByText('공고별 오류·수집 제외 1건')).toBeTruthy();
    expect(screen.getByText('공고를 찾을 수 없습니다. · http_404')).toBeTruthy();
    expect(screen.queryByText(/이후 재시도/)).toBeNull();
    expect(screen.queryByRole('button', { name: '원문 열기', hidden: true })).toBeNull();
  });

  it('상세 오류는 최대 50건을 표시하고 진행률이 100%를 넘지 않는다', async () => {
    show([run({ result: { detail_progress: { done: 20, total: 10 }, detail_issues: Array.from({ length: 51 }, (_, i) => issue({ title: `오류 공고 ${i + 1}` })) } })]);
    expect(await screen.findByText('상세 후보 진행 10/10')).toBeTruthy();
    expect(screen.getByLabelText('상세 후보 진행 10/10').firstElementChild?.getAttribute('style')).toContain('100%');
    expect(screen.getByText('공고별 오류·수집 제외 50건')).toBeTruthy();
    expect(screen.getByText('오류 공고 50')).toBeTruthy();
    expect(screen.queryByText('오류 공고 51')).toBeNull();
  });

  it('이전 실행에 상세 결과가 없거나 결과가 null이어도 기록을 표시한다', async () => {
    show([run({ result: null as unknown as RunInfo['result'], progress: { done: 1, total: 1, label: '전국 목록 확인' } })]);
    expect(await screen.findByText('전국 목록 확인')).toBeTruthy();
    expect(screen.queryByText(/공고별 오류·수집 제외/)).toBeNull();
  });
});
