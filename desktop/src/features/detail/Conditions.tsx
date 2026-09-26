import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { formatDateTime, formatFull, formatFuzzyDate, formatPay, formatRelative } from '../../domain/format';
import {
  applicantScopeLabel,
  basisLabel,
  collaborationLabel,
  engagementLabel,
  intentLabel,
} from '../../domain/labels';
import type { Evidence, EvidenceKey, LeadDetail } from '../../domain/model';
import { intentTone, recruitDisplay, remoteClass, remoteDisplay } from '../../domain/present';
import { StatusMark } from '../../ui/StatusMark';

function EvidenceList({ items, onShow }: { items: Evidence[]; onShow: (id: string) => void }) {
  return (
    <ul className="evidence">
      {items.map((e) => (
        <li key={e.id} className="evidence-item">
          <q className="evidence-quote">{e.quote}</q>
          <span className={`basis basis-${e.basis}`}>{basisLabel[e.basis]}</span>
          {e.note ? <span className="evidence-note">{e.note}</span> : null}
          {e.span ? (
            <button type="button" className="link-btn" onClick={() => onShow(e.id)}>
              원문에서 보기
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function Row({ label, children, evidence, onShow }: { label: string; children: ReactNode; evidence?: Evidence[]; onShow: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const n = evidence?.length ?? 0;
  return (
    <div className="cond-row">
      <dt>{label}</dt>
      <dd>
        <div className="cond-value">
          <div className="cond-main">{children}</div>
          {n > 0 ? (
            <button type="button" className="evidence-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
              근거 {n}
            </button>
          ) : null}
        </div>
        {open && evidence ? <EvidenceList items={evidence} onShow={onShow} /> : null}
      </dd>
    </div>
  );
}

export function Conditions({ lead, categoryLabel, onShowEvidence, now }: { lead: LeadDetail; categoryLabel: (id: string) => string; onShowEvidence: (id: string) => void; now: Date }) {
  const ev = (k: EvidenceKey) => lead.evidence[k];
  const remote = remoteDisplay[remoteClass(lead)];
  const recruit = recruitDisplay(lead, now);
  const pay = formatPay(lead.pay);
  const cats = lead.categories.filter((c) => c !== 'other').map(categoryLabel);

  return (
    <dl className="conditions">
      <Row label="요청 유형" evidence={[...(ev('demandIntent') ?? []), ...(ev('engagementType') ?? [])]} onShow={onShowEvidence}>
        <StatusMark tone={intentTone(lead.demandIntent.value)}>{intentLabel[lead.demandIntent.value]}</StatusMark>
        <span className="cond-sub">{engagementLabel[lead.engagementType.value]}</span>
      </Row>
      <Row label="업무" onShow={onShowEvidence}>
        {cats.length ? cats.join(' · ') : '기타·미분류'}
      </Row>
      <Row label="재택·진행" evidence={[...(ev('workMode') ?? []), ...(ev('collaborationMode') ?? []), ...(ev('applicantScope') ?? [])]} onShow={onShowEvidence}>
        <StatusMark tone={remote.tone}>{remote.label}</StatusMark>
        <span className={`cond-sub${lead.collaborationMode.value === 'onsite_required' ? ' cell-warn' : ''}`}>{collaborationLabel[lead.collaborationMode.value]}</span>
        <span className={`cond-sub${lead.applicantScope.value === 'regional_restriction' ? ' cell-warn' : ''}`}>{applicantScopeLabel[lead.applicantScope.value]}</span>
        {lead.feedback.remote ? <span className="cond-mine">내 확인: {lead.feedback.remote === 'confirmed' ? '재택 가능' : '재택 불가'}</span> : null}
      </Row>
      <Row label="보수" evidence={ev('pay')} onShow={onShowEvidence}>
        <span className={pay.missing ? 'pay-missing' : 'num strong'}>{pay.text}</span>
        {pay.raw && pay.raw !== pay.text ? <span className="cond-sub">원문 “{pay.raw}”</span> : null}
      </Row>
      <Row label="모집" evidence={[...(ev('sourceStatus') ?? []), ...(ev('deadline') ?? [])]} onShow={onShowEvidence}>
        <StatusMark tone={recruit.tone}>{recruit.label}</StatusMark>
        <span className="cond-sub" title={lead.lastCheckedAt ? formatFull(lead.lastCheckedAt) : undefined}>
          {recruit.detail}
        </span>
        <span className="cond-sub">구인 마감: {lead.deadline.at || lead.deadline.raw ? formatFuzzyDate(lead.deadline, now) : '표시 없음'}</span>
      </Row>
      {lead.workPeriod.start.at || lead.workPeriod.start.raw ? (
        <Row label="작업 시작" onShow={onShowEvidence}>
          {formatFuzzyDate(lead.workPeriod.start, now)} <span className="cond-sub">(작업 일정 — 구인 마감과 다름)</span>
        </Row>
      ) : null}
      <Row label="게시·발견" onShow={onShowEvidence}>
        <span>게시 {formatFuzzyDate(lead.published, now)}</span>
        <span className="cond-sub" title={formatFull(lead.firstSeenAt)}>
          최초 발견 {formatDateTime(lead.firstSeenAt, now)} ({formatRelative(lead.firstSeenAt, now)})
        </span>
      </Row>
      {lead.postedRegion || lead.workplace || lead.applicantRegion || lead.foundIn.length ? (
        <Row label="지역" onShow={onShowEvidence}>
          {lead.postedRegion ? <span>게시 지역 {lead.postedRegion}</span> : null}
          {lead.workplace ? <span className="cond-sub">근무 장소 {lead.workplace}</span> : null}
          {lead.applicantRegion ? <span className="cond-sub">지원 지역 {lead.applicantRegion}</span> : null}
          {lead.foundIn.length ? <span className="cond-sub">발견 지역 {lead.foundIn.join(', ')}</span> : null}
        </Row>
      ) : null}
      <Row label="연락 경로" onShow={onShowEvidence}>
        {lead.contactChannel ?? '원문에서 확인'}
        <span className="cond-sub">자동 연락·지원은 하지 않습니다</span>
      </Row>
    </dl>
  );
}
