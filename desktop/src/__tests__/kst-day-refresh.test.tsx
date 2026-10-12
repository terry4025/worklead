import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { qk } from '../app/queries';
import { Workspace } from '../app/Workspace';
import { mapDetail } from '../data/live/mapper';
import { MockPort } from '../data/mock/mockPort';
import type { LeadDetail, QueueId } from '../domain/model';
import { inQueue } from '../domain/queues';
import { toSummary } from '../domain/summary';
import { DEFAULT_FILTER } from '../features/leads/FilterBar';
import { ToastProvider } from '../ui/Toasts';

const clients: QueryClient[] = [];
const ports: MockPort[] = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  ports.splice(0).forEach((port) => port.dispose());
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  // React Query batches observer notifications separately from the minute clock.
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
}

async function harness(at: string) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(at));
  window.localStorage.clear();
  const input = JSON.parse(readFileSync(join(__dirname, '../../../contracts/fixtures/lead-details/01.json'), 'utf8'));
  let current: LeadDetail = {
    ...mapDetail(input), id: 'kst-deadline', title: '자정 마감 웹사이트 제작', memo: '저장된 메모',
    deadline: { at: '2026-09-29T15:00:00Z', precision: 'day', raw: '2026년 9월 30일까지' },
    draft: { text: '저장된 초안', editedByUser: true, generatedAt: null },
  };
  const port = new MockPort({ scenario: 'normal', initialLeads: [current], latency: [0, 0] });
  ports.push(port);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  clients.push(client);
  const queues: QueueId[] = ['recommended', 'needs_review', 'interested', 'active', 'dismissed', 'auto_excluded', 'all'];
  const counts = vi.spyOn(port, 'countQueues').mockImplementation(async () => Object.fromEntries(queues.map((q) => [q, Number(inQueue(current, q))])) as Record<QueueId, number>);
  const list = vi.spyOn(port, 'listLeads').mockImplementation(async ({ queue }) => ({ items: inQueue(current, queue) ? [toSummary(current)] : [], hasMore: false, nextCursor: null }));
  const detail = vi.spyOn(port, 'getLead').mockImplementation(async () => current);
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const startRun = vi.spyOn(port, 'startRun');
  const recheck = vi.spyOn(port, 'recheckLead');
  const update = vi.spyOn(port, 'updateLead');
  render(<StrictMode><QueryClientProvider client={client}><PortProvider value={{ port, mode: 'demo', scenario: 'normal', setScenario: () => undefined }}><ToastProvider><Workspace /></ToastProvider></PortProvider></QueryClientProvider></StrictMode>);
  await advance(20);
  await advance(20);
  expect(screen.getByRole('textbox', { name: /^메모 / })).toHaveProperty('value', '저장된 메모');
  return {
    client, counts, list, detail, invalidate, startRun, recheck, update,
    expire: () => { current = { ...current, recommendation: 'excluded', sourceStatus: 'closed', memo: '서버 메모 갱신', draft: { text: '서버 초안 갱신', editedByUser: true, generatedAt: null } }; },
  };
}

it('KST 23:59에서 자정을 지나면 로컬 목록·카운트·상세를 한 번 갱신하고 열린 입력을 보존한다', async () => {
  const h = await harness('2026-09-30T14:59:00Z');
  expect(h.invalidate).not.toHaveBeenCalled();
  const before = { counts: h.counts.mock.calls.length, list: h.list.mock.calls.length, detail: h.detail.mock.calls.length };
  const memo = screen.getByRole('textbox', { name: /^메모 / });
  const draft = screen.getByRole('textbox', { name: '문의 초안' });
  fireEvent.change(memo, { target: { value: '작성 중인 메모' } });
  fireEvent.change(draft, { target: { value: '작성 중인 초안' } });
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  fireEvent.change(screen.getByLabelText('금액(원)'), { target: { value: '12345' } });
  h.expire();

  await advance(60_000);
  expect(h.invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([['counts'], ['leads'], ['lead']]);
  expect(h.counts).toHaveBeenCalledTimes(before.counts + 1);
  expect(h.list).toHaveBeenCalledTimes(before.list + 2); // 추천 + 확인 필요
  expect(h.detail).toHaveBeenCalledTimes(before.detail + 1);
  expect(h.client.getQueryData(qk.counts(DEFAULT_FILTER))).toMatchObject({ recommended: 0, auto_excluded: 1 });
  expect(h.client.getQueryData(qk.lead('kst-deadline'))).toMatchObject({ sourceStatus: 'closed', recommendation: 'excluded' });
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(screen.getByRole('textbox', { name: /^메모 / })).toBe(memo);
  expect(memo).toHaveProperty('value', '작성 중인 메모');
  expect(draft).toHaveProperty('value', '작성 중인 초안');
  expect(screen.getByLabelText('금액(원)')).toHaveProperty('value', '12345');

  await advance(120_000);
  expect(h.invalidate).toHaveBeenCalledTimes(3);
  expect(h.counts).toHaveBeenCalledTimes(before.counts + 1);
  expect(h.list).toHaveBeenCalledTimes(before.list + 2);
  expect(h.detail).toHaveBeenCalledTimes(before.detail + 1);
  expect(h.startRun).not.toHaveBeenCalled();
  expect(h.recheck).not.toHaveBeenCalled();
  expect(h.update).not.toHaveBeenCalled();
});

it('초기 마운트와 같은 KST 날짜의 분 갱신·UTC 자정에는 다시 조회하지 않는다', async () => {
  const h = await harness('2026-09-30T23:59:00Z'); // KST 10월 1일 08:59
  const before = { counts: h.counts.mock.calls.length, list: h.list.mock.calls.length, detail: h.detail.mock.calls.length };
  await advance(180_000);
  expect(h.invalidate).not.toHaveBeenCalled();
  expect(h.counts).toHaveBeenCalledTimes(before.counts);
  expect(h.list).toHaveBeenCalledTimes(before.list);
  expect(h.detail).toHaveBeenCalledTimes(before.detail);
});

it('절전 뒤 며칠 건너뛰어도 현재 KST 날짜로 한 번 갱신하고 다음 날짜도 감지한다', async () => {
  const h = await harness('2026-09-30T05:00:00Z');
  vi.setSystemTime(new Date('2026-10-03T05:00:00Z'));
  await advance(60_000);
  expect(h.invalidate).toHaveBeenCalledTimes(3);
  await advance(60_000);
  expect(h.invalidate).toHaveBeenCalledTimes(3);
  vi.setSystemTime(new Date('2026-10-04T05:00:00Z'));
  await advance(60_000);
  expect(h.invalidate).toHaveBeenCalledTimes(6);
});
