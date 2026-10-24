/**
 * 데이터 조회·변경 훅. 수집 결과는 useLiveEvents에서 자동 반영한다.
 * 사용자 편집 응답은 기존 목록의 같은 행을 우선 갱신한다.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData, type QueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import type { LeadDetail, LeadFilter, LeadSummary, Page, QueueId, RelationInput, RunKind } from '../domain/model';
import { toSummary } from '../domain/summary';
import type { WorkleadPort } from '../data/port';
import { PortError, toPortError } from '../data/port';
import { usePort } from './PortContext';
import { getDatabaseEpoch } from './databaseCache';

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
  return useQuery({ queryKey: qk.bootstrap, queryFn: () => port.bootstrap(), staleTime: 60_000, refetchInterval: 60_000 });
}

export function useSources() {
  const port = usePort();
  return useQuery({ queryKey: qk.sources, queryFn: () => port.listSources(), staleTime: 15_000, refetchInterval: 30_000 });
}

export function useRuns(kinds?: RunKind[]) {
  const port = usePort();
  return useQuery({ queryKey: [...qk.runs, 'history', kinds ?? []], queryFn: () => port.listRuns({ limit: 20, kinds }), staleTime: 10_000, refetchInterval: 10_000 });
}

// 개별 재확인·재분석 이력에 밀려 진행 중 작업과 마지막 탐색이 사라지지 않게 한다.
export async function loadRunStatus(port: WorkleadPort) {
  const [active, discoveries] = await Promise.all([
    port.listRuns({ states: ['queued', 'running', 'paused'], limit: 100 }),
    port.listRuns({ kinds: ['discovery'], states: ['succeeded', 'partial', 'failed', 'cancelled'], limit: 10 }),
  ]);
  return [...active, ...discoveries];
}

export function useRunStatus() {
  const port = usePort();
  return useQuery({ queryKey: [...qk.runs, 'status'], queryFn: () => loadRunStatus(port), staleTime: 5_000, refetchInterval: 10_000 });
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
    retry: (count, error) => count < 2 && toPortError(error).retryable,
    refetchInterval: (query) => query.state.status === 'error' && query.state.data ? 30_000 : false,
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
  const current = qc.getQueryData<LeadDetail>(qk.lead(d.id));
  if (current?.related && d.related) {
    d = { ...d, related: d.related.map((row) => {
      const prior = current.related.find((r) => r.id === row.id);
      return prior && prior.databaseGeneration === row.databaseGeneration && prior.revision > row.revision ? prior : row;
    }) };
  }
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
    onMutate: () => getDatabaseEpoch(qc),
    mutationFn: ({ id, arg }: { id: string; arg: A }) => fn(port, id, arg),
    onSuccess: async (d, _variables, epoch) => {
      if (epoch !== getDatabaseEpoch(qc)) return;
      // 저장 전에 읽은 자동 재조회 응답이 새 사용자 기록을 되돌리지 않게 한다.
      await qc.cancelQueries({ queryKey: qk.lead(d.id), exact: true });
      if (epoch !== getDatabaseEpoch(qc)) return;
      applyDetail(qc, d);
      void qc.invalidateQueries({ queryKey: ['counts'] });
      void qc.invalidateQueries({ queryKey: qk.sales });
    },
  });
}

/** 관계 응답의 오래된 메모·영업 상태를 캐시에 적용하지 않는다. */
function patchRelation(qc: QueryClient, detail: LeadDetail, relationId: string): void {
  const next = detail.related.find((r) => r.id === relationId);
  if (!next) return;
  qc.setQueryData<LeadDetail>(qk.lead(detail.id), (current) => {
    if (!current?.related) return current;
    return { ...current, related: current.related.map((row) => {
      if (row.id !== relationId || (row.databaseGeneration === next.databaseGeneration && row.revision > next.revision)) return row;
      return { ...row, confirmed: next.confirmed, dismissed: next.dismissed, revision: next.revision, databaseGeneration: next.databaseGeneration };
    }) };
  });
}

function refreshRelationDetails(qc: QueryClient, details: LeadDetail[], relationId: string): void {
  for (const detail of details) {
    patchRelation(qc, detail, relationId);
    // 상세·활동은 새 GET으로 읽으며, 열지 않은 다른 리드 캐시를 생성하지 않는다.
    void qc.invalidateQueries({ queryKey: qk.lead(detail.id), exact: true });
  }
}

export function useRelationMutation() {
  const port = usePort();
  const qc = useQueryClient();
  const epochs = useRef(new WeakMap<object, number>());
  return useMutation({
    retry: false,
    onMutate: async (variables: { id: string; relatedId: string; relationId: string; input: RelationInput }) => {
      const epoch = getDatabaseEpoch(qc);
      epochs.current.set(variables, epoch);
      await Promise.all([variables.id, variables.relatedId].map((id) => qc.cancelQueries({ queryKey: qk.lead(id), exact: true })));
      return epoch;
    },
    mutationFn: (variables) => {
      if (epochs.current.get(variables) !== getDatabaseEpoch(qc)) {
        throw new PortError({ kind: 'api', code: 'relation_generation_conflict', message: '데이터가 복원되었습니다. 현재 관계를 다시 불러오세요' });
      }
      return port.updateRelation(variables.id, variables.relationId, variables.input);
    },
    onSuccess: (result, variables, epoch) => {
      if (epoch !== getDatabaseEpoch(qc)) return;
      refreshRelationDetails(qc, [result.lead, result.relatedLead], variables.relationId);
    },
  });
}

/** 결과 불명·충돌 뒤에는 변경 요청을 반복하지 않고 현재 양쪽 관계만 읽는다. */
export async function reloadRelation(qc: QueryClient, port: Port, id: string, relatedId: string, relationId: string): Promise<void> {
  const epoch = getDatabaseEpoch(qc);
  const details = await Promise.all([port.getLead(id), port.getLead(relatedId)]);
  if (epoch !== getDatabaseEpoch(qc)) {
    throw new PortError({ kind: 'api', code: 'relation_generation_conflict', message: '데이터가 복원되었습니다. 현재 관계를 다시 불러오세요' });
  }
  refreshRelationDetails(qc, details, relationId);
}
