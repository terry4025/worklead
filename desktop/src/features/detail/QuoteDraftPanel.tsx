import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { usePortCtx } from '../../app/PortContext';
import { Button } from '../../ui/Button';

export function QuoteDraftPanel({ leadId }: { leadId: string }) {
  const { port } = usePortCtx();
  const qc = useQueryClient();
  const key = ['quote-draft', leadId];
  const q = useQuery({ queryKey: key, queryFn: () => port.quoteDraft(leadId),
    refetchInterval: (query) => (query.state.data?.state === 'pending' ? 3000 : false) });
  const make = useMutation({ mutationFn: () => port.requestQuoteDraft(leadId), onSuccess: (d) => qc.setQueryData(key, d) });
  const d = q.data;
  const canRequest = !d || d.state === 'none' || d.state === 'failed';
  return <section className="d-section" aria-label="견적서 초안">
    <h3 className="d-h">견적서 초안</h3>
    {canRequest ? <Button size="sm" variant="ghost" disabled={make.isPending} onClick={() => make.mutate()}>견적서 초안 만들기</Button> : null}
    {make.isError ? <p className="field-error">설정에서 EXAONE 견적서 초안 기능을 켜세요.</p> : null}
    {d?.state === 'pending' ? <p className="field-hint">견적서 초안을 만들고 있습니다.</p> : null}
    {d?.state === 'failed' ? <p className="field-error">견적서 초안을 만들지 못했습니다.</p> : null}
    {d?.state === 'ready' && d.text ? <>
      <p className="field-hint">금액·기간 칸은 직접 채우세요. 보내기 전에 확인하세요.</p>
      <pre className="quote-draft-text">{d.text}</pre>
      <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard.writeText(d.text ?? '')}>복사</Button>
    </> : null}
  </section>;
}
