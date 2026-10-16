import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, Link, Square } from 'lucide-react';
import { usePortCtx } from '../../app/PortContext';
import { formatDateTime } from '../../domain/format';
import type { MessageWatchStatus } from '../../domain/jobs';
import { Button } from '../../ui/Button';
import { errorMessage } from '../../ui/States';
import { StatusMark } from '../../ui/StatusMark';

const key = ['jobs', 'messages', 'status'] as const;
const labels: Record<MessageWatchStatus['state'], string> = {
  stopped: '연결하지 않음', starting: '연결 중', login_required: '직접 로그인 필요',
  watching: '미읽음 표시 감시 중', surface_unrecognized: '메시지 표시 확인 필요', error: '연결 오류',
};

export function MessageConnectionPanel({ onSettings, now }: { onSettings: () => void; now: Date }) {
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: key, queryFn: () => port.jobs.messagesStatus(), refetchInterval: 5000 });
  const action = useMutation({
    mutationFn: (start: boolean) => start ? port.jobs.messagesStart() : port.jobs.messagesStop(),
    onSuccess: (status) => { qc.setQueryData(key, status); },
  });
  const status = q.data;
  const watching = status?.state === 'watching' && !q.isError;
  return <section className="message-connection" aria-labelledby="message-heading">
    <h2 id="message-heading"><Bell size={17} aria-hidden="true" /> 당근 새 메시지</h2>
    <StatusMark tone={q.isError || action.isError ? 'bad' : watching ? 'good' : 'warn'}>{q.isError ? '연결 상태 확인 실패' : status ? labels[status.state] : '연결 상태 확인 중'}</StatusMark>
    <p>{status?.message ?? '메시지 연결 여부를 아직 확인하지 못했습니다.'}</p>
    {status ? <dl className="message-status"><div><dt>미읽음 표시</dt><dd>{status.unread_count === null ? '미확인' : `${status.unread_count}개`}{!watching && status.last_observed_at ? ' · 마지막 관찰' : ''}</dd></div><div><dt>마지막 관찰</dt><dd>{status.last_observed_at ? formatDateTime(status.last_observed_at, now) : '관찰 기록 없음'}</dd></div><div><dt>최근 증가</dt><dd>{status.last_increase_at ? formatDateTime(status.last_increase_at, now) : '증가 관찰 기록 없음'}</dd></div></dl> : null}
    <div className="draft-actions">
      <Button size="sm" icon={<Link size={14} />} busy={action.isPending} disabled={!status || (status.enabled && !['error', 'stopped'].includes(status.state))} onClick={() => action.mutate(true)}>연결하고 감시 시작</Button>
      {status?.enabled ? <Button size="sm" icon={<Square size={13} />} disabled={action.isPending} onClick={() => action.mutate(false)}>감시 중지</Button> : null}
    </div>
    {q.isError || action.isError ? <div role="alert"><p className="field-error">{errorMessage(action.error ?? q.error)}</p><Button size="sm" disabled={action.isPending} onClick={() => void q.refetch()}>연결 상태 다시 확인</Button></div> : null}
    <p className="field-hint">{mode === 'demo' ? '데모에서는 실제 메시지를 읽지 않습니다. ' : ''}시작하면 전용 Edge 창이 열립니다. 그 창에서 직접 로그인하세요. 메시지 본문은 저장하지 않습니다.</p>
    <p className="field-hint">화면의 미읽음 수를 {status?.poll_interval_seconds ?? 2}초마다 확인하고 증가하면 알립니다. 처음 읽은 수는 기준값입니다. 수가 그대로인 새 메시지는 감지하지 못할 수 있으며, Worklead나 전용 Edge를 종료하면 감시가 중단됩니다.</p>
    {status?.browser_notifications === 'denied' ? <p className="field-error">Edge 알림이 차단되어 있습니다. 브라우저에서 직접 알림 권한을 확인하세요.</p> : null}
    <Button size="sm" variant="ghost" icon={<Bell size={14} />} onClick={onSettings}>Windows 알림·조용한 시간 설정</Button>
    <p className="field-hint">Windows 알림 사용 여부와 방해 금지 설정에 따라 알림이 보이지 않을 수 있습니다. 실제 새 메시지 수신은 연결 후 확인해야 합니다.</p>
  </section>;
}
