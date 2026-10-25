import { QueryClient } from '@tanstack/react-query';

export function createLocalQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Data comes from this PC. navigator.onLine describes outside network
        // connectivity and must not pause loopback/SQLite reads indefinitely.
        networkMode: 'always',
        retry: (count, err) => count < 1 && (err as { kind?: string })?.kind === 'connection',
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      // Local commands must complete/fail now, rather than being queued for an
      // unexpected replay when the outside network comes back.
      mutations: { retry: false, networkMode: 'always' },
    },
  });
}
