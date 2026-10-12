import { Copy, Plus } from 'lucide-react';
import { forwardRef, useEffect, useRef, useState } from 'react';
import { formatDate, formatDateTime, formatMoneyMinor, formatMoneyTotal, todayKst } from '../../domain/format';
import { outcomeKindLabel, salesStageLabel, selectableStages } from '../../domain/labels';
import type { CurrencySales, LeadDetail, OutcomeKind, SalesStage } from '../../domain/model';
import { groupOutcomes } from '../../domain/sales';
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
  const [savedText, setSavedText] = useState(lead.memo);
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const currentText = useRef(text);
  const serverText = useRef(savedText);
  const saving = useRef(false);
  const m = useLeadMutation((port, id, memo: string) => port.updateLead(id, { memo }));
  useEffect(() => {
    if (currentText.current === serverText.current) {
      currentText.current = lead.memo;
      setText(lead.memo);
    }
    serverText.current = lead.memo;
    setSavedText(lead.memo);
  }, [lead.memo]);
  const save = () => {
    const submitted = currentText.current;
    if (saving.current || submitted === serverText.current) return;
    saving.current = true;
    setState('saving');
    setError(null);
    m.mutate({ id: lead.id, arg: submitted }, {
      onSuccess: (updated) => {
        serverText.current = updated.memo;
        setSavedText(updated.memo);
        setState(currentText.current === updated.memo ? 'saved' : 'idle');
      },
      onError: (err) => { setState('error'); setError(errorMessage(err)); },
      onSettled: () => { saving.current = false; },
    });
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
          currentText.current = e.target.value;
          setText(e.target.value);
          setError(null);
          if (!saving.current) setState('idle');
        }}
        onBlur={save}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
          if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur();
        }}
      />
      {error ? <p className="field-error" role="alert">메모를 저장하지 못했습니다: {error}</p> : null}
      <span className={`save-state save-${state}`} aria-live="polite">
        {state === 'saving' ? '저장 중…' : state === 'saved' ? '저장됨' : state === 'error' ? '저장 실패 — 다시 시도하세요' : text !== savedText ? '변경 내용 저장 필요' : ''}
      </span>
      {state === 'error' || text !== savedText ? <Button size="sm" busy={state === 'saving'} onClick={save}>{state === 'error' ? '메모 저장 다시 시도' : '메모 저장'}</Button> : null}
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

export function DraftBox({ lead, mode }: { lead: LeadDetail; mode: 'demo' | 'live' }) {
  const [text, setText] = useState(lead.draft?.text ?? '');
  const [savedText, setSavedText] = useState(lead.draft?.text ?? '');
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const currentText = useRef(text);
  const serverText = useRef(savedText);
  const saving = useRef(false);
  const toast = useToast();
  const gen = useLeadMutation((port, id) => port.generateDraft(id));
  const save = useLeadMutation((port, id, draftText: string) => port.updateLead(id, { draftText }));
  useEffect(() => {
    const next = lead.draft?.text ?? '';
    // 저장 응답이 도착하는 동안 이어 쓴 글은 서버의 이전 글로 덮지 않는다.
    if (currentText.current === serverText.current) {
      currentText.current = next;
      setText(next);
    }
    serverText.current = next;
    setSavedText(next);
  }, [lead.draft?.text]);
  const saveDraft = () => {
    const submitted = currentText.current;
    if (saving.current || submitted === serverText.current) return;
    saving.current = true;
    setState('saving');
    setError(null);
    save.mutate({ id: lead.id, arg: submitted }, {
      onSuccess: (updated) => {
        serverText.current = updated.draft?.text ?? submitted;
        setSavedText(serverText.current);
        setState(currentText.current === serverText.current ? 'saved' : 'idle');
      },
      onError: (err) => { setState('error'); setError(errorMessage(err)); },
      onSettled: () => { saving.current = false; },
    });
  };

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
        onChange={(e) => {
          currentText.current = e.target.value;
          setText(e.target.value);
          setError(null);
          if (!saving.current) setState('idle');
        }}
        onBlur={saveDraft}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveDraft(); }
        }}
      />
      {error ? <p className="field-error" role="alert">초안을 저장하지 못했습니다: {error}</p> : null}
      <div className="draft-actions">
        {state === 'error' || text !== savedText ? <Button size="sm" busy={state === 'saving'} onClick={saveDraft}>{state === 'error' ? '초안 저장 다시 시도' : '초안 저장'}</Button> : null}
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
        <span className={`save-state save-${state}`} aria-live="polite">{state === 'saving' ? '초안 저장 중…' : state === 'saved' ? '초안 저장됨' : text !== savedText && state !== 'error' ? '변경 내용 저장 필요' : ''}</span>
      </div>
    </div>
  );
}

export function CurrencyTotals({ groups }: { groups: CurrencySales[] }) {
  return <>{groups.map((group) => <p className="outcome-sum" key={group.currency} aria-label={`${group.currency || '통화 미기록'} 계약·수금 합계`}>
    <strong>{group.currency || '통화 미기록'}</strong> · 계약 확인 <strong className="num">{formatMoneyTotal(group.contract, group.currency)}</strong> · 실제 수금 <strong className="num">{formatMoneyTotal(group.collected, group.currency)}</strong>
    {group.refunded.records ? <> · 환불 <span className="num">{formatMoneyTotal(group.refunded, group.currency)}</span></> : null}
    {group.directCost.records ? <> · 직접 비용 <span className="num">{formatMoneyTotal(group.directCost, group.currency)}</span></> : null}
  </p>)}</>;
}

export function Outcomes({ lead }: { lead: LeadDetail }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<OutcomeKind>('payment_received');
  const [amount, setAmount] = useState('');
  const [date, setDate] = useState(todayKst());
  const [note, setNote] = useState('');
  const [evidenceRef, setEvidenceRef] = useState('');
  const submitting = useRef(false);
  const toast = useToast();
  const m = useLeadMutation(
    (port, id, input: { kind: OutcomeKind; amount: number | null; occurredOn: string; note: string | null; evidenceRef: string | null }) =>
      port.addOutcome(id, input),
  );
  const groups = groupOutcomes(lead.outcomes);
  const normalizedAmount = amount.replace(/[,\s원]/g, '');
  const parsedAmount = amount.trim() === '' ? null : Number(normalizedAmount);
  const invalid = parsedAmount !== null && (!/^\d+$/.test(normalizedAmount) || !Number.isSafeInteger(parsedAmount) || parsedAmount < 0);

  return (
    <div className="outcomes">
      {lead.outcomes.length ? (
        <>
          <CurrencyTotals groups={groups} />
          <table className="outcome-table">
            <tbody>
              {lead.outcomes.map((o) => (
                <tr key={o.id}>
                  <td className="num">{formatDate(o.occurredOn + 'T00:00:00+09:00')}</td>
                  <td>{outcomeKindLabel[o.kind]}</td>
                  <td className="num">{o.amount === null ? '금액 미기록' : formatMoneyMinor(o.amount, o.currency)}</td>
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
            if (invalid || submitting.current) return;
            submitting.current = true;
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
                onSettled: () => { submitting.current = false; },
              },
            );
          }}
        >
          <label className="select">
            <span className="sr-only">종류</span>
            <select value={kind} disabled={m.isPending} onChange={(e) => setKind(e.target.value as OutcomeKind)} aria-label="종류">
              {(Object.keys(outcomeKindLabel) as OutcomeKind[]).map((k) => (
                <option key={k} value={k}>
                  {outcomeKindLabel[k]}
                </option>
              ))}
            </select>
          </label>
          <input aria-label="금액(원)" inputMode="numeric" placeholder="금액 (원, 모르면 비움)" disabled={m.isPending} value={amount} onChange={(e) => setAmount(e.target.value)} aria-invalid={invalid} aria-describedby={invalid ? `${lead.id}-outcome-amount-error` : undefined} />
          <input aria-label="날짜" type="date" disabled={m.isPending} value={date} onChange={(e) => setDate(e.target.value)} required />
          <input aria-label="메모" placeholder="메모" disabled={m.isPending} value={note} onChange={(e) => setNote(e.target.value)} />
          <input aria-label="증빙 참조" placeholder="증빙 참조 (예: 입금 내역)" disabled={m.isPending} value={evidenceRef} onChange={(e) => setEvidenceRef(e.target.value)} />
          <div className="form-actions">
            <Button type="submit" size="sm" variant="primary" busy={m.isPending} disabled={invalid}>
              기록 추가
            </Button>
            <Button size="sm" variant="ghost" disabled={m.isPending} onClick={() => setOpen(false)}>
              취소
            </Button>
          </div>
          {invalid ? <p className="field-error" id={`${lead.id}-outcome-amount-error`}>금액은 0 이상 9,007,199,254,740,991 이하의 정수로 입력하세요</p> : null}
        </form>
      ) : (
        <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setOpen(true)}>
          계약·수금 기록
        </Button>
      )}
    </div>
  );
}
