import { Copy, ExternalLink, Plus } from 'lucide-react';
import { forwardRef, useEffect, useState } from 'react';
import { formatDate, formatDateTime, formatKrw, todayKst } from '../../domain/format';
import { outcomeKindLabel, salesStageLabel, selectableStages } from '../../domain/labels';
import type { LeadDetail, OutcomeKind, SalesStage } from '../../domain/model';
import { useLeadMutation } from '../../app/queries';
import { copyText } from '../../platform/external';
import { Button } from '../../ui/Button';
import { errorMessage } from '../../ui/States';
import { useToast } from '../../ui/Toasts';

export function StageSelect({ lead, id }: { lead: LeadDetail; id?: string }) {
  const m = useLeadMutation((port, leadId, stage: SalesStage) => port.updateLead(leadId, { salesStage: stage }));
  const toast = useToast();
  return (
    <label className="select stage-select">
      <span className="sr-only">진행 상태</span>
      <select
        id={id}
        value={lead.salesStage}
        disabled={m.isPending}
        onChange={(e) =>
          m.mutate(
            { id: lead.id, arg: e.target.value as SalesStage },
            { onError: (err) => toast.show({ tone: 'bad', text: `진행 상태를 바꾸지 못했습니다: ${errorMessage(err)}` }) },
          )
        }
        aria-label="진행 상태"
      >
        {selectableStages.map((s) => (
          <option key={s} value={s}>
            {salesStageLabel[s]}
          </option>
        ))}
        {!selectableStages.includes(lead.salesStage) ? <option value={lead.salesStage}>{salesStageLabel[lead.salesStage]}</option> : null}
      </select>
    </label>
  );
}

export const MemoBox = forwardRef<HTMLTextAreaElement, { lead: LeadDetail }>(function MemoBox({ lead }, ref) {
  const [text, setText] = useState(lead.memo);
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const m = useLeadMutation((port, id, memo: string) => port.updateLead(id, { memo }));
  useEffect(() => {
    setText(lead.memo);
    setState('idle');
  }, [lead.id, lead.memo]);
  const save = () => {
    if (text === lead.memo) return;
    setState('saving');
    m.mutate({ id: lead.id, arg: text }, { onSuccess: () => setState('saved'), onError: () => setState('error') });
  };
  return (
    <div className="memo">
      <label htmlFor={`memo-${lead.id}`} className="field-label">
        메모 <span className="field-hint">(M · 입력칸을 벗어나면 저장, Ctrl+Enter)</span>
      </label>
      <textarea
        id={`memo-${lead.id}`}
        ref={ref}
        value={text}
        rows={3}
        placeholder="판단 근거, 확인할 것, 연락 기록 등"
        onChange={(e) => {
          setText(e.target.value);
          setState('idle');
        }}
        onBlur={save}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save();
          if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur();
        }}
      />
      <span className={`save-state save-${state}`} aria-live="polite">
        {state === 'saving' ? '저장 중…' : state === 'saved' ? '저장됨' : state === 'error' ? '저장 실패 — 다시 시도하세요' : ''}
      </span>
    </div>
  );
});

export function FeedbackControls({ lead }: { lead: LeadDetail }) {
  const m = useLeadMutation((port, id, fb: { kind: 'remote' | 'real_request'; value: 'confirmed' | 'denied' | 'yes' | 'no' | 'clear' }) => port.sendFeedback(id, fb));
  const toast = useToast();
  const send = (kind: 'remote' | 'real_request', value: 'confirmed' | 'denied' | 'yes' | 'no' | 'clear') =>
    m.mutate({ id: lead.id, arg: { kind, value } }, { onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }) });
  return (
    <div className="feedback">
      <div className="feedback-row">
        <span className="field-label">재택 가능 여부 (내가 확인)</span>
        <div className="seg" role="group" aria-label="재택 가능 여부">
          <button type="button" aria-pressed={lead.feedback.remote === 'confirmed'} onClick={() => send('remote', lead.feedback.remote === 'confirmed' ? 'clear' : 'confirmed')}>
            가능
          </button>
          <button type="button" aria-pressed={lead.feedback.remote === 'denied'} onClick={() => send('remote', lead.feedback.remote === 'denied' ? 'clear' : 'denied')}>
            불가
          </button>
        </div>
      </div>
      <div className="feedback-row">
        <span className="field-label">실제 제작 의뢰인가요</span>
        <div className="seg" role="group" aria-label="실제 의뢰 여부">
          <button type="button" aria-pressed={lead.feedback.realRequest === 'yes'} onClick={() => send('real_request', lead.feedback.realRequest === 'yes' ? 'clear' : 'yes')}>
            예
          </button>
          <button type="button" aria-pressed={lead.feedback.realRequest === 'no'} onClick={() => send('real_request', lead.feedback.realRequest === 'no' ? 'clear' : 'no')}>
            아니오
          </button>
        </div>
      </div>
      <p className="field-hint">내 확인은 자동 판정과 따로 저장되고, 재분석해도 지워지지 않습니다.</p>
    </div>
  );
}

/** 30초 연락: 짧은 메시지를 복사하고 원문(지원·채팅)을 여는 흐름. 발송은 사용자가 원래 채널에서 직접 한다. */
export function QuickContact({ lead, onOpenOriginal }: { lead: LeadDetail; onOpenOriginal: () => void }) {
  const [text, setText] = useState(lead.quickMessage);
  const toast = useToast();
  useEffect(() => setText(lead.quickMessage), [lead.id, lead.quickMessage]);
  const copyAndOpen = async () => {
    const ok = await copyText(text);
    toast.show(ok ? { tone: 'good', text: '메시지를 복사했습니다. 열린 원문에서 지원·채팅 창에 붙여 넣으세요.' } : { tone: 'bad', text: '복사하지 못했습니다' });
    if (ok) onOpenOriginal();
  };
  const missing = text.includes('설정 > 프로필');
  return (
    <div className="quick">
      <textarea aria-label="빠른 연락 메시지" value={text} rows={5} onChange={(e) => setText(e.target.value)} />
      <div className="draft-actions">
        <Button size="sm" variant="primary" icon={<ExternalLink size={14} />} onClick={() => void copyAndOpen()} disabled={!lead.originalUrl}>
          복사하고 원문 열기
        </Button>
        <Button size="sm" icon={<Copy size={14} />} onClick={async () => toast.show((await copyText(text)) ? { tone: 'good', text: '복사했습니다' } : { tone: 'bad', text: '복사하지 못했습니다' })}>
          복사만
        </Button>
        <span className="field-hint">자동 발송하지 않습니다{missing ? ' · 소개·링크는 설정(,)에서 한 번 입력하면 채워집니다' : ''}</span>
      </div>
    </div>
  );
}

export function DraftBox({ lead, mode }: { lead: LeadDetail; mode: 'demo' | 'live' }) {
  const [text, setText] = useState(lead.draft?.text ?? '');
  const toast = useToast();
  const gen = useLeadMutation((port, id) => port.generateDraft(id));
  const save = useLeadMutation((port, id, draftText: string) => port.updateLead(id, { draftText }));
  useEffect(() => setText(lead.draft?.text ?? ''), [lead.id, lead.draft?.text]);

  if (!lead.draft) {
    return (
      <div className="draft-empty">
        <p className="muted-text">확인할 질문을 넣은 문의 초안을 만들 수 있습니다. 경력·가격은 지어내지 않고 직접 채울 자리로 남깁니다.</p>
        <Button
          size="sm"
          busy={gen.isPending}
          onClick={() => gen.mutate({ id: lead.id, arg: undefined as never }, { onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }) })}
        >
          초안 만들기
        </Button>
      </div>
    );
  }
  return (
    <div className="draft">
      <textarea
        aria-label="문의 초안"
        value={text}
        rows={9}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          if (text !== lead.draft?.text) save.mutate({ id: lead.id, arg: text });
        }}
      />
      <div className="draft-actions">
        <Button
          size="sm"
          icon={<Copy size={14} />}
          onClick={async () => toast.show((await copyText(text)) ? { tone: 'good', text: '초안을 복사했습니다. 원래 채널에서 직접 보내세요.' } : { tone: 'bad', text: '복사하지 못했습니다' })}
        >
          복사
        </Button>
        <span className="field-hint">
          자동 발송하지 않습니다 · {lead.draft.editedByUser ? '직접 수정함' : lead.draft.generatedAt ? `${formatDateTime(lead.draft.generatedAt)} 생성` : ''}
          {mode === 'demo' ? ' · 데모' : ''}
        </span>
      </div>
    </div>
  );
}

export function Outcomes({ lead }: { lead: LeadDetail }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<OutcomeKind>('payment_received');
  const [amount, setAmount] = useState('');
  const [date, setDate] = useState(todayKst());
  const [note, setNote] = useState('');
  const [evidenceRef, setEvidenceRef] = useState('');
  const toast = useToast();
  const m = useLeadMutation(
    (port, id, input: { kind: OutcomeKind; amount: number | null; occurredOn: string; note: string | null; evidenceRef: string | null }) =>
      port.addOutcome(id, input),
  );
  const sum = (k: OutcomeKind) => lead.outcomes.filter((o) => o.kind === k).reduce((a, o) => a + (o.amount ?? 0), 0);
  const contract = lead.outcomes.filter((o) => o.kind === 'contract_confirmed');
  const parsedAmount = amount.trim() === '' ? null : Number(amount.replace(/[,\s원]/g, ''));
  const invalid = parsedAmount !== null && (!Number.isFinite(parsedAmount) || parsedAmount < 0);

  return (
    <div className="outcomes">
      {lead.outcomes.length ? (
        <>
          <p className="outcome-sum">
            계약 확인 <strong className="num">{contract.length ? formatKrw(sum('contract_confirmed')) : '—'}</strong> · 실제 수금{' '}
            <strong className="num">{formatKrw(sum('payment_received'))}</strong>
            {sum('refund') ? <> · 환불 <span className="num">{formatKrw(sum('refund'))}</span></> : null}
            {sum('direct_cost') ? <> · 직접 비용 <span className="num">{formatKrw(sum('direct_cost'))}</span></> : null}
          </p>
          <table className="outcome-table">
            <tbody>
              {lead.outcomes.map((o) => (
                <tr key={o.id}>
                  <td className="num">{formatDate(o.occurredOn + 'T00:00:00+09:00')}</td>
                  <td>{outcomeKindLabel[o.kind]}</td>
                  <td className="num">{o.amount === null ? '금액 미기록' : formatKrw(o.amount)}</td>
                  <td className="muted-text">{[o.note, o.evidenceRef ? `증빙: ${o.evidenceRef}` : null].filter(Boolean).join(' · ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="muted-text">기록 없음. ‘수주’로 바꿔도 수금액은 늘지 않습니다 — 실제 입금만 기록하세요.</p>
      )}
      {open ? (
        <form
          className="outcome-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (invalid) return;
            m.mutate(
              { id: lead.id, arg: { kind, amount: parsedAmount, occurredOn: date, note: note.trim() || null, evidenceRef: evidenceRef.trim() || null } },
              {
                onSuccess: () => {
                  setAmount('');
                  setNote('');
                  setEvidenceRef('');
                  setOpen(false);
                  toast.show({ tone: 'good', text: '기록했습니다' });
                },
                onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }),
              },
            );
          }}
        >
          <label className="select">
            <span className="sr-only">종류</span>
            <select value={kind} onChange={(e) => setKind(e.target.value as OutcomeKind)} aria-label="종류">
              {(Object.keys(outcomeKindLabel) as OutcomeKind[]).map((k) => (
                <option key={k} value={k}>
                  {outcomeKindLabel[k]}
                </option>
              ))}
            </select>
          </label>
          <input aria-label="금액(원)" inputMode="numeric" placeholder="금액 (원, 모르면 비움)" value={amount} onChange={(e) => setAmount(e.target.value)} aria-invalid={invalid} />
          <input aria-label="날짜" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
          <input aria-label="메모" placeholder="메모" value={note} onChange={(e) => setNote(e.target.value)} />
          <input aria-label="증빙 참조" placeholder="증빙 참조 (예: 입금 내역)" value={evidenceRef} onChange={(e) => setEvidenceRef(e.target.value)} />
          <div className="form-actions">
            <Button type="submit" size="sm" variant="primary" busy={m.isPending} disabled={invalid}>
              기록 추가
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              취소
            </Button>
          </div>
          {invalid ? <p className="field-error">금액은 0 이상의 숫자로 입력하세요</p> : null}
        </form>
      ) : (
        <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setOpen(true)}>
          계약·수금 기록
        </Button>
      )}
    </div>
  );
}
