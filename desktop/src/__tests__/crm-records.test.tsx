import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { qk } from '../app/queries';
import { mapDetail, mapSales } from '../data/live/mapper';
import { MockPort } from '../data/mock/mockPort';
import type { LeadDetail, Outcome } from '../domain/model';
import { DraftBox, MemoBox, Outcomes } from '../features/detail/Records';
import { LeadDetailPane } from '../features/detail/LeadDetailPane';
import { ToastProvider } from '../ui/Toasts';
import { formatMoneyMinor } from '../domain/format';

const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach((qc) => qc.clear()); vi.restoreAllMocks(); });

function lead(id = 'owned-a'): LeadDetail {
  const input = JSON.parse(readFileSync(join(__dirname, '../../../contracts/fixtures/lead-details/01.json'), 'utf8'));
  const value = mapDetail(input);
  return { ...value, id, title: id, memo: id + ' 메모', draft: { text: id + ' 초안', editedByUser: true, generatedAt: null }, outcomes: [] };
}

function outcome(id: string, kind: Outcome['kind'], amount: number | null, currency = 'KRW'): Outcome {
  return { id, kind, amount, currency, occurredOn: '2026-09-30', recordedAt: '2026-09-30T00:00:00Z', note: null, evidenceRef: null };
}

function harness(leads: LeadDetail[] = []) {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  clients.push(qc);
  leads.forEach((l) => qc.setQueryData(qk.lead(l.id), l));
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'demo', scenario: 'normal', setScenario: () => undefined }}><ToastProvider>{children}</ToastProvider></PortProvider></QueryClientProvider>;
  const pane = (id: string) => <LeadDetailPane leadId={id} sources={[]} categoryLabel={(c) => c} now={new Date('2026-09-30T00:00:00Z')} overlay={false} onClose={() => undefined} onDismissed={() => undefined} />;
  return { port, qc, wrapper, pane };
}

it('통화별 합계를 분리하고 USD 최소 단위 100을 USD 1.00으로 표시한다', () => {
  const { wrapper } = harness();
  const value = lead();
  value.outcomes = [outcome('krw', 'payment_received', 1000), outcome('usd', 'payment_received', 100, 'USD')];
  const view = render(<Outcomes lead={value} />, { wrapper });
  const sums = Array.from(view.container.querySelectorAll('.outcome-sum')).map((p) => p.textContent).join(' ');
  expect(sums).toContain('USD 1.00');
  expect(sums).toContain('1,000원');
  expect(sums).not.toContain('1,100원');
  expect(screen.getAllByText('USD 1.00').length).toBeGreaterThan(0);
});

it('미기록 계약·수금·환불·비용을 0원이나 숨김으로 처리하지 않는다', () => {
  const { wrapper } = harness();
  const value = lead();
  value.outcomes = [outcome('contract', 'contract_confirmed', null), outcome('pay', 'payment_received', null), outcome('refund', 'refund', null), outcome('cost', 'direct_cost', null)];
  const view = render(<Outcomes lead={value} />, { wrapper });
  const sum = view.container.querySelector('.outcome-sum')!.textContent;
  expect(sum).toContain('환불');
  expect(sum).toContain('직접 비용');
  expect(sum?.match(/금액 미기록 1건/g)).toHaveLength(4);
  expect(sum).not.toContain('0원');
});

it('안전 정수 범위 밖·소수 금액은 제출하지 않는다', async () => {
  const { port, wrapper } = harness();
  const add = vi.spyOn(port, 'addOutcome');
  render(<Outcomes lead={lead()} />, { wrapper });
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  for (const value of ['1.5', '9007199254740992', '-1']) {
    fireEvent.change(screen.getByLabelText('금액(원)'), { target: { value } });
    expect(screen.getByRole('button', { name: '기록 추가' })).toHaveProperty('disabled', true);
    fireEvent.submit(screen.getByRole('button', { name: '기록 추가' }).closest('form')!);
  }
  await act(async () => undefined);
  expect(add).not.toHaveBeenCalled();
});

it('초안 저장 실패가 보이고 입력을 유지한 채 명시적으로 재시도할 수 있다', async () => {
  const value = lead();
  const { port, wrapper } = harness();
  const saved = { ...value, draft: { ...value.draft!, text: '수정한 초안' } };
  const update = vi.spyOn(port, 'updateLead').mockRejectedValueOnce(new Error('합성 저장 오류')).mockResolvedValueOnce(saved);
  render(<DraftBox lead={value} mode="demo" />, { wrapper });
  fireEvent.change(screen.getByRole('textbox', { name: '문의 초안' }), { target: { value: '수정한 초안' } });
  fireEvent.blur(screen.getByRole('textbox', { name: '문의 초안' }));
  expect((await screen.findByRole('alert')).textContent).toContain('초안을 저장하지 못했습니다');
  expect(screen.getByRole('textbox', { name: '문의 초안' })).toHaveProperty('value', '수정한 초안');
  fireEvent.click(screen.getByRole('button', { name: '초안 저장 다시 시도' }));
  await screen.findByText('초안 저장됨');
  expect(update).toHaveBeenCalledTimes(2);
  expect(update).toHaveBeenLastCalledWith(value.id, { draftText: '수정한 초안' });
});

it('따뜻한 캐시에서 다른 리드를 선택하면 계약 입력 폼을 격리한다', () => {
  const a = lead('owned-a'); const b = lead('owned-b');
  const { wrapper, pane } = harness([a, b]);
  const view = render(pane(a.id), { wrapper });
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  fireEvent.change(screen.getByLabelText('금액(원)'), { target: { value: '111' } });
  fireEvent.change(screen.getByLabelText('메모'), { target: { value: 'A에만 쓴 기록' } });
  view.rerender(pane(b.id));
  expect(screen.queryByLabelText('금액(원)')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  expect(screen.getByLabelText('금액(원)')).toHaveProperty('value', '');
  expect(screen.getByLabelText('메모')).toHaveProperty('value', '');
});

it('안전 정수 두 원금의 큰 합계도 원 단위 끝자리까지 보존한다', () => {
  const { wrapper } = harness();
  const value = lead();
  value.outcomes = [outcome('a', 'payment_received', Number.MAX_SAFE_INTEGER), outcome('b', 'payment_received', Number.MAX_SAFE_INTEGER)];
  const view = render(<Outcomes lead={value} />, { wrapper });
  expect(view.container.querySelector('.outcome-sum')!.textContent).toContain('18,014,398,509,481,982원');
  expect(formatMoneyMinor('18014398509481982', 'USD')).toBe('USD 180,143,985,094,819.82');
});

it('부분 미기록은 확인된 금액과 미기록 수를 함께 표시하고 실제 0원 환불·비용은 남긴다', () => {
  const { wrapper } = harness();
  const value = lead();
  value.outcomes = [outcome('known', 'payment_received', 1000), outcome('unknown', 'payment_received', null), outcome('refund-zero', 'refund', 0), outcome('cost-zero', 'direct_cost', 0)];
  const view = render(<Outcomes lead={value} />, { wrapper });
  const sum = view.container.querySelector('.outcome-sum')!.textContent;
  expect(sum).toContain('확인 1,000원 · 금액 미기록 1건');
  expect(sum).toContain('환불 0원');
  expect(sum).toContain('직접 비용 0원');
});

it('구형·알 수 없는 통화 코드를 그대로 표시하고 임의 환산하지 않는다', () => {
  const { wrapper } = harness();
  const value = lead();
  value.outcomes = [outcome('USD', 'payment_received', 100, 'USD'), outcome('usd', 'payment_received', 100, 'usd'), outcome('unknown', 'payment_received', 100, 'ZZZ')];
  const view = render(<Outcomes lead={value} />, { wrapper });
  expect(view.container.querySelectorAll('.outcome-sum')).toHaveLength(3);
  expect(screen.getAllByText('usd 100 (최소 단위)').length).toBeGreaterThan(0);
  expect(screen.getAllByText('ZZZ 100 (최소 단위)').length).toBeGreaterThan(0);
  expect(formatMoneyMinor(100, 'JPY')).toBe('JPY 100');
  expect(formatMoneyMinor(1001, 'KWD')).toBe('KWD 1.001');
});

it('복원된 unsafe 숫자 원금은 반올림한 값을 확정 금액으로 표시하지 않는다', () => {
  const { wrapper } = harness();
  const value = lead();
  value.outcomes = [outcome('legacy-unsafe', 'payment_received', Number.MAX_SAFE_INTEGER + 1)];
  render(<Outcomes lead={value} />, { wrapper });
  expect(screen.getByText('정확한 금액 확인 필요')).toBeTruthy();
  expect(screen.queryByText('9,007,199,254,740,992원')).toBeNull();
});

it.each([['', null], ['0', 0], ['20,000', 20000], ['9007199254740991', Number.MAX_SAFE_INTEGER]] as const)('빈 값·0·정수 입력을 구별하여 저장한다: %s', async (input, expected) => {
  const value = lead();
  const { port, wrapper } = harness();
  const add = vi.spyOn(port, 'addOutcome').mockResolvedValue(value);
  render(<Outcomes lead={value} />, { wrapper });
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  fireEvent.change(screen.getByLabelText('금액(원)'), { target: { value: input } });
  fireEvent.click(screen.getByRole('button', { name: '기록 추가' }));
  await waitFor(() => expect(add).toHaveBeenCalledWith(value.id, expect.objectContaining({ amount: expected })));
  await waitFor(() => expect(screen.queryByLabelText('금액(원)')).toBeNull());
});

it('같은 렌더 사이의 두 submit은 성과 기록을 한 번만 추가한다', async () => {
  const value = lead();
  const { port, wrapper } = harness();
  let complete!: (value: LeadDetail) => void;
  const add = vi.spyOn(port, 'addOutcome').mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  render(<Outcomes lead={value} />, { wrapper });
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  fireEvent.change(screen.getByLabelText('금액(원)'), { target: { value: '1' } });
  const form = screen.getByRole('button', { name: '기록 추가' }).closest('form')!;
  act(() => { fireEvent.submit(form); fireEvent.submit(form); });
  await waitFor(() => expect(add).toHaveBeenCalledOnce());
  await act(async () => complete(value));
});

it('이전 리드의 늦은 성과 응답이 현재 리드 폼을 비우거나 닫지 않는다', async () => {
  const a = lead('owned-a'); const b = lead('owned-b');
  const { port, qc, wrapper, pane } = harness([a, b]);
  let complete!: (value: LeadDetail) => void;
  const add = vi.spyOn(port, 'addOutcome').mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  const view = render(pane(a.id), { wrapper });
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  fireEvent.change(screen.getByLabelText('금액(원)'), { target: { value: '111' } });
  fireEvent.click(screen.getByRole('button', { name: '기록 추가' }));
  await waitFor(() => expect(add).toHaveBeenCalledOnce());
  view.rerender(pane(b.id));
  fireEvent.click(screen.getByRole('button', { name: '계약·수금 기록' }));
  fireEvent.change(screen.getByLabelText('금액(원)'), { target: { value: '777' } });
  await act(async () => complete({ ...a, outcomes: [outcome('a-paid', 'payment_received', 111)] }));
  await waitFor(() => expect(qc.getQueryData<LeadDetail>(qk.lead(a.id))?.outcomes).toHaveLength(1));
  expect(screen.getByLabelText('금액(원)')).toHaveProperty('value', '777');
  expect(qc.getQueryData<LeadDetail>(qk.lead(b.id))?.outcomes).toHaveLength(0);
});

it('리드 전환 후 이전 메모 저장 완료 표시가 현재 리드에 나타나지 않는다', async () => {
  const a = lead('owned-a'); const b = lead('owned-b');
  const { port, wrapper, pane } = harness([a, b]);
  let complete!: (value: LeadDetail) => void;
  const update = vi.spyOn(port, 'updateLead').mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  const view = render(pane(a.id), { wrapper });
  fireEvent.change(screen.getByLabelText(/^메모 \(M/), { target: { value: 'A 메모 변경' } });
  fireEvent.blur(screen.getByLabelText(/^메모 \(M/));
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  view.rerender(pane(b.id));
  await act(async () => complete({ ...a, memo: 'A 메모 변경' }));
  expect(screen.getByLabelText(/^메모 \(M/)).toHaveProperty('value', b.memo);
  expect(view.container.querySelector('.memo .save-state')!.textContent).toBe('');
});

it('이전 리드의 초안 저장 오류가 새 리드 초안에 섞이지 않는다', async () => {
  const a = lead('owned-a'); const b = lead('owned-b');
  const { port, wrapper, pane } = harness([a, b]);
  let fail!: (error: Error) => void;
  const update = vi.spyOn(port, 'updateLead').mockImplementation(() => new Promise((_resolve, reject) => { fail = reject; }));
  const view = render(pane(a.id), { wrapper });
  fireEvent.change(screen.getByRole('textbox', { name: '문의 초안' }), { target: { value: 'A 초안 변경' } });
  fireEvent.blur(screen.getByRole('textbox', { name: '문의 초안' }));
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  view.rerender(pane(b.id));
  await act(async () => fail(new Error('A 합성 실패')));
  expect(screen.getByRole('textbox', { name: '문의 초안' })).toHaveProperty('value', b.draft!.text);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText('초안 저장됨')).toBeNull();
});

it('초안 저장 중 이어 쓴 글을 늦은 서버 응답이 덮지 않고 명시 저장을 남긴다', async () => {
  const value = lead();
  const { port, wrapper } = harness();
  let complete!: (value: LeadDetail) => void;
  const update = vi.spyOn(port, 'updateLead').mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  const view = render(<DraftBox lead={value} mode="demo" />, { wrapper });
  fireEvent.change(screen.getByRole('textbox', { name: '문의 초안' }), { target: { value: '먼저 저장한 글' } });
  fireEvent.blur(screen.getByRole('textbox', { name: '문의 초안' }));
  fireEvent.keyDown(screen.getByRole('textbox', { name: '문의 초안' }), { key: 'Enter', ctrlKey: true });
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByRole('textbox', { name: '문의 초안' }), { target: { value: '응답 대기 중 이어 쓴 글' } });
  const saved = { ...value, draft: { ...value.draft!, text: '먼저 저장한 글' } };
  await act(async () => complete(saved));
  view.rerender(<DraftBox lead={saved} mode="demo" />);
  expect(screen.getByRole('textbox', { name: '문의 초안' })).toHaveProperty('value', '응답 대기 중 이어 쓴 글');
  expect(screen.queryByText('초안 저장됨')).toBeNull();
  expect(screen.getByRole('button', { name: '초안 저장' })).toHaveProperty('disabled', false);
});

it('메모 저장 중 이어 쓴 글을 늦은 서버 응답이 덮지 않고 명시 저장을 남긴다', async () => {
  const value = lead();
  const { port, wrapper } = harness();
  let complete!: (value: LeadDetail) => void;
  const update = vi.spyOn(port, 'updateLead').mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  const view = render(<MemoBox lead={value} />, { wrapper });
  fireEvent.change(screen.getByLabelText(/^메모 \(M/), { target: { value: '먼저 저장한 메모' } });
  fireEvent.blur(screen.getByLabelText(/^메모 \(M/));
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByLabelText(/^메모 \(M/), { target: { value: '응답 대기 중 이어 쓴 메모' } });
  const saved = { ...value, memo: '먼저 저장한 메모' };
  await act(async () => complete(saved));
  view.rerender(<MemoBox lead={saved} />);
  expect(screen.getByLabelText(/^메모 \(M/)).toHaveProperty('value', '응답 대기 중 이어 쓴 메모');
  expect(view.container.querySelector('.memo .save-state')!.textContent).not.toBe('저장됨');
  expect(screen.getByRole('button', { name: '메모 저장' })).toHaveProperty('disabled', false);
});

it('메모 저장 오류를 표시하고 유지한 글을 명시적으로 다시 저장한다', async () => {
  const value = lead();
  const { port, wrapper } = harness();
  const update = vi.spyOn(port, 'updateLead').mockRejectedValueOnce(new Error('합성 메모 저장 오류')).mockResolvedValueOnce({ ...value, memo: '변경한 메모' });
  render(<MemoBox lead={value} />, { wrapper });
  fireEvent.change(screen.getByLabelText(/^메모 \(M/), { target: { value: '변경한 메모' } });
  fireEvent.blur(screen.getByLabelText(/^메모 \(M/));
  expect((await screen.findByRole('alert')).textContent).toContain('메모를 저장하지 못했습니다');
  expect(screen.getByLabelText(/^메모 \(M/)).toHaveProperty('value', '변경한 메모');
  fireEvent.click(screen.getByRole('button', { name: '메모 저장 다시 시도' }));
  await screen.findByText('저장됨');
  expect(update).toHaveBeenCalledTimes(2);
  expect(update).toHaveBeenLastCalledWith(value.id, { memo: '변경한 메모' });
});

it('새 metrics 계약의 큰 정수 문자열·null·기간·통화 코드를 그대로 매핑한다', () => {
  const input = JSON.parse(readFileSync(join(__dirname, '../../../contracts/fixtures/metrics.json'), 'utf8'));
  const zero = { amount: '0', known_amount: '0', records: 0, missing_amounts: 0 };
  input.sales = { ...input.sales, period_start: '2026-09-01', period_end: '2026-09-30', time_zone: 'Asia/Seoul', stage_scope: 'current_all', collected: null, by_currency: [{ currency: 'USD', contract: zero, collected: { amount: null, known_amount: '18014398509481982', records: 3, missing_amounts: 1 }, refunded: zero, direct_cost: zero }] };
  const value = mapSales(input);
  expect(value.byCurrency[0]!.collected).toEqual({ amount: null, knownAmount: '18014398509481982', records: 3, missingAmounts: 1 });
  expect(value.collected).toBeNull();
  expect(value.periodStart).toBe('2026-09-01');
  expect(value.periodEnd).toBe('2026-09-30');
  expect(value.stageScope).toBe('current_all');
});

it('기존 metrics fixture를 변조하지 않고 누락 필드와 숫자 합계의 호환 기본값을 적용한다', () => {
  const input = JSON.parse(readFileSync(join(__dirname, '../../../contracts/fixtures/metrics.json'), 'utf8'));
  const value = mapSales(input);
  expect(value.byCurrency).toEqual([]);
  expect(value.periodStart).toBeNull();
  expect(value.periodEnd).toBeNull();
  expect(value.collected).toBe(String(input.sales.collected));
  expect(value.timeZone).toBe('Asia/Seoul');
});

it('데모도 발생일 한국 시간 기간과 통화별 미기록을 적용하며 수주와 수금을 분리한다', async () => {
  const value = lead();
  value.salesStage = 'won';
  value.outcomes = [outcome('old', 'payment_received', 100, 'KRW'), outcome('first', 'payment_received', 200, 'KRW'), outcome('today-unknown', 'payment_received', null, 'KRW'), outcome('future', 'payment_received', 400, 'KRW'), outcome('usd', 'payment_received', 100, 'USD')];
  value.outcomes[0]!.occurredOn = '2026-08-31';
  value.outcomes[1]!.occurredOn = '2026-09-01';
  value.outcomes[3]!.occurredOn = '2026-10-01';
  const port = new MockPort({ scenario: 'normal', initialLeads: [value], latency: [0, 0], now: () => Date.parse('2026-09-30T14:59:00Z') });
  const sales = await port.getSalesSummary();
  expect(sales.periodStart).toBe('2026-09-01');
  expect(sales.periodEnd).toBe('2026-09-30');
  expect(sales.byCurrency.find((g) => g.currency === 'KRW')!.collected).toEqual({ amount: null, knownAmount: '200', records: 2, missingAmounts: 1 });
  expect(sales.byCurrency.find((g) => g.currency === 'USD')!.collected.amount).toBe('100');
  expect(sales.won).toBe(1);
  expect(sales.collected).toBeNull();
  expect(sales.contractAmount).toBe('0');
});

