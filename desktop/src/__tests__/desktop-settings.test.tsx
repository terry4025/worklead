import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { invoke } from '@tauri-apps/api/core';
import { StrictMode, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { MockPort } from '../data/mock/mockPort';
import { DesktopSettingsPanel } from '../features/settings/DesktopSettingsPanel';
import { SettingsDialog } from '../features/settings/SettingsDialog';
import { isTauri } from '../platform/bridge';
import { desktopExit, desktopHide, getDesktopSettings, updateDesktopSettings, type DesktopSettings, type DesktopSettingsPatch } from '../platform/desktop';

vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn() }));
vi.mock('../platform/bridge', async (original) => ({ ...await original<typeof import('../platform/bridge')>(), isTauri: vi.fn() }));

const labels = {
  close_to_tray: '창을 닫으면 트레이로 숨기기',
  native_notifications: 'Windows 알림 사용',
  autostart_enabled: 'Windows 로그인 시 자동 시작',
};
const defaults: DesktopSettings = { close_to_tray: false, native_notifications: false, autostart_enabled: false, tray_available: true };
let current: DesktopSettings;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function input(key: keyof typeof labels) {
  return screen.getByRole('checkbox', { name: labels[key] }) as HTMLInputElement;
}

async function loaded() {
  await waitFor(() => expect(input('native_notifications').disabled).toBe(false));
}

function calls(command: string) {
  return vi.mocked(invoke).mock.calls.filter(([name]) => name === command);
}

beforeEach(() => {
  current = { ...defaults };
  vi.mocked(isTauri).mockReturnValue(true);
  vi.mocked(invoke).mockReset();
  vi.mocked(invoke).mockImplementation(async (command, args) => {
    if (command === 'get_desktop_settings') return { ...current };
    if (command === 'update_desktop_settings') {
      current = { ...current, ...(args as { patch: DesktopSettingsPatch }).patch };
      return { ...current };
    }
    return undefined;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});

describe('데스크톱 명령 경계', () => {
  it('Rust 명령 이름과 patch 계약을 그대로 전달한다', async () => {
    expect(await getDesktopSettings()).toEqual(defaults);
    expect(await updateDesktopSettings({ native_notifications: true })).toEqual({ ...defaults, native_notifications: true });
    await desktopHide();
    await desktopExit();
    expect(vi.mocked(invoke).mock.calls).toEqual([
      ['get_desktop_settings', undefined],
      ['update_desktop_settings', { patch: { native_notifications: true } }],
      ['desktop_hide', undefined],
      ['desktop_exit', undefined],
    ]);
  });

  it('브라우저에서 네이티브 명령을 실행하지 않는다', async () => {
    vi.mocked(isTauri).mockReturnValue(false);
    for (const task of [getDesktopSettings, () => updateDesktopSettings({ close_to_tray: true }), desktopHide, desktopExit]) {
      await expect(task()).rejects.toThrow('데스크톱 앱에서만 사용할 수 있습니다.');
    }
    expect(invoke).not.toHaveBeenCalled();
  });
});

describe('네이티브 설정 패널', () => {
  it('브라우저에서는 패널을 숨기고 설정 조회도 하지 않는다', () => {
    vi.mocked(isTauri).mockReturnValue(false);
    render(<DesktopSettingsPanel />);
    expect(screen.queryByText('데스크톱 (이 PC)')).toBeNull();
    expect(invoke).not.toHaveBeenCalled();
  });

  it('기본 설정은 모두 꺼져 있고 즉시 저장·알림 범위를 설명한다', async () => {
    render(<DesktopSettingsPanel />);
    await loaded();
    for (const key of Object.keys(labels) as (keyof typeof labels)[]) expect(input(key).checked).toBe(false);
    expect(screen.getByText(/변경 즉시 저장됩니다/)).toBeTruthy();
    expect(screen.getByText(/OS 알림에는 리드 제목·연락처를 넣지 않습니다/)).toBeTruthy();
    expect(screen.getByText(/조용한 시간 설정이 적용됩니다/)).toBeTruthy();
    expect(screen.getByText(/다시 켠 시점 이후의 알림만 표시/)).toBeTruthy();
    expect(screen.getByText(/Windows 알림·방해 금지 설정에 따라 보이지 않을 수/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /알림.*(?:테스트|미리 보기)|(?:테스트|미리 보기).*알림/ })).toBeNull();
    expect(calls('get_desktop_settings')).toHaveLength(1);
    expect(calls('update_desktop_settings')).toHaveLength(0);
  });

  it('저장된 설정을 불러오고 기본값으로 덮어쓰지 않는다', async () => {
    current = { close_to_tray: true, native_notifications: true, autostart_enabled: true, tray_available: true };
    render(<DesktopSettingsPanel />);
    await loaded();
    for (const key of Object.keys(labels) as (keyof typeof labels)[]) expect(input(key).checked).toBe(true);
    expect(calls('update_desktop_settings')).toHaveLength(0);
  });

  it('트레이 생성 실패 시 숨김 기능만 비활성화한다', async () => {
    current.tray_available = false;
    render(<DesktopSettingsPanel />);
    await loaded();
    expect(input('close_to_tray').disabled).toBe(true);
    const hide = screen.getByRole('button', { name: '트레이로 숨기기' }) as HTMLButtonElement;
    expect(hide.disabled).toBe(true);
    expect(input('autostart_enabled').disabled).toBe(false);
    expect(screen.getByText(/트레이를 사용할 수 없어/)).toBeTruthy();
    fireEvent.click(input('close_to_tray'));
    fireEvent.click(hide);
    expect(calls('update_desktop_settings')).toHaveLength(0);
    expect(calls('desktop_hide')).toHaveLength(0);
    fireEvent.click(input('native_notifications'));
    await waitFor(() => expect(input('native_notifications').checked).toBe(true));
    expect(calls('update_desktop_settings')).toEqual([['update_desktop_settings', { patch: { native_notifications: true } }]]);
  });

  it.each(Object.keys(labels) as (keyof typeof labels)[])('%s는 변경한 필드만 즉시 저장한다', async (key) => {
    render(<DesktopSettingsPanel />);
    await loaded();
    fireEvent.click(input(key));
    await screen.findByText('데스크톱 설정 저장됨');
    expect(input(key).checked).toBe(true);
    expect(calls('update_desktop_settings')).toEqual([['update_desktop_settings', { patch: { [key]: true } }]]);
    expect(screen.queryByRole('button', { name: /^저장$/ })).toBeNull();
  });

  it('설정 저장을 기다리는 동안 같은 프레임의 중복 클릭과 다른 명령을 막는다', async () => {
    render(<DesktopSettingsPanel />);
    await loaded();
    const save = deferred<DesktopSettings>();
    vi.mocked(invoke).mockReturnValueOnce(save.promise);
    act(() => {
      fireEvent.click(input('native_notifications'));
      fireEvent.click(input('native_notifications'));
      fireEvent.click(input('autostart_enabled'));
      fireEvent.click(screen.getByRole('button', { name: 'Worklead 종료' }));
    });
    await waitFor(() => expect(calls('update_desktop_settings')).toHaveLength(1));
    expect(screen.getByText('데스크톱 설정 저장 중…')).toBeTruthy();
    for (const key of Object.keys(labels) as (keyof typeof labels)[]) expect(input(key).disabled).toBe(true);
    expect(calls('desktop_exit')).toHaveLength(0);
    expect(input('native_notifications').checked).toBe(false);
    await act(async () => save.resolve({ ...defaults, native_notifications: true }));
    await loaded();
    expect(input('native_notifications').checked).toBe(true);
    expect(input('autostart_enabled').checked).toBe(false);
  });

  it('조회 오류는 한국어로 표시하고 읽기 재시도만 한다', async () => {
    vi.mocked(invoke).mockRejectedValueOnce(new Error('private-native-error'));
    render(<DesktopSettingsPanel />);
    await screen.findByRole('alert');
    expect(screen.getByText(/데스크톱 설정을 불러오지 못했습니다/)).toBeTruthy();
    expect(screen.queryByText('private-native-error')).toBeNull();
    expect(input('native_notifications').disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '데스크톱 설정 다시 불러오기' }));
    await loaded();
    expect(calls('get_desktop_settings')).toHaveLength(2);
    expect(calls('update_desktop_settings')).toHaveLength(0);
  });

  it('저장 응답 오류는 완료로 표시하지 않고 조회로 실제 저장 상태를 확인한다', async () => {
    render(<DesktopSettingsPanel />);
    await loaded();
    // Simulate a persisted update whose command response was lost.
    current.native_notifications = true;
    vi.mocked(invoke).mockRejectedValueOnce(new Error('private-native-error'));
    fireEvent.click(input('native_notifications'));
    await screen.findByText('데스크톱 설정 저장 결과를 확인하지 못했습니다. 설정을 다시 불러오세요.');
    expect(input('native_notifications').checked).toBe(false);
    expect(screen.queryByText('데스크톱 설정 저장됨')).toBeNull();
    expect(screen.queryByText('private-native-error')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '데스크톱 설정 다시 불러오기' }));
    await waitFor(() => expect(input('native_notifications').checked).toBe(true));
    expect(calls('update_desktop_settings')).toHaveLength(1);
    expect(calls('get_desktop_settings')).toHaveLength(2);
  });

  it('StrictMode의 이전 조회가 늦게 도착해도 최신 조회를 덮지 않는다', async () => {
    const old = deferred<DesktopSettings>();
    current.native_notifications = true;
    vi.mocked(invoke).mockReturnValueOnce(old.promise);
    render(<StrictMode><DesktopSettingsPanel /></StrictMode>);
    await waitFor(() => expect(calls('get_desktop_settings')).toHaveLength(2));
    await expect(vi.mocked(invoke).mock.results[1]!.value).resolves.toEqual({ ...defaults, native_notifications: true });
    await loaded();
    expect(input('native_notifications').checked).toBe(true);
    await act(async () => old.resolve(defaults));
    expect(input('native_notifications').checked).toBe(true);
    expect(calls('get_desktop_settings')).toHaveLength(2);
  });

  it.each([['트레이로 숨기기', 'desktop_hide'], ['Worklead 종료', 'desktop_exit']])('%s는 pending 중 한 번만 실행한다', async (label, command) => {
    render(<DesktopSettingsPanel />);
    await loaded();
    const action = deferred<void>();
    vi.mocked(invoke).mockReturnValueOnce(action.promise);
    const button = screen.getByRole('button', { name: label! });
    act(() => { fireEvent.click(button); fireEvent.click(button); });
    await waitFor(() => expect(calls(command!)).toHaveLength(1));
    expect(button).toHaveProperty('disabled', true);
    await act(async () => action.resolve(undefined));
    await waitFor(() => expect(button).toHaveProperty('disabled', false));
  });

  it.each([['트레이로 숨기기', '트레이로 숨기지 못했습니다. 다시 시도하세요.'], ['Worklead 종료', '앱을 종료하지 못했습니다. 다시 시도하세요.']])('%s 오류를 한국어로 표시한다', async (label, message) => {
    render(<DesktopSettingsPanel />);
    await loaded();
    vi.mocked(invoke).mockRejectedValueOnce(new Error('private-native-error'));
    fireEvent.click(screen.getByRole('button', { name: label! }));
    await screen.findByText(message!);
    expect(screen.queryByText('private-native-error')).toBeNull();
  });
});

it('기존 설정 저장과 PC 설정 즉시 저장을 분리하고 편집 중인 조건을 보존한다', async () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const save = vi.spyOn(port, 'updateSettings');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'demo', scenario: 'normal', setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  const view = render(<SettingsDialog open onClose={() => undefined} fontScale="normal" setFontScale={() => undefined} theme="system" setTheme={() => undefined} />, { wrapper });
  const money = await screen.findByLabelText('최소 계약 금액 (원)');
  fireEvent.change(money, { target: { value: '777000' } });
  await loaded();
  fireEvent.click(input('autostart_enabled'));
  await screen.findByText('데스크톱 설정 저장됨');
  expect(save).not.toHaveBeenCalled();
  expect(screen.queryByText('저장됨')).toBeNull();
  expect(money).toHaveProperty('value', '777000');
  fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ profile: expect.objectContaining({ minContract: 777000 }) })));
  expect(calls('update_desktop_settings')).toHaveLength(1);
  expect(save.mock.calls[0]![0]).not.toHaveProperty('desktop');
  view.unmount();
  qc.clear();
});
