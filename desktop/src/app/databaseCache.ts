import type { QueryClient } from '@tanstack/react-query';

// Query cancellation does not cover direct requests or mutation responses.
// Their captured epoch must still match before they write to database caches.
const epochs = new WeakMap<QueryClient, number>();

export function getDatabaseEpoch(qc: QueryClient): number {
  return epochs.get(qc) ?? 0;
}

export async function resetDatabaseQueries(qc: QueryClient): Promise<void> {
  epochs.set(qc, getDatabaseEpoch(qc) + 1);
  await qc.cancelQueries();
  await qc.resetQueries({}, { throwOnError: true });
}
