import { QueryClient, QueryClientProvider, onlineManager, useMutation, useQuery } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { createLocalQueryClient } from '../app/queryClient';

const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); onlineManager.setOnline(true); });
function wrap(client: QueryClient) {
  clients.push(client);
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

it('기존 기본값은 오프라인에서 초기 목록을 pending/paused로 두지만 로컬 클라이언트는 조회한다', async () => {
  onlineManager.setOnline(false);
  const oldRequest = vi.fn(async () => ['never-loaded']);
  const old = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: oldRequest }), { wrapper: wrap(new QueryClient()) });
  expect(old.result.current.status).toBe('pending'); expect(old.result.current.fetchStatus).toBe('paused');
  expect(oldRequest).not.toHaveBeenCalled();
  const request = vi.fn(async () => ['local-row']);
  const current = renderHook(() => useQuery({ queryKey: ['leads', 'all'], queryFn: request }), { wrapper: wrap(createLocalQueryClient()) });
  await waitFor(() => expect(current.result.current.data).toEqual(['local-row']));
  expect(request).toHaveBeenCalledOnce(); expect(current.result.current.fetchStatus).toBe('idle');
});

it('외부 연결이 오프라인이어도 로컬 갱신과 명령은 바로 완료하고 재연결 때 다시 실행하지 않는다', async () => {
  onlineManager.setOnline(false);
  const client = createLocalQueryClient();
  let rows = ['old'];
  const read = vi.fn(async () => rows), write = vi.fn(async () => 'saved');
  const view = renderHook(() => ({ list: useQuery({ queryKey: ['leads', 'all'], queryFn: read }), command: useMutation({ mutationFn: write }) }), { wrapper: wrap(client) });
  await waitFor(() => expect(view.result.current.list.data).toEqual(['old']));
  rows = ['new'];
  await act(async () => { await client.invalidateQueries({ queryKey: ['leads'] }); await view.result.current.command.mutateAsync(); });
  await waitFor(() => expect(view.result.current.list.data).toEqual(['new']));
  expect(write).toHaveBeenCalledOnce();
  act(() => onlineManager.setOnline(true));
  expect(read).toHaveBeenCalledTimes(2); expect(write).toHaveBeenCalledOnce();
});
