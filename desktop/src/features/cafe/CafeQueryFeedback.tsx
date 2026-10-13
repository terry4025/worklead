import type { UseQueryResult } from '@tanstack/react-query';
import { Button } from '../../ui/Button';

type QueryState = Pick<UseQueryResult<unknown>, 'isPending' | 'isFetching' | 'error' | 'refetch'>;

export function CafeQueryFeedback({ label, query }: { label: string; query: QueryState }) {
  if (query.error) return <div className="cafe-query-error" role="alert"><div><strong>{label} 조회 실패</strong><p>{query.error.message}</p></div><Button size="sm" busy={query.isFetching} onClick={() => void query.refetch()}>{label} 다시 불러오기</Button></div>;
  if (query.isPending) return <p className="cafe-query-loading" role="status">{label} 불러오는 중…</p>;
  return null;
}
