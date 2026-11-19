import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { resetDatabaseQueries } from '../app/databaseCache';
import { qk } from '../app/queries';
import { MockPort } from '../data/mock/mockPort';
import { SettingsDialog } from '../features/settings/SettingsDialog';

function harness() {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'demo', scenario: 'normal', setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  const dialog = (open = true) => <SettingsDialog open={open} onClose={() => undefined} fontScale="normal" setFontScale={() => undefined} theme="system" setTheme={() => undefined} />;
  return { port, qc, wrapper, dialog };
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal'); Reflect.deleteProperty(HTMLDialogElement.prototype, 'close'); });

it('변경한 설정을 저장하면 캐시·폼 갱신 이후에도 저장됨 표시를 유지하고 새 편집에서 해제한다', async () => {
  const { port, qc, wrapper, dialog } = harness();
  const save = vi.spyOn(port, 'updateSettings');
  const view = render(dialog(), { wrapper });
  fireEvent.change(await screen.findByLabelText('최소 계약 금액 (원)'), { target: { value: '999000' } });
  fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
  await waitFor(() => expect(qc.getQueryData<{ profile: { minContract: number } }>(qk.settings)?.profile.minContract).toBe(999000));
  await waitFor(() => expect(screen.getByLabelText('최소 계약 금액 (원)')).toHaveProperty('value', '999000'));
  await screen.findByText('저장됨');
  expect(save).toHaveBeenCalledOnce();
  vi.useFakeTimers();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText('저장됨')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('최소 계약 금액 (원)'), { target: { value: '999001' } });
  expect(screen.queryByText('저장됨')).toBeNull();
  view.unmount(); qc.clear();
});

it('설정 화면을 다시 열면 이전 저장 완료 표시를 초기화한다', async () => {
  const { qc, wrapper, dialog } = harness();
  const view = render(dialog(), { wrapper });
  fireEvent.change(await screen.findByLabelText('최소 계약 금액 (원)'), { target: { value: '999000' } });
  fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
  await screen.findByText('저장됨');
  view.rerender(dialog(false));
  view.rerender(dialog(true));
  await waitFor(() => expect(screen.getByLabelText('최소 계약 금액 (원)')).toHaveProperty('value', '999000'));
  expect(screen.queryByText('저장됨')).toBeNull();
  view.unmount(); qc.clear();
});

it('확인 필요 알림을 추천 알림과 별도로 저장한다', async () => {
  const { port, qc, wrapper, dialog } = harness();
  const save = vi.spyOn(port, 'updateSettings');
  const view = render(dialog(), { wrapper });
  const toggle = await screen.findByRole('checkbox', { name: '새 제작 의뢰 중 확인 필요' });
  expect(toggle).toHaveProperty('checked', true);
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ notifications: expect.objectContaining({ newReview: false, newRecommended: true }) })));
  await screen.findByText('저장됨');
  view.unmount(); qc.clear();
});

it('복원 캐시 초기화 이후에는 저장 표시를 해제하고 복원된 설정을 표시한다', async () => {
  const { port, qc, wrapper, dialog } = harness();
  const original = structuredClone(await port.getSettings());
  const view = render(dialog(), { wrapper });
  fireEvent.change(await screen.findByLabelText('최소 계약 금액 (원)'), { target: { value: '999000' } });
  fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
  await screen.findByText('저장됨');
  vi.spyOn(port, 'getSettings').mockResolvedValue(original);
  await act(async () => resetDatabaseQueries(qc));
  await waitFor(() => expect(screen.getByLabelText('최소 계약 금액 (원)')).toHaveProperty('value', String(original.profile.minContract ?? '')));
  expect(screen.queryByText('저장됨')).toBeNull();
  view.unmount(); qc.clear();
});

it('사업자 등록·세금계산서 발행 체크박스를 표시하고 저장 시 함께 보낸다', async () => {
  const { port, qc, wrapper, dialog } = harness();
  const save = vi.spyOn(port, 'updateSettings');
  const view = render(dialog(), { wrapper });
  const business = await screen.findByRole('checkbox', { name: '사업자 등록 완료' });
  const tax = screen.getByRole('checkbox', { name: '세금계산서 발행 가능' });
  expect(business).toHaveProperty('checked', false);
  expect(tax).toHaveProperty('checked', false);
  expect(screen.getByText('사업자 등록이 필요한 공고도 자동 지원 후보로 봅니다.')).toBeTruthy();
  expect(screen.getByText('세금계산서 발행을 요구하는 공고도 자동 지원 후보로 봅니다.')).toBeTruthy();
  fireEvent.click(business);
  fireEvent.click(tax);
  fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ profile: expect.objectContaining({ businessRegistered: true, taxInvoice: true }) })));
  await screen.findByText('저장됨');
  view.unmount(); qc.clear();
});
