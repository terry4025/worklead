import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
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
  expect((screen.getByRole('checkbox', { name: '자동 게시' }) as HTMLInputElement).disabled).toBe(true);
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
