import { useEffect, useMemo, useRef } from 'react';
import type { LeadFilter, LeadSummary, QueueId } from '../../domain/model';
import type { ViewDef } from '../../domain/queues';
import { useLeadList } from '../../app/queries';
import { Button } from '../../ui/Button';
import { ErrorState, RowSkeleton } from '../../ui/States';
import { LeadRow } from './LeadRow';
import { formatTime } from '../../domain/format';
import { scrollWithin } from '../../ui/scroll';

export interface ListSnapshot {
  ids: string[];
  byId: Map<string, { lead: LeadSummary; queue: QueueId }>;
  loading: boolean;
  error: unknown;
  hasMore: Map<QueueId, () => void>;
}

function useSection(queue: QueueId | undefined, filter: LeadFilter) {
  return useLeadList(queue ?? 'all', filter, queue !== undefined);
}

export function LeadList({
  view,
  filter,
  counts,
  selectedId,
  onSelect,
  onOpen,
  onSnapshot,
  categoryLabel,
  empty,
  now,
}: {
  view: ViewDef;
  filter: LeadFilter;
  counts: Record<QueueId, number> | null | undefined;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
  onSnapshot: (s: ListSnapshot) => void;
  categoryLabel: (id: string) => string;
  empty: React.ReactNode;
  now: Date;
}) {
  const s0 = view.sections[0];
  const s1 = view.sections[1];
  const q0 = useSection(s0?.queue, filter);
  const q1 = useSection(s1?.queue, filter);
  const sections = [s0 ? { def: s0, q: q0 } : null, s1 ? { def: s1, q: q1 } : null].filter(
    (x): x is NonNullable<typeof x> => x !== null,
  );

  // 쿼리 결과 객체는 렌더마다 새로 만들어지므로, 안정적인 data 참조와 상태 값으로만 다시 계산한다
  const snapshot = useMemo<ListSnapshot>(() => {
    const ids: string[] = [];
    const byId = new Map<string, { lead: LeadSummary; queue: QueueId }>();
    const hasMore = new Map<QueueId, () => void>();
    const parts = [
      s0 ? { queue: s0.queue, data: q0.data, more: q0.hasNextPage, next: q0.fetchNextPage } : null,
      s1 ? { queue: s1.queue, data: q1.data, more: q1.hasNextPage, next: q1.fetchNextPage } : null,
    ];
    for (const p of parts) {
      if (!p) continue;
      for (const page of p.data?.pages ?? []) {
        for (const lead of page.items) {
          if (byId.has(lead.id)) continue;
          ids.push(lead.id);
          byId.set(lead.id, { lead, queue: p.queue });
        }
      }
      if (p.more) hasMore.set(p.queue, () => void p.next());
    }
    const err0 = s0 && q0.isError && !q0.data ? q0.error : null;
    const err1 = s1 && q1.isError && !q1.data ? q1.error : null;
    return {
      ids,
      byId,
      loading: Boolean((s0 && q0.isPending) || (s1 && q1.isPending)),
      error: err0 ?? err1 ?? null,
      hasMore,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s0, s1, q0.data, q1.data, q0.isPending, q1.isPending, q0.hasNextPage, q1.hasNextPage, q0.isError, q1.isError, q0.error, q1.error]);

  useEffect(() => onSnapshot(snapshot), [snapshot, onSnapshot]);

  // 선택된 행이 보이도록 스크롤
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selectedId) return;
    const el = document.getElementById(`lead-row-${selectedId}`);
    const head = listRef.current?.querySelector<HTMLElement>('.list-head');
    const section = el?.closest('.list-section')?.querySelector<HTMLElement>('.section-head');
    scrollWithin(el, listRef.current, 'nearest', (head?.offsetHeight ?? 0) + (section?.offsetHeight ?? 0));
  }, [selectedId]);

  if (snapshot.loading && snapshot.ids.length === 0) return <RowSkeleton />;
  if (snapshot.error) return <ErrorState error={snapshot.error} onRetry={() => sections.forEach((s) => void s.q.refetch())} />;
  if (snapshot.ids.length === 0) return <>{empty}</>;

  const refetchFailed = sections.find((s) => s.q.isRefetchError);

  return (
    <div
      ref={listRef}
      className="list"
      role="listbox"
      aria-label={`${view.label} 리드 목록`}
      aria-activedescendant={selectedId ? `lead-row-${selectedId}` : undefined}
      tabIndex={0}
      id="lead-list"
    >
      {refetchFailed ? (
        <div className="inline-alert inline-alert-warn" role="alert">
          목록을 새로 불러오지 못했습니다 — {refetchFailed.q.dataUpdatedAt ? `${formatTime(new Date(refetchFailed.q.dataUpdatedAt).toISOString())}에 불러온 목록 표시 중` : '이전 목록 표시 중'}
          <Button size="sm" variant="ghost" onClick={() => void refetchFailed.q.refetch()}>
            다시 시도
          </Button>
        </div>
      ) : null}
      <div className="list-head" aria-hidden="true">
        <span className="c-score" title="우선순위 — 규칙 점수, 수주 확률 아님">점수</span>
        <span className="c-main">제목 · 판단 이유</span>
        <span className="c-intent">유형</span>
        <span className="c-cat">업무</span>
        <span className="c-remote">재택</span>
        <span className="c-pay">보수</span>
        <span className="c-recruit">모집</span>
      </div>
      {sections.map(({ def, q }) => {
        const items = (q.data?.pages ?? []).flatMap((p) => p.items);
        const count = counts?.[def.queue];
        return (
          <section key={def.queue} className="list-section" aria-label={def.title}>
            {view.sections.length > 1 ? (
              <div className="section-head" role="presentation">
                <span className="section-title">{def.title}</span>
                <span className="section-count num">{count ?? items.length}</span>
                <span className="section-hint">{def.hint}</span>
              </div>
            ) : null}
            {items.length === 0 && !q.isPending ? <div className="section-empty">{def.title} 없음</div> : null}
            {items.map((lead) => (
              <LeadRow
                key={lead.id}
                lead={lead}
                queue={def.queue}
                selected={lead.id === selectedId}
                categoryLabel={categoryLabel}
                now={now}
                onSelect={onSelect}
                onOpen={onOpen}
              />
            ))}
            {q.hasNextPage ? (
              <div className="load-more">
                <Button size="sm" variant="ghost" busy={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
                  {def.title} 더 불러오기{count !== undefined ? ` (${count - items.length}건 남음)` : ''}
                </Button>
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}
