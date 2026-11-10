import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { MockPort } from '../data/mock/mockPort';
import { MessageInboxPanel } from '../features/jobs/MessageInboxPanel';

let qc: QueryClient;
afterEach(() => { qc?.clear(); vi.restoreAllMocks(); });
it('새 내용은 새로고침 없이 바꾸며 오류·계정 변경·관찰 지연 시 숨긴다', async () => {
  const port = new MockPort({ scenario: 'normal', latency: [0,0] });
  const value = { enabled:true,state:'visible' as const,text:'합성 답장 <img src=x onerror=alert(1)>',observed_at:'2026-10-25T07:00:00Z',lead_id:null,lead_title:null,source:'visible_chat_panel' as const,persists_body:false as const, draft_state:'off' as const, draft_text:null };
  const read = vi.spyOn(port.jobs, 'messagesInbox').mockResolvedValue(value);
  qc = new QueryClient({defaultOptions:{queries:{retry:false}}});
  const ui = render(<QueryClientProvider client={qc}><PortProvider value={{port,mode:'live',scenario:null,setScenario:()=>undefined}}><MessageInboxPanel now={new Date('2026-10-25T07:00:00Z')} onOpenLead={vi.fn()} /></PortProvider></QueryClientProvider>);
  await screen.findByText(value.text);
  expect(ui.container.querySelector('img')).toBeNull();
  expect(screen.getByText(/특정 지원에 대한 답장으로 단정하지/)).toBeTruthy();
  await act(async()=>{qc.setQueryData(['jobs','messages','inbox'],{...value,text:'다음 합성 답장'});});
  await screen.findByText('다음 합성 답장');
  await act(async()=>{qc.setQueryData(['jobs','messages','inbox'],{...value,state:'stale',text:null});});
  await screen.findByText('관찰 지연 · 이전 내용 숨김');
  expect(screen.queryByText('다음 합성 답장')).toBeNull();
  await act(async()=>{qc.setQueryData(['jobs','messages','inbox'],{...value,state:'account_changed',text:null});});
  await screen.findByText('계정 변경 · 다시 연결 필요');
  expect(screen.queryByText(value.text)).toBeNull();
  await act(async()=>{qc.setQueryData(['jobs','messages','inbox'],value);});
  await screen.findByText(value.text);
  read.mockRejectedValue(new Error('local connection lost'));
  await act(async()=>{await qc.invalidateQueries({queryKey:['jobs','messages','inbox']});});
  await screen.findByText('연결 상태 확인 실패 · 내용 숨김');
  expect(screen.queryByText(value.text)).toBeNull();
});

it('AI 답장 초안은 준비됐을 때만 복사 버튼과 함께 보인다', async () => {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const base = { enabled: true, state: 'visible' as const, text: '합성 고객 문의', observed_at: '2026-10-25T07:00:00Z', lead_id: null,
    lead_title: null, source: 'visible_chat_panel' as const, persists_body: false as const };
  vi.spyOn(port.jobs, 'messagesInbox').mockResolvedValue({ ...base, draft_state: 'ready', draft_text: '합성 답장 초안' });
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}>
    <MessageInboxPanel now={new Date('2026-10-25T07:00:00Z')} onOpenLead={vi.fn()} /></PortProvider></QueryClientProvider>);
  await screen.findByText('합성 답장 초안');
  expect(screen.getByRole('button', { name: '초안 복사' })).toBeTruthy();
  expect(screen.getByText(/자동으로 보내지 않습니다/)).toBeTruthy();
  await act(async () => { qc.setQueryData(['jobs', 'messages', 'inbox'], { ...base, draft_state: 'off', draft_text: null }); });
  await waitFor(() => expect(screen.queryByRole('button', { name: '초안 복사' })).toBeNull());
});
