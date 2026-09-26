import { NotebookPen, RefreshCw, Star } from 'lucide-react';
import { memo } from 'react';
import { formatPay } from '../../domain/format';
import { intentShortLabel } from '../../domain/labels';
import type { LeadSummary, QueueId } from '../../domain/model';
import { intentTone, recruitDisplay, remoteCaveats, remoteClass, remoteDisplay } from '../../domain/present';
import { inQueue } from '../../domain/queues';
import { StatusMark } from '../../ui/StatusMark';

const REASON_GLYPH = { positive: '✓', caution: '!', negative: '✕' } as const;

function movedNote(l: LeadSummary): string {
  if (l.userMark === 'dismissed') return '제외함 · U 로 되돌리기';
  if (['contacted', 'negotiating', 'won', 'on_hold'].includes(l.salesStage)) return '진행 중으로 이동';
  if (l.userMark === null) return '관심 해제';
  return '목록 조건에서 벗어남';
}

export const LeadRow = memo(function LeadRow({
  lead,
  queue,
  selected,
  categoryLabel,
  now,
  onSelect,
  onOpen,
}: {
  lead: LeadSummary;
  queue: QueueId;
  selected: boolean;
  categoryLabel: (id: string) => string;
  now: Date;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
}) {
  const remote = remoteDisplay[remoteClass(lead)];
  const caveats = remoteCaveats(lead);
  const recruit = recruitDisplay(lead, now);
  const pay = formatPay(lead.pay);
  const moved = !inQueue(lead, queue);
  const cats = lead.categories.filter((c) => c !== 'other').map(categoryLabel);
  const catText = cats.length ? cats.slice(0, 2).join(' · ') : '기타';
  const score = lead.priority.total;

  return (
    <div
      role="option"
      id={`lead-row-${lead.id}`}
      aria-selected={selected}
      className={`row${selected ? ' is-selected' : ''}${moved ? ' is-moved' : ''}`}
      onClick={() => onSelect(lead.id)}
      onDoubleClick={() => onOpen(lead.id)}
    >
      <div className="c-score" title="우선순위 — 설명 가능한 규칙 점수이며 수주 확률이 아닙니다">
        <span className="score num">{score ?? '—'}</span>
        {lead.priority.unknownFactors > 0 ? <span className="score-unknown">미확인 {lead.priority.unknownFactors}</span> : null}
      </div>

      <div className="c-main">
        <div className="row-title">
          {lead.userMark === 'interested' ? <Star className="row-icon is-star" size={14} aria-label="관심" /> : null}
          <span className="row-title-text">{lead.title}</span>
          {lead.hasMemo ? <NotebookPen className="row-icon" size={13} aria-label="메모 있음" /> : null}
          {lead.activeJob ? (
            <span className="row-job">
              <RefreshCw size={12} aria-hidden="true" className="spin" />
              {lead.activeJob === 'recheck' ? '재확인 중' : '재분석 중'}
            </span>
          ) : null}
        </div>
        {moved ? (
          <div className="row-moved">{movedNote(lead)}</div>
        ) : (
          <div className="row-reasons">
            <span className="row-kind">
              {lead.demandIntent.value !== 'buyer_project' ? <span className={`tone-${intentTone(lead.demandIntent.value)}`}>{intentShortLabel[lead.demandIntent.value]}</span> : null}
              {catText}
            </span>
            {lead.reasons.slice(0, 3).map((r, i) => (
              <span key={i} className={`reason reason-${r.tone}`}>
                <span aria-hidden="true" className="reason-glyph">
                  {REASON_GLYPH[r.tone]}
                </span>
                {r.text}
              </span>
            ))}
          </div>
        )}
        <div className="row-meta">
          <span className={`meta-intent tone-${intentTone(lead.demandIntent.value)}`}>{intentShortLabel[lead.demandIntent.value]}</span>
          <span>{catText}</span>
          <StatusMark tone={remote.tone}>{remote.label}</StatusMark>
          <span className={pay.missing ? 'pay-missing' : 'num'}>{pay.text}</span>
          <StatusMark tone={recruit.tone}>{recruit.label}</StatusMark>
        </div>
      </div>

      <div className="c-intent">
        <span className={`tone-${intentTone(lead.demandIntent.value)}`}>{intentShortLabel[lead.demandIntent.value]}</span>
      </div>
      <div className="c-cat">{catText}</div>
      <div className="c-remote">
        <StatusMark tone={remote.tone}>{remote.label}</StatusMark>
        {caveats.length ? <small className="cell-sub cell-warn">{caveats.join(' · ')}</small> : null}
      </div>
      <div className="c-pay">
        <span className={pay.missing ? 'pay-missing' : 'num'} title={pay.raw ? `원문: ${pay.raw}` : undefined}>
          {pay.text}
        </span>
      </div>
      <div className="c-recruit">
        <StatusMark tone={recruit.tone}>{recruit.label}</StatusMark>
        <small className="cell-sub">{recruit.detail}</small>
      </div>
    </div>
  );
});
