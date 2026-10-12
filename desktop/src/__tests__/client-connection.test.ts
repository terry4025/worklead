import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClient } from '@contracts/index';

const first = { baseUrl: 'http://127.0.0.1:41001', token: 'first-session' };
const restarted = { baseUrl: 'http://127.0.0.1:41002', token: 'next-session' };

afterEach(() => vi.useRealTimers());

describe('sidecar session changes', () => {
  it('uses the current native connection after the backend restarts', async () => {
    let current = first;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
      expect(String(url)).toBe(`${current.baseUrl}/v1/health`);
      expect(new Headers(init?.headers).get('Authorization')).toBe(`Bearer ${current.token}`);
      return Response.json({ status: 'ok' });
    });
    const client = createClient(async () => current, fetcher);
    await client.health();
    current = restarted;
    await client.health();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('does not replay a failed mutation and uses the new session on a later request', async () => {
    let current = first;
    const fetcher = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(Response.json({ status: 'ok' }));
    const client = createClient(async () => current, fetcher);
    await expect(client.reanalyzeLead('lead-test')).rejects.toMatchObject({ code: 'connection_failed' });
    expect(fetcher).toHaveBeenCalledTimes(1);
    current = restarted;
    await client.health();
    expect(fetcher.mock.calls[1]?.[0]).toBe(`${restarted.baseUrl}/v1/health`);
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get('Authorization')).toBe(`Bearer ${restarted.token}`);
  });

  it('resolves the new session when the event stream reconnects', async () => {
    vi.useFakeTimers();
    let current = first;
    const ctrl = new AbortController();
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => {
        current = restarted;
        throw new TypeError('Backend stopped');
      })
      .mockImplementationOnce(async () => {
        ctrl.abort();
        throw new DOMException('Aborted', 'AbortError');
      });
    const client = createClient(async () => current, fetcher);
    const stream = client.streamEvents({ afterSeq: 12, signal: ctrl.signal, onEvent: vi.fn() });
    await vi.advanceTimersByTimeAsync(1000);
    await stream;
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1]?.[0]).toBe(`${restarted.baseUrl}/v1/events?after_seq=12`);
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get('Authorization')).toBe(`Bearer ${restarted.token}`);
  });

  it('continues to support a fixed development connection', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: 'ok' }));
    await createClient(first, fetcher).health();
    expect(fetcher.mock.calls[0]?.[0]).toBe(`${first.baseUrl}/v1/health`);
  });
});
