import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { createClient } from '@contracts/index';
import { PortProvider } from '../app/PortContext';
import { useLiveEvents } from '../app/useLiveEvents';
import { MockPort } from '../data/mock/mockPort';
import { PortError } from '../data/port';
import type { ApplicationEntry, MessageWatchStatus } from '../domain/jobs';
import { ApplicationWorkspace } from '../features/jobs/ApplicationWorkspace';
import { MessageConnectionPanel } from '../features/jobs/MessageConnectionPanel';
import * as external from '../platform/external';
import { ToastProvider } from '../ui/Toasts';

const clients: QueryClient[] = [];
const now = new Date('2026-10-16T08:00:00Z');
afterEach(() => { clients.splice(0).forEach((client) => client.clear()); vi.restoreAllMocks(); });
function entry(patch: Partial<ApplicationEntry> = {}): ApplicationEntry {
  return { lead_id: 'application-a', title: '상세페이지 제작 의뢰', original_url: 'https://jobs.daangn.com/job-posts/public-post-123456789abc', recommendation: 'needs_review', published_at: '2026-10-16T07:40:00Z', last_checked_at: '2026-10-16T07:59:00Z', candidate: true, can_prepare: true, can_submit: false, mode: 'draft_only', submission_supported: false, hold_reasons: [{ code: 'remote_unknown', message: '재택 가능 여부 확인 필요' }], questions: ['납품 일정은 어떻게 되나요?'], draft: { text: '기존 사용자 초안입니다.', generated_at: null, edited_by_user: true }, ...patch };
}
function status(patch: Partial<MessageWatchStatus> = {}): MessageWatchStatus {
  return { enabled: false, state: 'stopped', unread_count: null, last_observed_at: null, last_increase_at: null, error_code: null, message: '메시지 감시를 시작하지 않았습니다.', poll_interval_seconds: 2, observation: 'visible_unread_badge', captures_message_body: false, browser_notifications: 'unknown', browser_open: false, needs_user_action: false, ...patch };
}
function harness(initial = entry()) {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  vi.spyOn(port.jobs, 'applications').mockResolvedValue({ mode: 'draft_only', submission_supported: false, items: [initial], total: 1 });
  vi.spyOn(port.jobs, 'messagesStatus').mockResolvedValue(status());
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(qc);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}><ToastProvider>{children}</ToastProvider></PortProvider></QueryClientProvider>;
  const workspace = <ApplicationWorkspace now={now} onOpenLead={vi.fn()} onSettings={vi.fn()} />;
  return { port, qc, wrapper, workspace };
}

it('확인 필요 공고의 기존 초안은 별도 자동 지원과 분리해 보존한다', async () => {
  const { port, wrapper, workspace } = harness();
  const prepare = vi.spyOn(port.jobs, 'prepare').mockResolvedValue(entry());
  const open = vi.spyOn(external, 'openExternal').mockResolvedValue('opened');
  render(workspace, { wrapper });
  await screen.findByText('상세페이지 제작 의뢰');
  expect(screen.getByText('재택 가능 여부 확인 필요')).toBeTruthy();
  expect(screen.getByText('저장된 초안과 자동 지원을 따로 관리합니다')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /제출하기|자동 지원 켜기/ })).toBeNull();
  expect(prepare).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '저장된 초안 열기' }));
  expect(prepare).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox', { name: '지원 초안 · 직접 수정함' })).toHaveProperty('value', '기존 사용자 초안입니다.');
  fireEvent.click(screen.getByRole('button', { name: '원문 열기' }));
  await waitFor(() => expect(open).toHaveBeenCalledOnce());
});

it('다른 화면에서 초안을 바꾸면 비교 기준을 유지하고 편집 내용을 보존한다', async () => {
  const original = entry();
  const { port, qc, wrapper, workspace } = harness(original);
  const save = vi.spyOn(port.jobs, 'saveDraft').mockRejectedValue(new PortError({ kind: 'api', code: 'draft_changed', message: '다른 화면에서 초안이 변경되었습니다.' }));
  render(workspace, { wrapper });
  await screen.findByText('상세페이지 제작 의뢰');
  fireEvent.click(screen.getByRole('button', { name: '초안 펼치기' }));
  const editor = screen.getByRole('textbox');
  fireEvent.change(editor, { target: { value: '지금 편집 중인 지원 초안' } });
  await act(async () => { qc.setQueryData(['jobs', 'applications'], { mode: 'draft_only', submission_supported: false, items: [entry({ draft: { text: '다른 창에서 새로 저장한 초안', generated_at: null, edited_by_user: true } })], total: 1 }); });
  expect(editor).toHaveProperty('value', '지금 편집 중인 지원 초안');
  fireEvent.click(screen.getByRole('button', { name: '초안 저장' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(original.lead_id, '지금 편집 중인 지원 초안', original.draft!.text));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('다른 화면에서 초안이 변경되었습니다.'));
  expect(editor).toHaveProperty('value', '지금 편집 중인 지원 초안');
});

it('미연결·로그인 대기를 실제 메시지 수신 정상으로 표시하지 않고 사용자 동작으로만 연결한다', async () => {
  const { port, wrapper } = harness();
  const start = vi.spyOn(port.jobs, 'messagesStart').mockResolvedValue(status({ enabled: true, state: 'login_required', needs_user_action: true, browser_open: true, message: '전용 Edge에서 직접 로그인하세요.' }));
  render(<MessageConnectionPanel now={now} onSettings={vi.fn()} />, { wrapper });
  await screen.findByText('연결하지 않음');
  expect(screen.getByText('미확인')).toBeTruthy();
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '연결하고 감시 시작' }));
  await screen.findByText('직접 로그인 필요');
  expect(start).toHaveBeenCalledOnce();
  expect(screen.queryByText('미읽음 표시 감시 중')).toBeNull();
  expect(screen.getByText(/수가 그대로인 새 메시지는 감지하지 못할 수/)).toBeTruthy();
});

it('감시 상태 갱신 실패 때 이전 미읽음 수를 현재 정상 관찰로 표시하지 않는다', async () => {
  const { port, wrapper } = harness();
  vi.mocked(port.jobs.messagesStatus).mockResolvedValueOnce(status({ enabled: true, state: 'watching', unread_count: 3, last_observed_at: now.toISOString(), browser_open: true, message: '미읽음 표시를 관찰하고 있습니다.' })).mockRejectedValue(new Error('관찰 연결 실패'));
  render(<MessageConnectionPanel now={now} onSettings={vi.fn()} />, { wrapper });
  await screen.findByText('미읽음 표시 감시 중');
  // 상태 조회 실패를 주입하되 실제 Edge나 OS 알림은 사용하지 않는다.
  await act(async () => { await clients[0]!.invalidateQueries({ queryKey: ['jobs', 'messages', 'status'] }); });
  await screen.findByText('연결 상태 확인 실패');
  expect(screen.queryByText('미읽음 표시 감시 중')).toBeNull();
  expect(screen.getByText('3개 · 마지막 관찰')).toBeTruthy();
});

it('메시지 알림은 인앱 토스트에서 메시지 연결 화면으로 이동한다', async () => {
  const { port, wrapper } = harness();
  const openMessages = vi.fn();
  function Listener() { useLiveEvents(vi.fn(), openMessages); return null; }
  let deliver: Parameters<typeof port.subscribe>[0] | undefined;
  vi.spyOn(port, 'subscribe').mockImplementation((callback) => { deliver = callback; return () => undefined; });
  render(<Listener />, { wrapper });
  act(() => deliver?.({ type: 'notification.created', id: 'message-alert', seq: 1, at: now.toISOString(), kind: 'daangn_message', title: '당근에 새 메시지가 있습니다.', leadId: null, sourceId: 'daangn-alba', suppressedReason: null }));
  fireEvent.click(await screen.findByRole('button', { name: '메시지 상태 보기' }));
  expect(openMessages).toHaveBeenCalledOnce();
});

it('로컬 API는 현재 인증 연결을 사용하고 준비·저장·메시지 연결만 요청한다', async () => {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const client = createClient({ baseUrl: 'http://127.0.0.1:43000', token: 'local-test' }, async (url, init) => {
    calls.push({ url: String(url), init });
    return Response.json(String(url).includes('/messages/') ? status() : entry());
  });
  await client.jobApplicationPrepare('id / 한글');
  await client.jobApplicationDraft('id / 한글', { text: '초안 변경', expected_text: '기존 초안' });
  await client.jobMessagesStart();
  await client.jobMessagesChromeStart();
  expect(calls[0]!.url).toContain('/v1/jobs/applications/id%20%2F%20%ED%95%9C%EA%B8%80/prepare');
  expect(JSON.parse(calls[1]!.init!.body as string)).toEqual({ text: '초안 변경', expected_text: '기존 초안' });
  expect(JSON.parse(calls[2]!.init!.body as string)).toEqual({ confirm: true });
  expect(calls[3]!.url).toBe('http://127.0.0.1:43000/v1/jobs/messages/chrome/start');
  expect(JSON.parse(calls[3]!.init!.body as string)).toEqual({ confirm: true });
  expect(calls.every((call) => new Headers(call.init?.headers).get('Authorization') === 'Bearer local-test')).toBe(true);
  expect(calls.some((call) => /\/submit$|jobs\.daangn/.test(call.url))).toBe(false);
});

it('크롬 연결코드는 복사로만 전달하고 DOM·조회캐시·mutation캐시·저장소에 노출하지 않는다', async () => {
  const { port, qc, wrapper } = harness();
  const token = 'synthetic-bridge-secret-do-not-render';
  const pairing = { connection: { url: 'http://127.0.0.1:43111', token }, extension_directory: 'C:\\LocalWorklead\\chrome-message-extension', status: status({ provider: 'chrome', enabled: true, state: 'starting', needs_user_action: true, message: '크롬 확장 연결을 기다립니다.' }) };
  const start = vi.spyOn(port.jobs, 'messagesChromeStart').mockResolvedValue(pairing);
  const copy = vi.spyOn(external, 'copyText').mockResolvedValue(true);
  const storageBefore = JSON.stringify({ ...localStorage });
  const view = render(<MessageConnectionPanel now={now} onSettings={vi.fn()} />, { wrapper });
  await screen.findByText('연결하지 않음');
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '현재 크롬으로 연결' }));
  await screen.findByText('크롬 확장 연결을 기다립니다.');
  expect(screen.queryByText('미읽음 표시 감시 중')).toBeNull();
  expect(view.container.innerHTML).not.toContain(token);
  expect(JSON.stringify(qc.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(token);
  expect(JSON.stringify(qc.getMutationCache().getAll().map((mutation) => mutation.state))).not.toContain(token);
  expect(JSON.stringify({ ...localStorage })).toBe(storageBefore);
  fireEvent.click(screen.getByRole('button', { name: '크롬 연결코드 복사' }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(JSON.stringify(pairing.connection)));
  fireEvent.click(screen.getByText('크롬 확장 설치·연결 방법'));
  fireEvent.click(screen.getByRole('button', { name: '확장 폴더 경로 복사' }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(pairing.extension_directory));
  expect(start).toHaveBeenCalledWith(true);
  expect(screen.getByText(/보이는 채팅 내용도 Worklead에서 보기/)).toBeTruthy();
  expect(screen.getByText(/앱을 다시 시작하면 다시 연결해야/)).toBeTruthy();
  expect(screen.getByRole('button', { name: '연결하고 감시 시작' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '감시 중지' }));
  await screen.findByText('연결하지 않음');
  expect(screen.queryByRole('button', { name: '크롬 연결코드 복사' })).toBeNull();
});

it('전용 Edge 감시 중에는 크롬 연결을 겹쳐 시작하지 않는다', async () => {
  const { port, wrapper } = harness();
  vi.mocked(port.jobs.messagesStatus).mockResolvedValue(status({ provider: 'edge', enabled: true, state: 'watching', browser_open: true, message: '전용 Edge에서 감시 중입니다.' }));
  const chrome = vi.spyOn(port.jobs, 'messagesChromeStart');
  render(<MessageConnectionPanel now={now} onSettings={vi.fn()} />, { wrapper });
  await screen.findByText('미읽음 표시 감시 중');
  expect(screen.getByRole('button', { name: '현재 크롬으로 연결' })).toHaveProperty('disabled', true);
  expect(chrome).not.toHaveBeenCalled();
});
