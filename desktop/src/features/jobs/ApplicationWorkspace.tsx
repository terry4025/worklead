import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, ExternalLink, FileText, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { usePortCtx } from '../../app/PortContext';
import { getDatabaseEpoch } from '../../app/databaseCache';
import { qk } from '../../app/queries';
import { formatDateTime } from '../../domain/format';
import { recommendationLabel } from '../../domain/labels';
import type { ApplicationEntry } from '../../domain/jobs';
import { copyText, openExternal } from '../../platform/external';
import { Button } from '../../ui/Button';
import { ErrorState, errorMessage } from '../../ui/States';
import { StatusMark } from '../../ui/StatusMark';
import { useToast } from '../../ui/Toasts';
import { MessageConnectionPanel } from './MessageConnectionPanel';
import { AutoApplyPanel } from './AutoApplyPanel';
import './jobs.css';

const applicationKey = ['jobs', 'applications'] as const;

export function HoldReasons({ entry }: { entry: ApplicationEntry }) {
  return <div className="application-assessment">
    <StatusMark tone={entry.hold_reasons.length || !entry.candidate ? 'warn' : 'muted'}>
      {!entry.candidate ? '준비 대상 아님' : entry.hold_reasons.length ? '조건 확인 후 검토' : '초안 준비 가능'}
    </StatusMark>
    {entry.hold_reasons.length ? <ul className="application-holds">{entry.hold_reasons.map((reason, i) => <li key={`${reason.code}-${i}`}>{reason.message}</li>)}</ul> : null}
    {entry.questions.length ? <details className="application-questions"><summary>원문에서 확인할 질문 {entry.questions.length}개</summary><ul>{entry.questions.map((question, i) => <li key={i}>{question}</li>)}</ul></details> : null}
  </div>;
}

function usePrepare() {
  const { port } = usePortCtx();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: async (id: string) => {
      const epoch = getDatabaseEpoch(qc);
      const entry = await port.jobs.prepare(id);
      return { entry, epoch };
    },
    onSuccess: ({ entry, epoch }) => {
      if (getDatabaseEpoch(qc) !== epoch) return;
      qc.setQueryData(['jobs', 'application', entry.lead_id], entry);
      void qc.invalidateQueries({ queryKey: applicationKey });
      void qc.invalidateQueries({ queryKey: qk.lead(entry.lead_id) });
      toast.show({ tone: 'info', text: '초안을 준비했습니다. 외부로 전송하지 않았습니다.' });
    },
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }),
  });
}

/** 상세의 기존 편집기를 그대로 두고 서버 평가·보류 근거만 연결한다. */
export function ApplicationAssessment({ leadId }: { leadId: string }) {
  const { port } = usePortCtx();
  const q = useQuery({ queryKey: ['jobs', 'application', leadId], queryFn: () => port.jobs.application(leadId), staleTime: 30_000 });
  const prepare = usePrepare();
  if (q.isError) return <div className="application-inline"><p className="field-error">지원 준비 상태를 확인하지 못했습니다. {errorMessage(q.error)}</p><Button size="sm" onClick={() => void q.refetch()}>지원 준비 상태 다시 확인</Button></div>;
  if (!q.data) return <p className="field-hint" role="status">지원 준비 조건 확인 중…</p>;
  return <div className="application-inline">
    <HoldReasons entry={q.data} />
    <p className="field-hint">이 초안은 자동 전송하지 않습니다. 점수 기준 자동 지원은 지원 준비 화면에서 별도로 시작·연결합니다.</p>
    {!q.data.draft ? <Button size="sm" icon={<FileText size={14} />} disabled={!q.data.can_prepare} busy={prepare.isPending} onClick={() => prepare.mutate(leadId)}>지원 초안 준비</Button> : <p className="field-hint">저장된 초안을 유지합니다. 아래에서 내용을 확인·편집하세요.</p>}
  </div>;
}

function ApplicationCard({ entry, now, onOpenLead }: { entry: ApplicationEntry; now: Date; onOpenLead: (id: string) => void }) {
  const { port } = usePortCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const prepare = usePrepare();
  const [expanded, setExpanded] = useState(false);
  const [text, setText] = useState(entry.draft?.text ?? '');
  const [serverText, setServerText] = useState(entry.draft?.text ?? null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const busy = useRef(false);
  const current = useRef(text);
  const previous = useRef(serverText);
  useEffect(() => {
    const next = entry.draft?.text ?? null;
    if (current.current === (previous.current ?? '')) {
      current.current = next ?? '';
      setText(next ?? '');
    }
    // 편집 중 외부에서 바뀐 초안은 비교 기준도 유지해 CAS 충돌로 처리한다.
    if (current.current === (next ?? '')) {
      previous.current = next;
      setServerText(next);
    }
  }, [entry.draft?.text]);
  const dirty = text !== (serverText ?? '');
  const save = async () => {
    if (busy.current || !dirty || !text.trim() || text.length > 20000) return;
    busy.current = true;
    setSaving(true);
    setSaveError(null);
    const epoch = getDatabaseEpoch(qc);
    const submitted = current.current;
    try {
      const updated = await port.jobs.saveDraft(entry.lead_id, submitted, previous.current);
      if (getDatabaseEpoch(qc) !== epoch) return;
      previous.current = updated.draft?.text ?? null;
      setServerText(previous.current);
      qc.setQueryData(['jobs', 'application', entry.lead_id], updated);
      void qc.invalidateQueries({ queryKey: applicationKey });
      void qc.invalidateQueries({ queryKey: qk.lead(entry.lead_id) });
      toast.show({ tone: 'good', text: '초안을 저장했습니다. 외부로 전송하지 않았습니다.' });
    } catch (error) {
      setSaveError(errorMessage(error));
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };
  return <article className="application-card" aria-labelledby={`application-${entry.lead_id}`}>
    <header className="application-card-head">
      <div><StatusMark tone={entry.recommendation === 'recommended' ? 'good' : 'warn'}>{recommendationLabel[entry.recommendation]}</StatusMark><h3 id={`application-${entry.lead_id}`}>{entry.title}</h3><p className="field-hint">게시 {entry.published_at ? formatDateTime(entry.published_at, now) : '시각 미확인'} · 모집 확인 {entry.last_checked_at ? formatDateTime(entry.last_checked_at, now) : '미확인'}</p></div>
      <Button size="sm" onClick={() => onOpenLead(entry.lead_id)}>상세·영업 기록</Button>
    </header>
    <HoldReasons entry={entry} />
    <div className="draft-actions">
      <Button size="sm" variant="primary" icon={<FileText size={14} />} disabled={!entry.can_prepare} busy={prepare.isPending} onClick={() => { setExpanded(true); if (!entry.draft) prepare.mutate(entry.lead_id); }}>{entry.draft ? '저장된 초안 열기' : '지원 초안 준비'}</Button>
      {entry.draft ? <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '초안 접기' : '초안 펼치기'}{dirty ? ' · 저장 필요' : ''}</Button> : null}
      <Button size="sm" icon={<ExternalLink size={14} />} disabled={!entry.original_url} onClick={async () => { if (!entry.original_url) return; const result = await openExternal(entry.original_url); if (result !== 'opened') toast.show({ tone: result === 'copied' ? 'info' : 'bad', text: result === 'copied' ? '원문 주소를 복사했습니다.' : '원문 주소를 열지 못했습니다.' }); }}>원문 열기</Button>
    </div>
    {entry.draft ? <div hidden={!expanded} className="application-editor">
      <label className="field-label" htmlFor={`application-draft-${entry.lead_id}`}>지원 초안 · {entry.draft.edited_by_user ? '직접 수정함' : '저장된 정보로 작성'}</label>
      <textarea id={`application-draft-${entry.lead_id}`} rows={8} maxLength={20000} value={text} onChange={(e) => { current.current = e.target.value; setText(e.target.value); setSaveError(null); }} />
      <div className="draft-actions"><Button size="sm" busy={saving} disabled={!dirty || !text.trim()} onClick={() => void save()}>초안 저장</Button><Button size="sm" icon={<Copy size={14} />} onClick={async () => toast.show(await copyText(text) ? { tone: 'info', text: '현재 초안을 복사했습니다. 외부로 전송하지 않았습니다.' } : { tone: 'bad', text: '복사하지 못했습니다.' })}>초안 복사</Button><span className="field-hint">{dirty ? '변경 내용 저장 필요' : '저장된 초안'} · {text.length.toLocaleString()}/20,000자</span></div>
      {saveError ? <p className="field-error" role="alert">{saveError} 입력한 글은 이 화면에 유지됩니다. 상세에서 최신 초안을 확인한 뒤 다시 편집하세요.</p> : null}
    </div> : null}
  </article>;
}

export function ApplicationWorkspace({ onOpenLead, onSettings, now }: { onOpenLead: (id: string) => void; onSettings: () => void; now: Date }) {
  const { port, mode } = usePortCtx();
  const q = useQuery({ queryKey: applicationKey, queryFn: () => port.jobs.applications(50), staleTime: 15_000, refetchInterval: 30_000 });
  return <main className="jobs-workspace" id="jobs-workspace" aria-label="지원 준비와 메시지">
    <div className="jobs-workspace-head"><div><h1>지원 준비</h1><p className="muted-text">확인 필요 의뢰도 함께 살펴보고, 저장된 정보로 초안을 준비하세요.</p></div><Button size="sm" icon={<RefreshCw size={14} />} busy={q.isFetching} onClick={() => void q.refetch()}>후보 새로고침</Button></div>
    <div className="jobs-mode-note" role="note"><strong>저장된 초안과 자동 지원을 따로 관리합니다</strong><p>아래 후보의 초안 준비·저장은 외부로 전송하지 않습니다. 자동 지원은 별도 연결 후 당근에 저장된 기본 자기소개를 사용합니다. 저장된 초안은 덮어쓰지 않습니다.{mode === 'demo' ? ' 현재 데모 데이터입니다.' : ''}</p></div>
    <AutoApplyPanel now={now} onOpenLead={onOpenLead} />
    <div className="jobs-columns"><section className="application-list" aria-labelledby="application-list-heading"><div className="application-list-heading"><h2 id="application-list-heading">지원 준비 후보</h2>{q.data ? <span className="muted-text">총 {q.data.total.toLocaleString()}건 · {q.data.items.length.toLocaleString()}건 표시</span> : null}</div>
      {q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
      {q.isPending ? <p role="status">지원 준비 후보를 불러오는 중…</p> : null}
      {q.data?.items.length === 0 ? <p className="jobs-empty">지원 준비 대상이 없습니다. 새 공고 수집 결과와 리드의 확인 필요 목록을 살펴보세요.</p> : null}
      {q.data?.items.map((entry) => <ApplicationCard key={entry.lead_id} entry={entry} now={now} onOpenLead={onOpenLead} />)}
    </section><aside className="jobs-connection"><MessageConnectionPanel onSettings={onSettings} now={now} /></aside></div>
  </main>;
}
