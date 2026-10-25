import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { createClient } from '@contracts/index';
import { PortProvider } from '../app/PortContext';
import { MockPort } from '../data/mock/mockPort';
import type { AutoApplyStatus } from '../domain/jobs';
import { AutoApplyPanel, autoApplyKey } from '../features/jobs/AutoApplyPanel';
import { CollectionPanel } from '../features/collection/CollectionPanel';
import * as external from '../platform/external';

const clients: QueryClient[] = [];
const ports: MockPort[] = [];
const now = new Date('2026-10-25T05:00:00Z');
afterEach(() => { cleanup(); clients.splice(0).forEach((c) => c.clear()); ports.splice(0).forEach((p) => p.dispose()); vi.restoreAllMocks(); Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal'); });
function status(patch: Partial<AutoApplyStatus> = {}): AutoApplyStatus {
  return { enabled: false, state: 'stopped', min_score: 50, daily_limit: 20, today_submitted: 0, today_reserved: 0, held: 0, uncertain: 0, last_connected_at: null, error_code: null, message: '자동 지원을 시작하지 않았습니다.', attempts: [], ...patch };
}
function harness(initial = status(), mode: 'live' | 'demo' = 'live') {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] }); ports.push(port);
  const query = vi.spyOn(port.jobs, 'autoApplyStatus').mockResolvedValue(initial);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(qc);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode, scenario: null, setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  const open = vi.fn();
  return { port, qc, query, wrapper, open, panel: <AutoApplyPanel now={now} onOpenLead={open} /> };
}

it('연결 복구로 대조한 완료는 재제출 없이 확인한 기록으로 표시한다', async () => {
  const h = harness(status({ enabled: true, state: 'connected', today_submitted: 1,
    attempts: [{ id: 'auto_recovered', lead_id: 'lead_recovered', title: '복구한 공고', state: 'submitted', reason_code: 'completion_reconciled', created_at: now.toISOString(), finished_at: now.toISOString() }] }));
  const start = vi.spyOn(h.port.jobs, 'autoApplyStart');
  render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('연결 복구 후 공식 완료 표시를 재대조함 · 재제출 없음');
  expect(screen.getByText('제출 확인')).toBeTruthy();
  expect(screen.getByText(/완료 표시만 최대 3회 자동 대조/)).toBeTruthy();
  expect(start).not.toHaveBeenCalled();
});

it('50점부터 포함하는 기본 정책은 자동 시작하지 않고 명시적 시작 뒤에도 실제 연결 대기로 표시한다', async () => {
  const h = harness();
  const secret = 'synthetic-auto-apply-secret-never-render';
  const connection = { url: 'http://127.0.0.1:43222', token: secret };
  const start = vi.spyOn(h.port.jobs, 'autoApplyStart').mockResolvedValue({ connection, extension_directory: 'C:\\Worklead\\chrome-message-extension', status: status({ enabled: true, state: 'waiting', message: '확장 연결 대기 중입니다.' }) });
  const stop = vi.spyOn(h.port.jobs, 'autoApplyStop').mockResolvedValue(status());
  const copy = vi.spyOn(external, 'copyText').mockResolvedValue(true);
  const storage = JSON.stringify({ ...localStorage });
  const view = render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('자동 지원 중지');
  expect(start).not.toHaveBeenCalled();
  expect(screen.getByLabelText('자동 지원 최소 점수')).toHaveProperty('value', '50');
  expect(screen.getByText(/49점은 제외하고 50점부터/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' }));
  await screen.findByText('크롬 자동 지원 탭 연결 대기');
  expect(start).toHaveBeenCalledWith({ confirm: true, min_score: 50, daily_limit: 20 });
  expect(screen.queryByText('크롬 연결됨 · 대상 공고 대기')).toBeNull();
  expect(view.container.innerHTML).not.toContain(secret);
  expect(JSON.stringify(h.qc.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(secret);
  expect(JSON.stringify(h.qc.getMutationCache().getAll().map((m) => m.state))).not.toContain(secret);
  expect(JSON.stringify({ ...localStorage })).toBe(storage);
  fireEvent.click(screen.getByRole('button', { name: '자동 지원 연결코드 복사' }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(JSON.stringify(connection)));
  fireEvent.click(screen.getByRole('button', { name: '자동 지원 중지' }));
  await waitFor(() => expect(stop).toHaveBeenCalledOnce());
  await screen.findByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' });
  expect(screen.queryByRole('button', { name: '자동 지원 연결코드 복사' })).toBeNull();
});

it('점수·제출상한 입력은 범위를 검사하고 상태 폴링으로 작성 중인 값을 덮어쓰지 않는다', async () => {
  const h = harness();
  render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('자동 지원 중지');
  const score = screen.getByLabelText('자동 지원 최소 점수');
  const limit = screen.getByLabelText('자동 지원 하루 제출 상한');
  const start = screen.getByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' });
  for (const value of ['', '-1', '101', '49.5']) { fireEvent.change(score, { target: { value } }); expect(start).toHaveProperty('disabled', true); }
  fireEvent.change(score, { target: { value: '49' } });
  expect(screen.getByText(/48점은 제외하고 49점부터/)).toBeTruthy();
  for (const value of ['', '0', '201', '1.5']) { fireEvent.change(limit, { target: { value } }); expect(start).toHaveProperty('disabled', true); }
  fireEvent.change(limit, { target: { value: '3' } });
  expect(start).toHaveProperty('disabled', false);
  await act(async () => { h.qc.setQueryData(autoApplyKey, status({ min_score: 70, daily_limit: 10 })); });
  expect(score).toHaveProperty('value', '49'); expect(limit).toHaveProperty('value', '3');
});

it('연결·실제 제출 확인·기존 지원·보류·결과 미확인을 서로 구분한다', async () => {
  const h = harness(status({ enabled: true, state: 'connected', today_submitted: 1, held: 1, uncertain: 1,
    attempts: (['submitted', 'already_applied', 'held', 'uncertain', 'submitting'] as const).map((state, i) => ({ id: String(i), lead_id: `lead-${i}`, title: `공고 ${i}`, state, reason_code: state === 'held' ? 'questions_required' : null, created_at: now.toISOString(), finished_at: null })) }));
  render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('자동 지원 처리 중');
  for (const label of ['제출 확인', '기존 지원 확인', '조건 확인 필요 · 보류', '결과 미확인 · 재제출 중지', '제출 결과 확인 중']) expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getByText('추가 질문이나 동의 항목 확인 필요')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '공고 3' }));
  expect(h.open).toHaveBeenCalledWith('lead-3');
  h.query.mockRejectedValue(new Error('status unavailable'));
  await act(async () => { await h.qc.invalidateQueries({ queryKey: autoApplyKey }); });
  await screen.findByText('현재 자동 지원 상태 미확인');
  expect(screen.queryByText('자동 지원 처리 중')).toBeNull();
  expect(screen.getByText('마지막으로 확인한 상태')).toBeTruthy();
});

it('데모는 자동 지원 연결과 실제 제출을 시작할 수 없다', async () => {
  const h = harness(status(), 'demo');
  const start = vi.spyOn(h.port.jobs, 'autoApplyStart');
  render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('자동 지원 중지');
  expect(screen.getByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' })).toHaveProperty('disabled', true);
  expect(start).not.toHaveBeenCalled();
});

it('자동 지원 제어 API는 인증된 로컬 연결에만 정책과 시작·중지를 요청한다', async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const client = createClient({ baseUrl: 'http://127.0.0.1:43000', token: 'synthetic-local-token' }, async (url, init) => { calls.push({ url: String(url), init }); return Response.json(status()); });
  await client.jobAutoApplyStatus();
  await client.jobAutoApplyStart({ confirm: true, min_score: 50, daily_limit: 20 });
  await client.jobAutoApplyStop();
  expect(calls.map((call) => call.url)).toEqual(['http://127.0.0.1:43000/v1/jobs/auto-apply/status', 'http://127.0.0.1:43000/v1/jobs/auto-apply/start', 'http://127.0.0.1:43000/v1/jobs/auto-apply/stop']);
  expect(JSON.parse(calls[1]!.init!.body as string)).toEqual({ confirm: true, min_score: 50, daily_limit: 20 });
  expect(calls.every((call) => new Headers(call.init?.headers).get('Authorization') === 'Bearer synthetic-local-token')).toBe(true);
  expect(calls.some((call) => /jobs\.daangn|\/submit$/.test(call.url))).toBe(false);
});

it('당근 자동 수집만 5분을 선택할 수 있고 다른 사이트 주기는 유지한다', async () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  const h = harness();
  const original = (await h.port.listSources()).find((source) => source.id === 'daangn-alba')!;
  vi.spyOn(h.port, 'listSources').mockResolvedValue([original, { ...original, id: 'iboss', name: '다른 수집 사이트' }]);
  const update = vi.spyOn(h.port, 'updateSource');
  render(<CollectionPanel open onClose={() => undefined} onOpenLead={() => undefined} />, { wrapper: h.wrapper });
  const sources = await screen.findAllByRole('article');
  const daangn = sources.find((source) => source.querySelector('#src-daangn-alba'))!;
  const other = sources.find((source) => source.querySelector('#src-iboss'))!;
  expect(within(daangn).getByRole('option', { name: '5분마다' })).toBeTruthy();
  expect(within(other).queryByRole('option', { name: '5분마다' })).toBeNull();
  fireEvent.change(within(daangn).getByRole('combobox', { name: '수집 주기' }), { target: { value: '5' } });
  await waitFor(() => expect(update).toHaveBeenCalledWith('daangn-alba', { intervalMinutes: 5 }));
});
