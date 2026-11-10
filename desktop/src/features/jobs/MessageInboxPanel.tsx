import { useQuery } from '@tanstack/react-query';
import { MessageSquare } from 'lucide-react';
import { usePortCtx } from '../../app/PortContext';
import { formatDateTime } from '../../domain/format';
import { Button } from '../../ui/Button';
import { StatusMark } from '../../ui/StatusMark';

const labels = {
  off: '채팅 내용 연결 안 됨', waiting: '크롬 채팅 화면 대기', visible: '보이는 채팅 내용',
  panel_closed: '크롬에서 채팅 목록을 열어 주세요', surface_unrecognized: '채팅 화면을 확인하지 못함',
  login_required: '직접 로그인 후 다시 연결 필요', stale: '관찰 지연 · 이전 내용 숨김', account_changed: '계정 변경 · 다시 연결 필요',
};

export function MessageInboxPanel({ now, onOpenLead }: { now: Date; onOpenLead: (id: string) => void }) {
  const { port } = usePortCtx();
  const q = useQuery({ queryKey: ['jobs', 'messages', 'inbox'], queryFn: () => port.jobs.messagesInbox(), refetchInterval: 2000, gcTime: 0 });
  const view = q.data;
  return <section className="message-inbox" aria-labelledby="message-inbox-heading">
    <header className="auto-apply-head"><div><h2 id="message-inbox-heading"><MessageSquare size={18} aria-hidden="true" /> 당근 답장·채팅 보기</h2><p className="field-hint">연결한 크롬 탭의 채팅 목록과 선택한 대화에서 보이는 내용을 자동 반영합니다.</p></div><StatusMark tone={q.isError || view?.state === 'stale' ? 'warn' : view?.state === 'visible' ? 'good' : 'muted'}>{q.isError ? '연결 상태 확인 실패 · 내용 숨김' : view ? labels[view.state] : '채팅 연결 확인 중'}</StatusMark></header>
    {!q.isError && view?.state === 'visible' && view.text ? <>
      <p className="field-hint">{view.observed_at ? `${formatDateTime(view.observed_at, now)} 관찰` : ''} · {view.lead_title ? `연결 공고: ${view.lead_title}` : '연결 공고 미확인 · 특정 지원에 대한 답장으로 단정하지 않습니다.'}</p>
      <div className="message-inbox-text" aria-label="당근에 보이는 채팅 내용" aria-live="polite">{view.text}</div>
      {view.draft_state === 'pending' ? <p className="field-hint">답장 초안을 만들고 있습니다.</p> : null}
      {view.draft_state === 'failed' ? <p className="field-error">답장 초안을 만들지 못했습니다.</p> : null}
      {view.draft_state === 'ready' && view.draft_text ? <section className="message-draft" aria-label="AI 답장 초안">
        <p className="field-hint">AI 답장 초안입니다. 보내기 전에 직접 확인하세요. 자동으로 보내지 않습니다.</p>
        <div className="message-inbox-text">{view.draft_text}</div>
        <Button variant="ghost" onClick={() => void navigator.clipboard.writeText(view.draft_text ?? '')}>초안 복사</Button>
      </section> : null}
      {view.lead_id ? <Button size="sm" onClick={() => onOpenLead(view.lead_id!)}>연결 공고·지원 기록 보기</Button> : null}
    </> : <p className="field-hint">‘현재 크롬으로 연결’과 확장 팝업에서 ‘보이는 채팅 내용’ 항목을 켜세요. 채팅 목록을 열고, 상세 답장은 크롬에서 대화를 선택하면 이곳에도 표시됩니다.</p>}
    <p className="field-hint">미읽음 수가 늘면 기존 앱·Windows 알림으로 알립니다. 보이는 텍스트만 일시 표시하며 숨겨진 대화·이미지·이전 기록은 가져오지 않습니다. Worklead에서 메시지를 전송하지 않습니다.</p>
  </section>;
}
