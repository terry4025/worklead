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
  return { enabled: false, remembers_connection: false, paired: false, state: 'stopped', min_score: 50, daily_limit: 20, today_submitted: 0, today_reserved: 0, held: 0, uncertain: 0, last_connected_at: null, error_code: null, message: '자동 지원을 시작하지 않았습니다.', attempts: [], ...patch };
}
function harness(initial = status(), mode: 'live' | 'demo' = 'live') {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] }); ports.push(port);
  const query = vi.spyOn(port.jobs, 'autoApplyStatus').mockResolvedValue(initial);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(qc);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode, scenario: null, setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  const open = vi.fn();
  return { port, qc, query, wrapper, open, panel: <AutoApplyPanel now={now} onOpenLead={open} /> };
}

async function openManualConnection() {
  const summary = await screen.findByText('크롬 확장 수동 연결');
  fireEvent.click(summary);
  expect(summary.closest('details')).toHaveProperty('open', true);
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
  await openManualConnection();
  expect(start).not.toHaveBeenCalled();
  expect(screen.getByLabelText('자동 지원 최소 점수')).toHaveProperty('value', '50');
  expect(screen.getByText(/49점은 제외하고 50점부터/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' }));
  await screen.findByText('크롬 자동 지원 탭 연결 대기');
  expect(start).toHaveBeenCalledWith({ confirm: true, min_score: 50, daily_limit: 20, remember_connection: true });
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
  await openManualConnection();
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
  expect(screen.getByRole('button', { name: '설정 없이 자동 지원 시작' })).toHaveProperty('disabled', true);
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


it('저장된 연결 재개는 새 코드 없이 대기하며 연결됨이나 제출 완료로 표시하지 않는다', async () => {
  const h = harness(status({ remembers_connection: true, paired: true }));
  const start = vi.spyOn(h.port.jobs, 'autoApplyStart').mockResolvedValue({ connection: null, extension_directory: 'synthetic-extension-directory', status: status({ enabled: true, remembers_connection: true, paired: true, state: 'waiting' }) });
  const copy = vi.spyOn(external, 'copyText');
  render(h.panel, { wrapper: h.wrapper });
  await openManualConnection();
  await waitFor(() => expect(screen.getByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' })).toHaveProperty('disabled', false));
  fireEvent.click(screen.getByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' }));
  await screen.findByText('저장된 크롬 연결 자동 복구 중');
  expect(start).toHaveBeenCalledWith({ confirm: true, min_score: 50, daily_limit: 20, remember_connection: true });
  expect(screen.queryByRole('button', { name: '자동 지원 연결코드 복사' })).toBeNull();
  expect(screen.queryByText('크롬 연결됨 · 대상 공고 대기')).toBeNull();
  expect(screen.getByText('크롬에서 받은 작업 요청이 아직 없습니다.')).toBeTruthy();
  expect(copy).not.toHaveBeenCalled();
});

it('저장 연결 해제는 별도 API로 연결을 폐기하고 자동 지원을 시작하지 않는다', async () => {
  const h = harness(status({ remembers_connection: true, paired: true }));
  const forget = vi.spyOn(h.port.jobs, 'autoApplyForget').mockResolvedValue(status());
  const start = vi.spyOn(h.port.jobs, 'autoApplyStart');
  render(h.panel, { wrapper: h.wrapper });
  fireEvent.click(await screen.findByRole('button', { name: '저장 연결 해제' }));
  await waitFor(() => expect(forget).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.queryByRole('button', { name: '저장 연결 해제' })).toBeNull());
  expect(start).not.toHaveBeenCalled();
});

it('자동 연결을 기억하지 않는 선택은 시작 요청에도 반영된다', async () => {
  const h = harness();
  const start = vi.spyOn(h.port.jobs, 'autoApplyStart').mockResolvedValue({ connection: null, extension_directory: '', status: status({ enabled: true, state: 'waiting' }) });
  render(h.panel, { wrapper: h.wrapper });
  await openManualConnection();
  await screen.findByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' });
  fireEvent.click(screen.getByRole('checkbox', { name: '다음 실행부터 자동 연결·지원 재개' }));
  fireEvent.click(screen.getByRole('button', { name: '기준에 맞는 공고 자동 지원 시작' }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ confirm: true, min_score: 50, daily_limit: 20, remember_connection: false }));
});

it('연결 미응답은 이전 connected 상태나 처리 기록이 있어도 경고하고 자동 지원을 다시 시작하지 않는다', async () => {
  const h = harness(status({ enabled: true, remembers_connection: true, paired: true, state: 'connected',
    queue_reason: 'connection_stale', needs_user_action: true, retry_waiting: 1,
    attempts: [{ id: 'old_claim', lead_id: 'old_lead', title: '이전 처리 공고', state: 'claimed', reason_code: null, created_at: now.toISOString(), finished_at: null }] }));
  const start = vi.spyOn(h.port.jobs, 'autoApplyStart');
  render(h.panel, { wrapper: h.wrapper });
  expect((await screen.findByText('자동 지원 연결·상태 확인 필요')).closest('.status')?.className).toContain('status-warn');
  expect(within(screen.getByRole('alert')).getByText('크롬 응답이 끊겼습니다')).toBeTruthy();
  expect(screen.queryByText('자동 지원 처리 중')).toBeNull();
  expect(screen.queryByText('크롬 연결됨 · 대상 공고 대기')).toBeNull();
  await openManualConnection();
  expect(screen.getByRole('button', { name: '저장된 연결로 자동 지원 재개' })).toHaveProperty('disabled', false);
  expect(start).not.toHaveBeenCalled();
});

it('후보 없음·확장 연결 대기·상태 조회 실패를 구분해 보여 준다', async () => {
  const h = harness(status({ enabled: true, state: 'connected', queue_reason: 'no_candidates', candidates: [], retry_waiting: 0 }));
  render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('지금 지원 가능한 공고가 없습니다');
  expect(screen.queryByText('확장 연결을 기다립니다')).toBeNull();
  await act(async () => { h.qc.setQueryData(autoApplyKey, status({ enabled: true, state: 'waiting', queue_reason: 'connection_wait' })); });
  expect(screen.getByText('확장 연결을 기다립니다')).toBeTruthy();
  expect(screen.queryByText('지금 지원 가능한 공고가 없습니다')).toBeNull();
  h.query.mockRejectedValue(new Error('status failed'));
  await act(async () => { await h.qc.invalidateQueries({ queryKey: autoApplyKey }); });
  await screen.findByText('지원 대기 상태 미확인');
  expect(screen.queryByText('확장 연결을 기다립니다')).toBeNull();
  expect(screen.getByText('마지막으로 확인한 상태')).toBeTruthy();
});

it('공고별 재확인·재시도 대기·영구 보류·허가·완료를 구분하고 재시도 시각을 표시한다', async () => {
  const retryAt = '2026-10-25T05:05:00Z';
  const checkedAt = '2026-10-23T05:00:00Z';
  const h = harness(status({ enabled: true, state: 'connected', queue_reason: 'source_refresh', retry_waiting: 1,
    candidates: [
      { lead_id: 'queued', title: '대기 공고', stage: 'queued', reason_code: null },
      { lead_id: 'refresh', title: '오래된 원문 공고', stage: 'source_refresh', reason_code: 'refresh_failed', refresh_state: 'failed', refresh_error_code: 'unknown-private-error', last_checked_at: checkedAt },
      { lead_id: 'retry', title: '탭 오류 공고', stage: 'retry_wait', reason_code: 'browser_timeout', retry_count: 1, retry_after: retryAt },
      { lead_id: 'held', title: '확인 질문 공고', stage: 'held', reason_code: 'questions_required' },
      { lead_id: 'limit', title: '재시도 소진 공고', stage: 'held', reason_code: 'retry_exhausted', retry_count: 3 },
      { lead_id: 'permit', title: '제출 허가 공고', stage: 'permit_issued', reason_code: null },
      { lead_id: 'submitted', title: '완료 공고', stage: 'submitted', reason_code: null },
      { lead_id: 'uncertain', title: '미확인 공고', stage: 'uncertain', reason_code: 'submission_unconfirmed' },
    ] }));
  const view = render(h.panel, { wrapper: h.wrapper });
  const list = await screen.findByRole('region', { name: '공고별 진행 상태' });
  for (const label of ['지원 후보 대기', '원문 상태 재확인', '일시 실패 · 재시도 대기', '제출 허가 · 결과 확인 중', '제출 확인', '결과 미확인 · 재제출 중지']) expect(within(list).getByText(label)).toBeTruthy();
  expect(within(list).getByText('원문 재확인 실패 · 수집 상태 확인 필요')).toBeTruthy();
  expect(within(list).getByText('추가 질문이나 동의 항목 확인 필요')).toBeTruthy();
  expect(within(list).getByText('자동 재시도 3회 상한 도달')).toBeTruthy();
  expect(within(list).getAllByText('이 공고는 자동 재시도하지 않습니다.')).toHaveLength(2);
  expect(within(list).getByText(/재시도 1 \/ 3회/)).toBeTruthy();
  expect(within(list).getByText(/다음 재시도/)).toBeTruthy();
  expect(view.container.querySelector(`time[datetime="${retryAt}"]`)).toBeTruthy();
  expect(view.container.querySelector(`time[datetime="${checkedAt}"]`)).toBeTruthy();
  expect(view.container.innerHTML).not.toContain('unknown-private-error');
  expect(screen.getByText(/1분·5분·15분 간격으로 최대 3회/)).toBeTruthy();
  expect(within(list).queryByRole('button', { name: /^(재시도|지금 재시도|다시 지원)$/ })).toBeNull();
  fireEvent.click(within(list).getByRole('button', { name: '탭 오류 공고' }));
  expect(h.open).toHaveBeenCalledWith('retry');
});

it('알 수 없는 대기 사유는 원문 코드를 노출하거나 정상 실행으로 추정하지 않는다', async () => {
  const h = harness(status({ enabled: true, state: 'waiting', queue_reason: 'unexpected-private-code' }));
  const view = render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('지원 대기 사유를 확인해야 합니다');
  expect(view.container.innerHTML).not.toContain('unexpected-private-code');
  expect(screen.queryByText('자동 지원 처리 중')).toBeNull();
  await act(async () => { h.qc.setQueryData(autoApplyKey, status({ enabled: true, state: 'connected', needs_user_action: true, queue_reason: 'source_permission_unverified' })); });
  expect(within(screen.getByRole('alert')).getByText(/수집 설정에서 당근 소스의 허용 상태/)).toBeTruthy();
  expect(screen.queryByText(/당근 전용 탭·확장 연결을 확인하세요/)).toBeNull();
});

it('자동 설정은 화면을 열기만 해서는 등록하지 않고 시작 시 전용 창을 준비하며 로그인 대기를 완료로 표시하지 않는다', async () => {
  const h = harness();
  const setup = vi.spyOn(h.port.jobs, 'autoApplySetup').mockResolvedValue(status({
    provider: 'managed_edge', enabled: true, remembers_connection: true, state: 'waiting', setup_stage: 'login_required',
    browser_open: true, needs_user_action: true, queue_reason: 'login_required', message: '전용 당근 창에서 로그인해 주세요.',
  }));
  const manual = vi.spyOn(h.port.jobs, 'autoApplyStart');
  const copy = vi.spyOn(external, 'copyText');
  render(h.panel, { wrapper: h.wrapper });
  const button = await screen.findByRole('button', { name: '설정 없이 자동 지원 시작' });
  await waitFor(() => expect(button).toHaveProperty('disabled', false));
  expect(setup).not.toHaveBeenCalled();
  expect(screen.getByText('크롬 확장 수동 연결').closest('details')).toHaveProperty('open', false);
  fireEvent.click(button);
  await screen.findByText('당근 로그인 필요');
  expect(within(screen.getByRole('alert')).getByText('당근 로그인을 기다립니다')).toBeTruthy();
  expect(screen.getByText(/자동으로 열린 당근 창에서 로그인하면/)).toBeTruthy();
  expect(setup).toHaveBeenCalledWith({ confirm: true, min_score: 50, daily_limit: 20 });
  expect(screen.getByText('자동 지원 전용 당근 창이 열려 있습니다.')).toBeTruthy();
  expect(screen.getByText('자동 지원 창의 첫 작업 응답을 기다립니다.')).toBeTruthy();
  expect(screen.queryByText('크롬 확장 수동 연결')).toBeNull();
  expect(screen.queryByRole('button', { name: '자동 지원 연결코드 복사' })).toBeNull();
  expect(screen.queryByText('자동 연결됨 · 대상 공고 대기')).toBeNull();
  expect(manual).not.toHaveBeenCalled();
  expect(copy).not.toHaveBeenCalled();
});

it('관리되는 연결은 첫 작업 응답 뒤 실행 상태를 표시하고 중지한 뒤 자동 재등록하지 않는다', async () => {
  const h = harness(status({ provider: 'managed_edge', setup_stage: 'ready', browser_open: true,
    enabled: true, remembers_connection: true, paired: true, state: 'connected', needs_user_action: false,
    last_connected_at: now.toISOString(), queue_reason: 'no_candidates' }));
  const setup = vi.spyOn(h.port.jobs, 'autoApplySetup');
  const stop = vi.spyOn(h.port.jobs, 'autoApplyStop').mockResolvedValue(status({ provider: 'managed_edge' }));
  render(h.panel, { wrapper: h.wrapper });
  await screen.findByText('자동 연결됨 · 대상 공고 대기');
  expect(screen.getByRole('button', { name: '자동 연결·지원 재개' })).toHaveProperty('disabled', true);
  expect(screen.getByText(/최근 자동 지원 창 응답/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '자동 지원 중지' }));
  await waitFor(() => expect(stop).toHaveBeenCalledOnce());
  await screen.findByRole('button', { name: '설정 없이 자동 지원 시작' });
  expect(screen.queryByText('자동 연결됨 · 대상 공고 대기')).toBeNull();
  expect(setup).not.toHaveBeenCalled();
});

it('자동 설정 실패는 재등록 실패를 알리고 연결 완료로 추정하지 않는다', async () => {
  const h = harness();
  const setup = vi.spyOn(h.port.jobs, 'autoApplySetup').mockRejectedValue(new Error('setup failed'));
  render(h.panel, { wrapper: h.wrapper });
  const button = await screen.findByRole('button', { name: '설정 없이 자동 지원 시작' });
  await waitFor(() => expect(button).toHaveProperty('disabled', false));
  fireEvent.click(button);
  await screen.findByText('자동 지원을 등록하지 못했습니다. 현재 상태를 확인한 뒤 다시 시도하세요.');
  expect(setup).toHaveBeenCalledOnce();
  expect(screen.queryByText('자동 연결됨 · 대상 공고 대기')).toBeNull();
});

it('자동 설정 API는 로컬 인증 연결에 등록 조건만 전달한다', async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const client = createClient({ baseUrl: 'http://127.0.0.1:43000', token: 'synthetic-local-token' }, async (url, init) => {
    calls.push({ url: String(url), init }); return Response.json(status({ provider: 'managed_edge' }));
  });
  await client.jobAutoApplySetup({ confirm: true, min_score: 50, daily_limit: 20 });
  expect(calls).toHaveLength(1);
  expect(calls[0]?.url).toBe('http://127.0.0.1:43000/v1/jobs/auto-apply/setup');
  expect(JSON.parse(calls[0]!.init!.body as string)).toEqual({ confirm: true, min_score: 50, daily_limit: 20 });
  expect(new Headers(calls[0]?.init?.headers).get('Authorization')).toBe('Bearer synthetic-local-token');
});
