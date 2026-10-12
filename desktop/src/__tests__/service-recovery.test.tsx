import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ServiceRecovery } from '../features/header/ServiceRecovery';
import { restartBackend } from '../platform/bridge';
import { ToastProvider } from '../ui/Toasts';

const invoke = vi.hoisted(() => vi.fn<(command: string) => Promise<unknown>>());
vi.mock('@tauri-apps/api/core', () => ({ invoke }));
const connection = { base_url: 'http://127.0.0.1:41111', token: 'test-session' };
const defer = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
};

beforeEach(() => {
  Object.defineProperty(window, '__TAURI_INTERNALS__', { configurable: true, value: {} });
  invoke.mockReset();
});
afterEach(() => Reflect.deleteProperty(window, '__TAURI_INTERNALS__'));

describe('native service recovery', () => {
  it('waits for readiness and shares overlapping restart requests', async () => {
    const ready = defer<typeof connection>();
    invoke.mockImplementation(async (command) => command === 'backend_connection' ? ready.promise : undefined);
    let completed = false;
    const first = restartBackend().then((ok) => { completed = true; return ok; });
    const second = restartBackend();
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('backend_connection'));
    expect(completed).toBe(false);
    expect(invoke.mock.calls.filter(([command]) => command === 'restart_backend')).toHaveLength(1);
    ready.resolve(connection);
    expect(await first).toBe(true);
    expect(await second).toBe(true);
    invoke.mockResolvedValue(connection);
    expect(await restartBackend()).toBe(true);
    expect(invoke.mock.calls.filter(([command]) => command === 'restart_backend')).toHaveLength(2);
  });

  it('reports a failed replacement and permits a later user attempt', async () => {
    invoke.mockImplementation(async (command) => {
      if (command === 'backend_connection') throw 'replacement failed';
    });
    expect(await restartBackend()).toBe(false);
    invoke.mockResolvedValue(connection);
    expect(await restartBackend()).toBe(true);
  });

  it('does not invoke native commands from a browser', async () => {
    Reflect.deleteProperty(window, '__TAURI_INTERNALS__');
    expect(await restartBackend()).toBe(false);
    expect(invoke).not.toHaveBeenCalled();
  });

  it('offers recovery after an established workspace loses its service', async () => {
    const ready = defer<typeof connection>();
    invoke.mockImplementation(async (command) => command === 'backend_connection' ? ready.promise : undefined);
    const view = render(<ToastProvider><ServiceRecovery mode="live" stream="retrying" /></ToastProvider>);
    const button = screen.getByRole('button', { name: '서비스 다시 시작' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect((screen.getByRole('button', { name: '서비스 연결 중…' }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('backend_connection'));
    expect(invoke.mock.calls.filter(([command]) => command === 'restart_backend')).toHaveLength(1);
    await act(async () => { ready.resolve(connection); await ready.promise; });
    await waitFor(() => expect((screen.getByRole('button', { name: '서비스 다시 시작' }) as HTMLButtonElement).disabled).toBe(false));
    view.rerender(<ToastProvider><ServiceRecovery mode="live" stream="open" /></ToastProvider>);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows readiness failure without replaying the restart', async () => {
    invoke.mockRejectedValue('start failed');
    render(<ToastProvider><ServiceRecovery mode="live" stream="closed" /></ToastProvider>);
    fireEvent.click(screen.getByRole('button', { name: '서비스 다시 시작' }));
    await screen.findByText('로컬 서비스를 다시 시작하지 못했습니다. 앱을 종료한 뒤 다시 실행해주세요.');
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it.each(['connecting', 'open'] as const)('does not offer a disruptive restart when %s', (stream) => {
    render(<ToastProvider><ServiceRecovery mode="live" stream={stream} /></ToastProvider>);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('does not offer native recovery in demo mode', () => {
    render(<ToastProvider><ServiceRecovery mode="demo" stream="closed" /></ToastProvider>);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
