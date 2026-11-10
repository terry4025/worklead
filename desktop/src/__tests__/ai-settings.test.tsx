import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { resetDatabaseQueries } from '../app/databaseCache';
import { qk } from '../app/queries';
import { MockPort } from '../data/mock/mockPort';
import { PortError } from '../data/port';
import type { AiStatus, SettingsView } from '../domain/model';
import { AI_STATUS_KEY } from '../features/settings/AiSettingsPanel';
import { SettingsDialog } from '../features/settings/SettingsDialog';

const owned: { qc: QueryClient; port: MockPort }[] = [];
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const KEY = 'synthetic-ui-secret-do-not-store';
afterEach(() => { vi.restoreAllMocks(); owned.splice(0).forEach(({ qc, port }) => { qc.clear(); port.dispose(); }); Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal'); Reflect.deleteProperty(HTMLDialogElement.prototype, 'close'); localStorage.clear(); sessionStorage.clear(); });

async function harness(options: { configured?: boolean; enabled?: boolean; mode?: 'live' | 'demo'; unknown?: boolean; ledger?: AiStatus['ledger'] } = {}) {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function(this: HTMLDialogElement) { this.open = false; } });
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  vi.spyOn(port, 'listBackups').mockResolvedValue({ directory: 'owned-test-backups', items: [] });
  const server = await port.getSettings();
  server.ai.keyConfigured = options.unknown ? null : options.configured ?? false;
  server.ai.providerAvailable = options.configured ?? false;
  if (options.enabled) Object.assign(server.ai, { enabled: true, externalTransferConsent: true, pricingConfirmed: true, usdToKrwRate: 1400, dailyCostCap: 1000, monthlyCostCap: 10000 });
  const makeStatus = (): AiStatus => ({ provider: server.ai.provider, model: server.ai.model, keySupported: true, keyConfigured: server.ai.keyConfigured,
    errorCode: options.unknown ? 'key_unavailable' : null, providerAvailable: server.ai.providerAvailable, enabled: server.ai.enabled,
    ready: server.ai.enabled, blockedReason: server.ai.enabled ? null : 'disabled', settingsRevision: server.ai.revision, databaseGeneration: server.ai.databaseGeneration,
    pricingCheckedAt: '2026-09-30', price: { inputUsdPerMillion: 0.4, cachedInputUsdPerMillion: 0.1, outputUsdPerMillion: 1.6, sourceUrl: 'https://platform.openai.com/docs/models/gpt-4.1-mini', version: 'test' },
    ledger: options.ledger === undefined ? { day: { period: '2026-09-30', estimatedKrw: 0, reservedKrw: 0, uncertainKrw: 0, attempts: 0 }, month: { period: '2026-09', estimatedKrw: 0, reservedKrw: 0, uncertainKrw: 0, attempts: 0 } } : options.ledger, callsToday: null });
  vi.spyOn(port, 'getSettings').mockImplementation(async () => structuredClone(server));
  const read = vi.spyOn(port, 'getAiStatus').mockImplementation(async () => makeStatus());
  const save = vi.spyOn(port, 'saveAiCredential').mockImplementation(async () => { server.ai.keyConfigured = true; server.ai.providerAvailable = true; server.ai.enabled = false; server.ai.revision += 1; return makeStatus(); });
  const remove = vi.spyOn(port, 'deleteAiCredential').mockImplementation(async () => { server.ai.keyConfigured = false; server.ai.providerAvailable = false; server.ai.enabled = false; server.ai.revision += 1; return makeStatus(); });
  const update = vi.spyOn(port, 'updateSettings').mockImplementation(async (patch) => {
    if (patch.profile) server.profile = structuredClone(patch.profile);
    if (patch.ai) { const { expectedRevision, expectedGeneration, ...rest } = patch.ai; expect(expectedRevision).toBe(server.ai.revision); expect(expectedGeneration).toBe(server.ai.databaseGeneration); Object.assign(server.ai, rest); server.ai.revision += 1; }
    return structuredClone(server);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); owned.push({ qc, port });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: options.mode ?? 'live', scenario: 'normal', setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  const dialog = (open = true) => <SettingsDialog open={open} onClose={() => undefined} fontScale="normal" setFontScale={() => undefined} theme="light" setTheme={() => undefined} />;
  const view = render(dialog(), { wrapper });
  await screen.findByLabelText('AI 제공자'); await waitFor(() => expect(read).toHaveBeenCalled());
  return { port, qc, server, makeStatus, read, save, remove, update, view, dialog };
}

function enablePrerequisites() {
  fireEvent.change(screen.getByLabelText('달러 환산 (1 USD 당 원)'), { target: { value: '1400' } });
  fireEvent.change(screen.getByLabelText('일 비용 상한 (원)'), { target: { value: '1000' } });
  fireEvent.change(screen.getByLabelText('월 비용 상한 (원)'), { target: { value: '10000' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '위 모델 요금과 달러 환산 값을 확인했습니다' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '위 텍스트의 OpenAI 외부 전송에 동의합니다' }));
}

describe('선택 AI 설정과 비밀 입력', () => {
  it('기본 꺼짐이며 일반 설정 저장은 키/AI 요청을 보내지 않는다', async () => {
    const { save, remove, update } = await harness();
    const enabled = screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' }) as HTMLInputElement;
    expect(enabled.checked).toBe(false); expect(enabled.disabled).toBe(true);
    expect(screen.getByLabelText('OpenAI API 키')).toHaveProperty('type', 'password');
    fireEvent.change(screen.getByLabelText('최소 계약 금액 (원)'), { target: { value: '555000' } });
    fireEvent.click(screen.getByRole('button', { name: /^저장$/ })); await screen.findByText('저장됨');
    expect(update.mock.calls[0]?.[0].ai).toBeUndefined(); expect(save).not.toHaveBeenCalled(); expect(remove).not.toHaveBeenCalled();
  });

  it('키가 있어도 동의와 요금 확인을 완료하기 전에는 켤 수 없다', async () => {
    await harness({ configured: true });
    fireEvent.change(screen.getByLabelText('달러 환산 (1 USD 당 원)'), { target: { value: '1400' } });
    fireEvent.change(screen.getByLabelText('일 비용 상한 (원)'), { target: { value: '1000' } });
    fireEvent.change(screen.getByLabelText('월 비용 상한 (원)'), { target: { value: '10000' } });
    const enabled = screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' }); expect(enabled).toHaveProperty('disabled', true);
    fireEvent.click(screen.getByRole('checkbox', { name: '위 텍스트의 OpenAI 외부 전송에 동의합니다' })); expect(enabled).toHaveProperty('disabled', true);
    fireEvent.click(screen.getByRole('checkbox', { name: '위 모델 요금과 달러 환산 값을 확인했습니다' })); expect(enabled).toHaveProperty('disabled', false);
  });

  it('키는 한 번 별도 전송하고 즉시 비우며 캐시/브라우저 저장소에 남기지 않는다', async () => {
    const { save, makeStatus, server, qc } = await harness(); const pending = deferred<AiStatus>(); save.mockReturnValueOnce(pending.promise);
    fireEvent.change(screen.getByLabelText('최소 계약 금액 (원)'), { target: { value: '888000' } });
    fireEvent.change(screen.getByLabelText('OpenAI API 키'), { target: { value: KEY } });
    const button = screen.getByRole('button', { name: '키 저장' }); fireEvent.click(button); fireEvent.click(button);
    expect(screen.getByLabelText('OpenAI API 키')).toHaveProperty('value', '');
    expect(save).toHaveBeenCalledOnce(); expect(save).toHaveBeenCalledWith({ key: KEY, expectedRevision: 0, expectedGeneration: 0 });
    server.ai.revision = 1; server.ai.keyConfigured = true; server.ai.providerAvailable = true;
    await act(async () => pending.resolve(makeStatus())); await screen.findByText(/API 키를 Windows 보안 저장소에 저장했습니다/);
    expect(screen.getByLabelText('최소 계약 금액 (원)')).toHaveProperty('value', '888000');
    expect(JSON.stringify(qc.getQueryCache().getAll().map((q) => q.state))).not.toContain(KEY);
    expect(JSON.stringify(qc.getMutationCache().getAll().map((m) => m.state))).not.toContain(KEY);
    expect(JSON.stringify(localStorage)).not.toContain(KEY); expect(JSON.stringify(sessionStorage)).not.toContain(KEY);
  });

  it('키 삭제는 CAS만 보내고 켜진 AI를 끈다', async () => {
    const { remove } = await harness({ configured: true, enabled: true });
    await screen.findByText('현재 저장 상태: AI 실행 조건 충족 · API 연결 미검증');
    fireEvent.click(screen.getByRole('button', { name: '키 삭제' })); await screen.findByText(/API 키를 삭제했습니다/);
    expect(remove).toHaveBeenCalledOnce(); expect(remove).toHaveBeenCalledWith({ expectedRevision: 0, expectedGeneration: 0 });
    expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('checked', false);
  });

  it('동의·가격 확인·환산·상한을 채운 뒤 최신 CAS로 AI를 저장한다', async () => {
    const { update } = await harness({ configured: true }); enablePrerequisites();
    const enabled = screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' }); expect(enabled).toHaveProperty('disabled', false); fireEvent.click(enabled);
    fireEvent.click(screen.getByRole('button', { name: /^저장$/ })); await screen.findByText('저장됨');
    expect(update.mock.calls[0]?.[0].ai).toMatchObject({ enabled: true, externalTransferConsent: true, pricingConfirmed: true, usdToKrwRate: 1400, dailyCostCap: 1000, monthlyCostCap: 10000, expectedRevision: 0, expectedGeneration: 0 });
    expect(Object.hasOwn(update.mock.calls[0]![0].ai!, 'key')).toBe(false);
    expect(Object.hasOwn(update.mock.calls[0]![0].ai!, 'keyConfigured')).toBe(false);
  });

  it.each(['일 비용 상한 (원)', '월 비용 상한 (원)'])('%s의 미설정과 0원은 AI 활성화를 막는다', async (label) => {
    await harness({ configured: true }); enablePrerequisites();
    fireEvent.change(screen.getByLabelText(label), { target: { value: '' } }); expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('disabled', true);
    fireEvent.change(screen.getByLabelText(label), { target: { value: '0' } }); expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('disabled', true);
  });

  it('환산 수정과 전송/요금 동의 해제는 활성화와 요금 확인을 해제한다', async () => {
    await harness({ configured: true }); enablePrerequisites(); fireEvent.click(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' }));
    fireEvent.change(screen.getByLabelText('달러 환산 (1 USD 당 원)'), { target: { value: '1450' } });
    expect(screen.getByRole('checkbox', { name: '위 모델 요금과 달러 환산 값을 확인했습니다' })).toHaveProperty('checked', false);
    expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('checked', false);
    fireEvent.click(screen.getByRole('checkbox', { name: '위 모델 요금과 달러 환산 값을 확인했습니다' })); fireEvent.click(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '위 텍스트의 OpenAI 외부 전송에 동의합니다' }));
    expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('checked', false);
  });

  it('키 응답 유실은 GET으로만 확인하고 키/오류 원문을 표시하지 않는다', async () => {
    const { save, read, server } = await harness();
    save.mockImplementationOnce(async () => { server.ai.revision = 1; server.ai.enabled = false; server.ai.keyConfigured = true; server.ai.providerAvailable = true; throw new Error(KEY); });
    fireEvent.change(screen.getByLabelText('OpenAI API 키'), { target: { value: KEY } }); fireEvent.click(screen.getByRole('button', { name: '키 저장' }));
    await screen.findByText(/키 변경 결과를 확인하지 못했습니다/); await screen.findByText('Windows 보안 저장소에 저장됨');
    expect(save).toHaveBeenCalledOnce(); expect(read).toHaveBeenCalledTimes(2); expect(document.body.textContent).not.toContain(KEY); expect(screen.getByLabelText('OpenAI API 키')).toHaveProperty('value', '');
  });

  it('키 변경 뒤 상태 읽기까지 실패하면 자동 쓰기 없이 미확인 상태를 유지한다', async () => {
    const { save, read } = await harness(); save.mockRejectedValueOnce(new Error(KEY)); read.mockRejectedValueOnce(new Error(KEY));
    fireEvent.change(screen.getByLabelText('OpenAI API 키'), { target: { value: KEY } }); fireEvent.click(screen.getByRole('button', { name: '키 저장' }));
    await screen.findByText('저장 상태 확인 불가'); expect(screen.getByRole('button', { name: '키 저장' })).toHaveProperty('disabled', true);
    expect(save).toHaveBeenCalledOnce(); expect(read).toHaveBeenCalledTimes(2); expect(document.body.textContent).not.toContain(KEY);
  });

  it('오래된 상태와 AI 폼 revision이 다르면 쓰기를 막고 AI만 다시 읽는다', async () => {
    const { server, qc } = await harness({ configured: true });
    fireEvent.change(screen.getByLabelText('최소 계약 금액 (원)'), { target: { value: '777000' } }); server.ai.revision = 4;
    await act(async () => qc.invalidateQueries({ queryKey: AI_STATUS_KEY })); await screen.findByText(/AI 설정 또는 서비스가 변경됐습니다/);
    enablePrerequisites(); expect(screen.getByRole('button', { name: /^저장$/ })).toHaveProperty('disabled', true);
    fireEvent.click(screen.getByRole('button', { name: 'AI 상태 다시 불러오기' })); await screen.findByText(/다른 편집 내용은 유지했습니다/);
    expect(screen.getByLabelText('최소 계약 금액 (원)')).toHaveProperty('value', '777000');
  });

  it('복원 뒤 늦게 도착한 키 응답은 새 캐시/폼을 덮지 않는다', async () => {
    const { save, server, makeStatus, qc } = await harness(); const pending = deferred<AiStatus>(); save.mockReturnValueOnce(pending.promise);
    fireEvent.change(screen.getByLabelText('OpenAI API 키'), { target: { value: KEY } }); fireEvent.click(screen.getByRole('button', { name: '키 저장' }));
    const obsolete = { ...makeStatus(), settingsRevision: 1, keyConfigured: true };
    server.ai.databaseGeneration = 321; server.ai.revision = 8; server.ai.enabled = false; server.ai.externalTransferConsent = false;
    await act(async () => resetDatabaseQueries(qc)); await act(async () => pending.resolve(obsolete));
    await waitFor(() => expect(qc.getQueryData<AiStatus>(AI_STATUS_KEY)?.databaseGeneration).toBe(321));
    expect(qc.getQueryData<SettingsView>(qk.settings)?.ai.revision).toBe(8); expect(screen.queryByText(/API 키를 Windows 보안 저장소에 저장했습니다/)).toBeNull();
  });

  it('창을 닫으면 입력을 비우고 이전 키 응답이 재개한 화면을 덮지 않는다', async () => {
    const { view, dialog, save, makeStatus, qc } = await harness(); const pending = deferred<AiStatus>(); save.mockReturnValueOnce(pending.promise);
    fireEvent.change(screen.getByLabelText('OpenAI API 키'), { target: { value: KEY } }); fireEvent.click(screen.getByRole('button', { name: '키 저장' }));
    view.rerender(dialog(false)); view.rerender(dialog(true)); await screen.findByLabelText('OpenAI API 키');
    await act(async () => pending.resolve({ ...makeStatus(), settingsRevision: 1, keyConfigured: true }));
    expect(screen.getByLabelText('OpenAI API 키')).toHaveProperty('value', ''); expect(qc.getQueryData<AiStatus>(AI_STATUS_KEY)?.keyConfigured).toBe(false);
  });

  it('비용 사용/예약/과금 미확인을 별도 표시하고 미확인 저장소와 ledger는 활성화를 막는다', async () => {
    await harness({ unknown: true, ledger: { day: { period: '2026-09-30', estimatedKrw: 11, reservedKrw: 22, uncertainKrw: 33, attempts: 3 }, month: { period: '2026-09', estimatedKrw: 101, reservedKrw: 202, uncertainKrw: 303, attempts: 30 } } });
    expect(screen.getByText(/사용 추정 11원 · 예약 22원 · 과금 미확인 33원/)).toBeTruthy(); enablePrerequisites();
    expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('disabled', true);
  });

  it('비용 기록을 확인할 수 없거나 과금 미확인 금액까지 상한을 채우면 켤 수 없다', async () => {
    const { qc, makeStatus } = await harness({ configured: true, ledger: null }); enablePrerequisites();
    expect(screen.getByText(/비용 기록을 확인하지 못해 AI 요청이 중지됩니다/)).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('disabled', true);
    await act(async () => qc.setQueryData(AI_STATUS_KEY, { ...makeStatus(), ledger: { day: { period: '2026-09-30', estimatedKrw: 100, reservedKrw: 200, uncertainKrw: 700, attempts: 3 }, month: { period: '2026-09', estimatedKrw: 100, reservedKrw: 200, uncertainKrw: 700, attempts: 3 } } }));
    expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('disabled', true);
    await screen.findByText(/상한 1,000원 · 요청 3회 · 상한 도달/);
  });

  it('키 저장 전에 시작한 상태 GET이 늦어도 최신 key revision을 낮추지 않는다', async () => {
    const { qc, read, makeStatus } = await harness(); const oldStatus = makeStatus(); const pending = deferred<AiStatus>(); read.mockReturnValueOnce(pending.promise);
    let refresh!: Promise<void>; await act(async () => { refresh = qc.invalidateQueries({ queryKey: AI_STATUS_KEY }); });
    fireEvent.change(screen.getByLabelText('OpenAI API 키'), { target: { value: KEY } }); fireEvent.click(screen.getByRole('button', { name: '키 저장' }));
    await screen.findByText(/API 키를 Windows 보안 저장소에 저장했습니다/);
    await act(async () => { pending.resolve(oldStatus); await refresh; });
    expect(qc.getQueryData<AiStatus>(AI_STATUS_KEY)).toMatchObject({ settingsRevision: 1, keyConfigured: true });
    expect(screen.queryByText(/AI 설정 또는 서비스가 변경됐습니다/)).toBeNull();
  });

  it('demo는 실제 키 저장 입력을 비활성화한다', async () => {
    const { save } = await harness({ mode: 'demo' }); expect(screen.getByLabelText('OpenAI API 키')).toHaveProperty('disabled', true);
    expect(screen.getByText(/데모에서는 실제 API 키를 입력하거나 저장하지 않습니다/)).toBeTruthy(); expect(save).not.toHaveBeenCalled();
  });

  it('상태 조회 실패는 원문 오류 없이 표시하고 키 쓰기를 막는다', async () => {
    const { read, qc, save } = await harness(); read.mockRejectedValue(new PortError({ kind: 'connection', code: 'connection_failed', message: KEY, retryable: true }));
    await act(async () => qc.invalidateQueries({ queryKey: AI_STATUS_KEY })); await screen.findByText(/AI 상태를 불러오지 못했습니다/);
    expect(screen.getByRole('button', { name: '키 저장' })).toHaveProperty('disabled', true); expect(document.body.textContent).not.toContain(KEY); expect(save).not.toHaveBeenCalled();
  });

  it('키보드 Space로 동의하고 잘못된 토큰 상한은 저장을 막는다', async () => {
    await harness({ configured: true }); const user = userEvent.setup(); const consent = screen.getByRole('checkbox', { name: '위 텍스트의 OpenAI 외부 전송에 동의합니다' }); consent.focus(); await user.keyboard(' ');
    expect(consent).toHaveProperty('checked', true);
    fireEvent.change(screen.getByLabelText('응답 토큰 상한 (256–4,096)'), { target: { value: '255' } });
    expect(screen.getByRole('button', { name: /^저장$/ })).toHaveProperty('disabled', true); expect(screen.getByLabelText('응답 토큰 상한 (256–4,096)').getAttribute('aria-invalid')).toBe('true');
  });
});

describe('EXAONE 설정', () => {
  it('EXAONE을 고르면 요금 입력 대신 호출 상한과 기능 스위치를 보인다', async () => {
    await harness();
    fireEvent.change(screen.getByLabelText('AI 제공자'), { target: { value: 'exaone' } });
    expect(await screen.findByLabelText('EXAONE API 키')).toBeTruthy();
    expect(screen.getByLabelText('하루 호출 상한')).toBeTruthy();
    expect(screen.queryByLabelText('달러 환산 (1 USD 당 원)')).toBeNull();
    for (const name of ['검토 보조 분석', '제외 공고 AI 재검토', '맞춤 지원 문구 (자동 제출)', '채팅 답장 초안', '견적서 초안'])
      expect((screen.getByRole('checkbox', { name }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByRole('checkbox', { name: '위 텍스트의 LG EXAONE 외부 전송에 동의합니다' })).toBeTruthy();
    const notice = screen.getByText(/학습 거부를 설정하기 전까지/).textContent ?? '';
    expect(notice).toContain('LG EXAONE');
    expect(notice).not.toContain('OpenAI');
    expect(screen.getByText(/켜려면/).textContent).not.toMatch(/요금|환산/);
  });

  it('제공자를 저장하기 전에는 EXAONE 키 저장과 AI 켜기를 막는다', async () => {
    await harness({ mode: 'live', configured: true });
    expect(screen.getByLabelText('OpenAI API 키')).toHaveProperty('disabled', false);
    fireEvent.change(screen.getByLabelText('AI 제공자'), { target: { value: 'exaone' } });
    expect(await screen.findByLabelText('EXAONE API 키')).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: '키 저장' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('checkbox', { name: 'AI 보조 분석 사용' })).toHaveProperty('disabled', true);
    expect(screen.getByText('제공자를 저장한 뒤 키를 입력하세요.')).toBeTruthy();
  });
  it('프로필에 포트폴리오·기준가, 수집 절전 방지 옵션이 있다', async () => {
    await harness();
    expect((screen.getByLabelText('포트폴리오 주소') as HTMLInputElement).value).toBe('https://www.seyj.kr/');
    expect(screen.getByLabelText('기준가 안내')).toBeTruthy();
    expect((screen.getByRole('checkbox', { name: '수집 중 PC 절전 방지' }) as HTMLInputElement).checked).toBe(false);
  });
});
