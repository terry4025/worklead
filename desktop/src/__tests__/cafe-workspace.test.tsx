import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { MockPort } from '../data/mock/mockPort';
import { CafeWorkspace } from '../features/cafe/CafeWorkspace';

const clients: QueryClient[] = [];
afterEach(() => { clients.splice(0).forEach((q) => q.clear()); });
function show(port = new MockPort({ scenario: 'normal', latency: [0, 0] })) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(qc);
  render(<QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'demo', scenario: 'normal', setScenario: () => {} }}><CafeWorkspace /></PortProvider></QueryClientProvider>);
  return port;
}
it('requires recorded consent before browser actions, then discovers synthetic cafes', async () => {
  const port = show();
  const action = vi.spyOn(port.cafe, 'action');
  await screen.findByText('시작 전에 확인해 주세요');
  expect(screen.queryByText('브라우저 열어 로그인')).toBeNull();
  fireEvent.click(screen.getByText('위험 안내 확인'));
  fireEvent.click(await screen.findByText('브라우저 열어 로그인'));
  await screen.findByText('로그인 확인됨');
  fireEvent.click(screen.getByText('카페 찾기'));
  await screen.findByText('재택 개발 의뢰 (합성)');
  expect(action).toHaveBeenCalledWith('discover');
  expect((screen.getByRole('checkbox', { name: /자동 게시/ }) as HTMLInputElement).disabled).toBe(false);
  expect((screen.getByRole('checkbox', { name: /자동 게시/ }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('button', { name: '게시 대상 승인' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '지금 게시' }) as HTMLButtonElement).disabled).toBe(true);
});
it('saves a versioned template with consecutive blank lines and displays preview', async () => {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  await port.cafe.consent('cafe-risk-2026-10-12-v1');
  show(port);
  fireEvent.click(await screen.findByText('카페 설정'));
  fireEvent.change(await screen.findByLabelText('제목'), { target: { value: '합성 템플릿' } });
  fireEvent.change(screen.getByLabelText('본문'), { target: { value: '첫 문단\n\n\n끝 문단' } });
  fireEvent.click(screen.getByText('템플릿 저장'));
  await screen.findByText('게시 템플릿 · 버전 2');
  expect((await port.cafe.template()).body).toBe('첫 문단\n\n\n끝 문단');
});
it('shows error and retry without enabling unsafe actions', async () => {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  vi.spyOn(port.cafe, 'status').mockRejectedValue(new Error('연결 끊김'));
  show(port);
  expect((await screen.findByRole('alert')).textContent).toContain('연결 끊김');
  expect((screen.getByRole('button', { name: '다시 불러오기' }) as HTMLButtonElement).disabled).toBe(false);
});
it('shows unread inquiries and preserves memo, read state and editable draft', async () => {
  const port = show(new MockPort({ scenario: 'cafe_inquiries', latency: [0, 0] }));
  fireEvent.click(await screen.findByText('문의 (1)'));
  await screen.findByText('작업 문의 · 안 읽음');
  expect(screen.getByText('앱에서 전체 확인 필요')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('문의 메모'), { target: { value: '견적 후속 메모' } });
  fireEvent.click(screen.getByText('메모 저장'));
  await screen.findByText('답글 초안');
  fireEvent.click(screen.getByText('답글 초안'));
  await screen.findByLabelText('답글 초안 · 직접 수정 후 원문에서 답글을 작성하세요');
  expect((await port.cafe.comments()).items[0]?.lead_id).toBeNull();
  fireEvent.click(screen.getByText('읽음 처리'));
  await screen.findByText('작업 문의 · 읽음');
  const data = await port.cafe.comments();
  expect(data.unread_count).toBe(0);
  expect(data.items[0]?.memo).toBe('견적 후속 메모');
  const id = await port.cafe.commentLead(data.items[0]!.id);
  expect((await port.getLead(id)).sourceId).toBe('manual');
});
it('shows incomplete comment coverage even when the web has zero visible comments', async () => {
  const port = new MockPort({ scenario: 'cafe_inquiries', latency: [0, 0] });
  vi.spyOn(port.cafe, 'comments').mockResolvedValue({ items: [], unread_count: 0, coverage: [{ attempt_id: 'zero-visible', post_url: 'https://cafe.daangn.com/synthetic/posts/zero', cafe_name: '합성 카페', reported_count: 4, read_count: 0, needs_app_check: true, checked_at: new Date().toISOString() }] });
  show(port);
  fireEvent.click(await screen.findByRole('tab', { name: '문의' }));
  await screen.findByText('앱에서 전체 확인 필요');
  expect(screen.getByText(/웹에서 0개 읽음/)).toBeTruthy();
  expect(screen.getByText('아직 저장된 문의가 없습니다')).toBeTruthy();
});
it('moves between tabs with arrow keys without opening a browser or posting', async () => {
  const port = show(new MockPort({ scenario: 'cafe_inquiries', latency: [0, 0] }));
  const post = vi.spyOn(port.cafe, 'post');
  const list = await screen.findByRole('tab', { name: /카페 목록/ });
  list.focus();
  fireEvent.keyDown(list, { key: 'ArrowRight' });
  expect(screen.getByRole('tab', { name: '게시 기록' }).getAttribute('aria-selected')).toBe('true');
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: '게시 기록' }));
  expect(post).not.toHaveBeenCalled();
});
it('labels a relative comment timestamp as estimated instead of showing its derived date as exact', async () => {
  const port = new MockPort({ scenario: 'cafe_inquiries', latency: [0, 0] });
  const data = await port.cafe.comments();
  data.items[0]!.written_raw = '3일 전';
  data.items[0]!.written_at = '2026-10-10T09:00:00Z';
  vi.spyOn(port.cafe, 'comments').mockResolvedValue(data);
  show(port);
  fireEvent.click(await screen.findByRole('tab', { name: '문의 (1)' }));
  expect((await screen.findByText(/3일 전 \(작성 시각 추정\)/)).textContent).toContain('3일 전 (작성 시각 추정)');
  expect(screen.queryByText(/2026\. 10\. 10\./)).toBeNull();
});
it('refreshes inquiry data when a queued comment job finishes after its action response', async () => {
  const port = new MockPort({ scenario: 'cafe_inquiries', latency: [0, 0] });
  const finalComments = await port.cafe.comments();
  finalComments.items[0]!.body = '늦게 수집된 제작 문의입니다';
  let completed = false;
  vi.spyOn(port.cafe, 'comments').mockImplementation(async () => completed ? finalComments : { items: [], unread_count: 0, coverage: [] });
  show(port);
  fireEvent.click(await screen.findByRole('tab', { name: '문의' }));
  await screen.findByText('아직 저장된 문의가 없습니다');
  const qc = clients.at(-1)!;
  const status = await port.cafe.status();
  await act(async () => { qc.setQueryData(['cafe', 'status'], { ...status, active_job: { id: 'deferred-comments', kind: 'comments', state: 'running', created_at: new Date().toISOString(), started_at: new Date().toISOString(), finished_at: null, error_code: null, error_message: null, result: null } }); });
  await screen.findByText(/댓글 확인 진행 중/);
  completed = true;
  await act(async () => { qc.setQueryData(['cafe', 'status'], { ...status, active_job: null }); });
  expect(await screen.findByText('늦게 수집된 제작 문의입니다')).toBeTruthy();
});
