/**
 * 데이터 조회·변경 훅. 목록은 사용자가 읽는 동안 순서를 바꾸지 않도록,
 * 변경은 기존 목록 캐시의 해당 행만 고치고(재정렬 없음) 새로고침은 사용자가 결정한다.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData, type QueryClient } from '@tanstack/react-query';
import type { LeadDetail, LeadFilter, LeadSummary, Page, QueueId } from '../domain/model';
import { toSummary } from '../domain/summary';
import type { WorkleadPort } from '../data/port';
import { usePort } from './PortContext';

export const PAGE_SIZE = 40;

export const qk = {
  bootstrap: ['bootstrap'] as const,
  sources: ['sources'] as const,
  runs: ['runs'] as const,
  settings: ['settings'] as const,
  sales: ['sales'] as const,
  counts: (f: LeadFilter) => ['counts', f] as const,
  leadsAll: ['leads'] as const,
  leads: (q: QueueId, f: LeadFilter) => ['leads', q, f] as const,
  lead: (id: string) => ['lead', id] as const,
};

export function useBootstrap() {
  const port = usePort();
  return useQuery({ queryKey: qk.bootstrap, queryFn: () => port.bootstrap(), staleTime: 60_000 });
}

export function useSources() {
  const port = usePort();
  return useQuery({ queryKey: qk.sources, queryFn: () => port.listSources(), staleTime: 15_000 });
}

export function useRuns() {
  const port = usePort();
  return useQuery({ queryKey: qk.runs, queryFn: () => port.listRuns({ limit: 20 }), staleTime: 10_000 });
}

export function useSettings() {
  const port = usePort();
  return useQuery({ queryKey: qk.settings, queryFn: () => port.getSettings() });
}

export function useSales(enabled: boolean) {
  const port = usePort();
  return useQuery({ queryKey: qk.sales, queryFn: () => port.getSalesSummary(), enabled });
}

export function useCounts(filter: LeadFilter) {
  const port = usePort();
  return useQuery({ queryKey: qk.counts(filter), queryFn: () => port.countQueues(filter), staleTime: 10_000, placeholderData: (prev) => prev });
}

export function useLeadList(queue: QueueId, filter: LeadFilter, enabled = true) {
  const port = usePort();
  return useInfiniteQuery({
    queryKey: qk.leads(queue, filter),
    enabled,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => port.listLeads({ queue, filter, cursor: pageParam, limit: PAGE_SIZE }),
    getNextPageParam: (last) => (last.hasMore ? last.nextCursor : undefined),
    staleTime: 30_000,
  });
}

export function findSummary(qc: QueryClient, id: string): LeadSummary | undefined {
  for (const [, data] of qc.getQueriesData<InfiniteData<Page<LeadSummary>>>({ queryKey: qk.leadsAll })) {
    for (const page of data?.pages ?? []) {
      const hit = page.items.find((i) => i.id === id);
      if (hit) return hit;
    }
  }
  return undefined;
}

export function useLead(id: string | null) {
  const port = usePort();
  const qc = useQueryClient();
  return useQuery({
    queryKey: qk.lead(id ?? '-'),
    enabled: !!id,
    queryFn: () => port.getLead(id as string),
    // 목록 행으로 머리 부분을 먼저 보여준다 (isPlaceholderData 로 구분)
    placeholderData: () => (id ? (findSummary(qc, id) as LeadDetail | undefined) : undefined),
  });
}

/** 상세 결과를 캐시에 넣고, 목록의 같은 행만 교체한다 (재정렬 없음) */
export function applyDetail(qc: QueryClient, d: LeadDetail): void {
  qc.setQueryData(qk.lead(d.id), d);
  const summary = toSummary(d);
  qc.setQueriesData<InfiniteData<Page<LeadSummary>>>({ queryKey: qk.leadsAll }, (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((p) => ({ ...p, items: p.items.map((i) => (i.id === d.id ? summary : i)) })),
        }
      : data,
  );
}

export function patchSummary(qc: QueryClient, id: string, patch: Partial<LeadSummary>): void {
  qc.setQueriesData<InfiniteData<Page<LeadSummary>>>({ queryKey: qk.leadsAll }, (data) =>
    data
      ? { ...data, pages: data.pages.map((p) => ({ ...p, items: p.items.map((i) => (i.id === id ? { ...i, ...patch } : i)) })) }
      : data,
  );
  const cur = qc.getQueryData<LeadDetail>(qk.lead(id));
  if (cur) qc.setQueryData(qk.lead(id), { ...cur, ...patch });
}

type Port = WorkleadPort;

export function useLeadMutation<A>(fn: (port: Port, id: string, arg: A) => Promise<LeadDetail>) {
  const port = usePort();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, arg }: { id: string; arg: A }) => fn(port, id, arg),
    onSuccess: (d) => {
      applyDetail(qc, d);
      void qc.invalidateQueries({ queryKey: ['counts'] });
      void qc.invalidateQueries({ queryKey: qk.sales });
    },
  });
}
