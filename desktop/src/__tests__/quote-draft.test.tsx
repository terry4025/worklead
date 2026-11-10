import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { MockPort } from '../data/mock/mockPort';
import { QuoteDraftPanel } from '../features/detail/QuoteDraftPanel';

let qc: QueryClient;
afterEach(() => { qc?.clear(); vi.restoreAllMocks(); });

it('견적서 초안 버튼으로 요청하고, 준비되면 금액 칸과 복사 버튼을 보인다', async () => {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  let state: 'none' | 'pending' | 'ready' = 'none';
  vi.spyOn(port, 'quoteDraft').mockImplementation(async () => ({ state, text: state === 'ready' ? '견적서 초안\n1단계 [금액]' : null, failureCode: null }));
  vi.spyOn(port, 'requestQuoteDraft').mockImplementation(async () => { state = 'pending'; return { state: 'pending', text: null, failureCode: null }; });
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'live', scenario: null, setScenario: () => undefined }}>
    <QuoteDraftPanel leadId="lead-1" /></PortProvider></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '견적서 초안 만들기' }));
  await screen.findByText('견적서 초안을 만들고 있습니다.');
  expect(screen.queryByRole('button', { name: '견적서 초안 만들기' })).toBeNull();
  state = 'ready';
  await act(async () => { await qc.invalidateQueries({ queryKey: ['quote-draft', 'lead-1'] }); });
  await screen.findByText(/1단계 \[금액\]/);
  expect(screen.getByRole('button', { name: '복사' })).toBeTruthy();
});
