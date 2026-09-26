import { ArrowLeft, ExternalLink, RefreshCw, Sparkles, Star, XCircle } from 'lucide-react';
import { forwardRef, useCallback, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { formatDateTime, formatFull, formatFuzzyDate } from '../../domain/format';
import { analysisStatusLabel, recommendationLabel } from '../../domain/labels';
import type { LeadDetail, SourceInfo } from '../../domain/model';
import { patchSummary, useLead, useLeadMutation } from '../../app/queries';
import { usePortCtx } from '../../app/PortContext';
import { openExternal } from '../../platform/external';
import { Button } from '../../ui/Button';
import { ErrorState, errorMessage } from '../../ui/States';
import { scrollWithin } from '../../ui/scroll';
import { StatusMark } from '../../ui/StatusMark';
import { useToast } from '../../ui/Toasts';
import { Conditions } from './Conditions';
import { Profit } from './Profit';
import { DraftBox, FeedbackControls, MemoBox, Outcomes, StageSelect } from './Records';
import { SourceText, type Mark } from './SourceText';

export interface DetailHandle {
  openOriginal: () => void;
  toggleInterested: () => void;
  toggleDismissed: () => void;
  recheck: () => void;
  focusMemo: () => void;
  focus: () => void;
}

const REC_TONE = { recommended: 'good', needs_review: 'warn', excluded: 'muted' } as const;

export const LeadDetailPane = forwardRef<
  DetailHandle,
  {
    leadId: string | null;
    sources: SourceInfo[];
    categoryLabel: (id: string) => string;
    now: Date;
    overlay: boolean;
    onClose: () => void;
    onDismissed: (id: string) => void;
  }
>(function LeadDetailPane({ leadId, sources, categoryLabel, now, overlay, onClose, onDismissed }, ref) {
  const q = useLead(leadId);
  const lead = q.data;
  const partial = q.isPlaceholderData || (lead !== undefined && !('bodyText' in lead));
  const toast = useToast();
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const rootRef = useRef<HTMLElement>(null);
  const memoRef = useRef<HTMLTextAreaElement>(null);
  const [activeMark, setActiveMark] = useState<string | null>(null);

  const mark = useLeadMutation((p, id, userMark: LeadDetail['userMark']) => p.updateLead(id, { userMark }));
  const [rechecking, setRechecking] = useState(false);
  const [reanalyzing, setReanalyzing] = useState(false);

  const source = lead ? sources.find((s) => s.id === lead.sourceId) : undefined;

  const openOriginal = useCallback(async () => {
    if (!lead || partial) return;
    const full = lead as LeadDetail;
    if (!full.originalUrl) {
      toast.show({ tone: 'info', text: mode === 'demo' ? '데모 리드라 원문 주소가 없습니다' : '원문 주소가 없습니다 (수동 입력 등)' });
      return;
    }
    const r = await openExternal(full.originalUrl);
    if (r === 'rejected') toast.show({ tone: 'bad', text: 'http·https 가 아닌 주소라 열지 않았습니다' });
    if (r === 'copied') toast.show({ tone: 'info', text: '원문 주소를 복사했습니다. 브라우저에 붙여 넣으세요.' });
  }, [lead, partial, toast, mode]);

  const setMark = useCallback(
    (next: LeadDetail['userMark']) => {
      if (!lead) return;
      patchSummary(qc, lead.id, { userMark: next });
      mark.mutate(
        { id: lead.id, arg: next },
        {
          onError: (err) => {
            patchSummary(qc, lead.id, { userMark: lead.userMark });
            toast.show({ tone: 'bad', text: `저장하지 못했습니다: ${errorMessage(err)}` });
          },
        },
      );
      if (next === 'dismissed') {
        onDismissed(lead.id);
        toast.show({ tone: 'info', text: '제외했습니다', action: { label: '되돌리기 (U)', run: () => setMark(null) } });
      }
    },
    [lead, mark, qc, toast, onDismissed],
  );

  const recheck = useCallback(async () => {
    if (!lead) return;
    setRechecking(true);
    try {
      await port.recheckLead(lead.id);
      patchSummary(qc, lead.id, { activeJob: 'recheck' });
      toast.show({ tone: 'info', text: '재확인을 요청했습니다. 끝나면 모집 상태가 갱신됩니다.' });
    } catch (err) {
      toast.show({ tone: 'bad', text: errorMessage(err) }, 7000);
    } finally {
      setRechecking(false);
    }
  }, [lead, port, qc, toast]);

  const reanalyze = useCallback(async () => {
    if (!lead) return;
    setReanalyzing(true);
    try {
      await port.reanalyzeLead(lead.id);
      patchSummary(qc, lead.id, { activeJob: 'reanalyze' });
    } catch (err) {
      toast.show({ tone: 'bad', text: errorMessage(err) });
    } finally {
      setReanalyzing(false);
    }
  }, [lead, port, qc, toast]);

  const showEvidence = useCallback((id: string) => {
    setActiveMark(id);
    window.requestAnimationFrame(() => scrollWithin(document.getElementById(`mark-${id}`), rootRef.current, 'center'));
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      openOriginal: () => void openOriginal(),
      toggleInterested: () => lead && setMark(lead.userMark === 'interested' ? null : 'interested'),
      toggleDismissed: () => lead && setMark(lead.userMark === 'dismissed' ? null : 'dismissed'),
      recheck: () => void recheck(),
      focusMemo: () => {
        const el = memoRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
      },
      focus: () => rootRef.current?.focus(),
    }),
    [openOriginal, lead, setMark, recheck],
  );

  const marks = useMemo<Mark[]>(() => {
    if (!lead || partial) return [];
    const full = lead as LeadDetail;
    const out: Mark[] = [];
    for (const list of Object.values(full.evidence)) for (const e of list ?? []) if (e.span) out.push({ id: e.id, start: e.span.start, end: e.span.end, tone: 'evidence' });
    for (const r of full.risks) if (r.span) out.push({ id: r.id, start: r.span.start, end: r.span.end, tone: 'risk' });
    return out;
  }, [lead, partial]);

  if (!leadId) {
    return (
      <aside className="detail detail-empty" aria-label="리드 상세">
        <p>목록에서 리드를 선택하면 판단 근거가 여기에 표시됩니다.</p>
        <p className="muted-text">
          <kbd>↑</kbd> <kbd>↓</kbd> 이동 · <kbd>Enter</kbd> 상세로 · <kbd>?</kbd> 단축키
        </p>
      </aside>
    );
  }

  if (q.isError && !lead) {
    return (
      <aside className="detail" aria-label="리드 상세">
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      </aside>
    );
  }

  if (!lead) {
    return (
      <aside className="detail" aria-label="리드 상세" aria-busy="true">
        <div className="detail-loading">불러오는 중…</div>
      </aside>
    );
  }

  const full = partial ? null : (lead as LeadDetail);
  const recTone = REC_TONE[lead.recommendation];

  return (
    <aside ref={rootRef} className="detail" aria-label="리드 상세" tabIndex={-1} id="lead-detail">
      <header className="detail-head">
        {overlay ? (
          <button type="button" className="link-btn back-btn" onClick={onClose}>
            <ArrowLeft size={15} aria-hidden="true" /> 목록으로 <kbd>Esc</kbd>
          </button>
        ) : null}
        <h2 className="detail-title">{lead.title}</h2>
        <p className="detail-meta">
          <span>{source?.name ?? lead.sourceId}</span>
          {lead.postedRegion ? <span>{lead.postedRegion}</span> : null}
          <span title={lead.published.at ? formatFull(lead.published.at) : undefined}>게시 {formatFuzzyDate(lead.published, now)}</span>
          <span title={formatFull(lead.firstSeenAt)}>발견 {formatDateTime(lead.firstSeenAt, now)}</span>
        </p>
        <div className="detail-actions" role="toolbar" aria-label="리드 동작">
          <Button size="sm" variant="primary" icon={<ExternalLink size={14} />} shortcut="O" onClick={() => void openOriginal()} disabled={!full}>
            원문 열기
          </Button>
          <Button size="sm" icon={<Star size={14} />} shortcut="S" pressed={lead.userMark === 'interested'} onClick={() => setMark(lead.userMark === 'interested' ? null : 'interested')}>
            관심
          </Button>
          <Button size="sm" icon={<XCircle size={14} />} shortcut="X" pressed={lead.userMark === 'dismissed'} onClick={() => setMark(lead.userMark === 'dismissed' ? null : 'dismissed')}>
            제외
          </Button>
          <Button size="sm" icon={<RefreshCw size={14} className={lead.activeJob === 'recheck' ? 'spin' : undefined} />} shortcut="R" busy={rechecking} disabled={lead.activeJob === 'recheck'} onClick={() => void recheck()}>
            {lead.activeJob === 'recheck' ? '재확인 중' : '재확인'}
          </Button>
          {full ? <StageSelect lead={full} /> : null}
        </div>
      </header>

      <div className="detail-body">
        <section className="d-section" aria-labelledby="sec-judge">
          <h3 id="sec-judge" className="d-h">
            판단
          </h3>
          <div className="judge-line">
            <StatusMark tone={recTone}>{recommendationLabel[lead.recommendation]}</StatusMark>
            <span className="judge-score">
              우선순위 <strong className="num">{lead.priority.total ?? '—'}</strong>
              <span className="muted-text">/100 · 규칙 점수, 수주 확률 아님</span>
            </span>
            {lead.analysisStatus !== 'complete' ? (
              <StatusMark tone={lead.analysisStatus === 'failed' ? 'bad' : 'unknown'}>{analysisStatusLabel[lead.analysisStatus]}</StatusMark>
            ) : null}
          </div>
          <ul className="reasons-full">
            {lead.reasons.map((r, i) => (
              <li key={i} className={`reason reason-${r.tone}`}>
                <span aria-hidden="true" className="reason-glyph">
                  {r.tone === 'positive' ? '✓' : r.tone === 'caution' ? '!' : '✕'}
                </span>
                {r.text}
              </li>
            ))}
          </ul>
          {full?.analysis.summary ? (
            <p className="summary">
              {full.analysis.summary}
              <span className="engine-tag">{full.analysis.summaryEngine === 'ai' ? 'AI 요약' : '원문 첫 문장'}</span>
            </p>
          ) : null}
          {full?.analysis.failure ? (
            <div className="inline-alert inline-alert-bad" role="note">
              <span>
                AI 분석 실패 ({full.analysis.failure.code}) — {full.analysis.failure.message} 규칙 기반 판정·근거는 그대로 사용합니다.
              </span>
              <Button size="sm" variant="ghost" icon={<Sparkles size={14} />} busy={reanalyzing || lead.activeJob === 'reanalyze'} onClick={() => void reanalyze()}>
                재분석
              </Button>
            </div>
          ) : null}
          {full?.analysis.conversionOpportunity ? <p className="note-box">제안 기회 (의뢰 아님): {full.analysis.conversionOpportunity}</p> : null}
          {full?.score ? (
            <details className="score-details">
              <summary>점수 구성 보기</summary>
              <table className="score-table">
                <tbody>
                  {full.score.factors.map((f) => (
                    <tr key={f.key}>
                      <th scope="row">{f.label}</th>
                      <td className="score-bar-cell">
                        {f.score === null ? (
                          <span className="score-na">미평가</span>
                        ) : (
                          <span className="score-bar" aria-hidden="true">
                            <span style={{ width: `${(f.score / f.max) * 100}%` }} />
                          </span>
                        )}
                      </td>
                      <td className="num score-num">{f.score === null ? '—' : `${f.score}/${f.max}`}</td>
                      <td className="score-reason">{f.reason}</td>
                    </tr>
                  ))}
                  {full.score.riskPenalty.score ? (
                    <tr>
                      <th scope="row">위험 감점</th>
                      <td />
                      <td className="num score-num is-negative">−{full.score.riskPenalty.score}</td>
                      <td className="score-reason">{full.score.riskPenalty.reasons.join(', ')}</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
              <p className="field-hint">
                미평가 요소는 0점으로 채우거나 나머지로 다시 계산하지 않습니다 · 규칙 {full.score.ruleVersion}
              </p>
            </details>
          ) : null}
        </section>

        {full ? (
          <>
            <section className="d-section" aria-labelledby="sec-cond">
              <h3 id="sec-cond" className="d-h">
                조건과 근거
              </h3>
              <Conditions lead={full} categoryLabel={categoryLabel} onShowEvidence={showEvidence} now={now} />
            </section>

            {full.risks.length || full.analysis.uncertain.length || full.analysis.questions.length ? (
              <section className="d-section" aria-labelledby="sec-check">
                <h3 id="sec-check" className="d-h">
                  확인할 점
                </h3>
                {full.risks.length ? (
                  <ul className="risk-list">
                    {full.risks.map((r) => (
                      <li key={r.id}>
                        <StatusMark tone="bad">{r.label}</StatusMark>
                        {r.quote ? (
                          <button type="button" className="link-btn" onClick={() => showEvidence(r.id)}>
                            “{r.quote}”
                          </button>
                        ) : null}
                      </li>
                    ))}
                    <li className="field-hint">원문에 근거한 위험 신호입니다. 작성자를 단정하지 않습니다.</li>
                  </ul>
                ) : null}
                {full.analysis.uncertain.length ? (
                  <p className="uncertain">
                    <span className="field-label">불확실</span> {full.analysis.uncertain.join(' · ')}
                  </p>
                ) : null}
                {full.analysis.questions.length ? (
                  <ol className="questions">
                    {full.analysis.questions.map((qq) => (
                      <li key={qq}>{qq}</li>
                    ))}
                  </ol>
                ) : null}
                {full.analysis.nextAction ? <p className="next-action">다음 행동: {full.analysis.nextAction}</p> : null}
              </section>
            ) : null}

            <section className="d-section" aria-labelledby="sec-profit">
              <h3 id="sec-profit" className="d-h">
                수익성 <span className="d-h-sub">추정</span>
              </h3>
              <Profit p={full.profitability} />
            </section>

            <section className="d-section" aria-labelledby="sec-src">
              <h3 id="sec-src" className="d-h">
                원문 <span className="d-h-sub">안전한 텍스트 · 근거 구간 강조</span>
              </h3>
              {full.bodyText ? (
                <SourceText text={full.bodyText} marks={marks} activeId={activeMark} />
              ) : (
                <p className="muted-text">보존 기간이 지나 원문 본문을 삭제했습니다. 원문 링크로 확인하세요.</p>
              )}
            </section>

            <section className="d-section" aria-labelledby="sec-mine">
              <h3 id="sec-mine" className="d-h">
                내 기록
              </h3>
              <MemoBox lead={full} ref={memoRef} />
              <FeedbackControls lead={full} />
            </section>

            <section className="d-section" aria-labelledby="sec-draft">
              <h3 id="sec-draft" className="d-h">
                문의 초안
              </h3>
              <DraftBox lead={full} mode={mode} />
            </section>

            <section className="d-section" aria-labelledby="sec-out">
              <h3 id="sec-out" className="d-h">
                계약·수금
              </h3>
              <Outcomes lead={full} />
            </section>

            <section className="d-section" aria-labelledby="sec-prov">
              <details>
                <summary id="sec-prov" className="d-h d-h-summary">
                  발견 경로 · 관련 글 · 활동
                </summary>
                {full.discoveryPaths.length ? (
                  <ul className="plain-list">
                    {full.discoveryPaths.map((p) => (
                      <li key={`${p.regionScope}-${p.queryGroup}`}>
                        {p.regionLabel ?? p.regionScope} · 검색어 묶음 {p.queryGroup} · {p.timesSeen}회 발견 · 마지막 {formatDateTime(p.lastSeenAt, now)}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {full.related.length ? (
                  <ul className="plain-list">
                    {full.related.map((r) => (
                      <li key={r.id}>
                        {{ duplicate: '중복', repost: '재게시 후보', similar: '유사 글 후보' }[r.relation]}: {r.title}
                        {r.basis ? <span className="muted-text"> — {r.basis}</span> : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <ul className="activity">
                  {full.activity.map((a, i) => (
                    <li key={i}>
                      <span className="num muted-text">{formatDateTime(a.at, now)}</span> {a.text}
                    </li>
                  ))}
                </ul>
                <p className="field-hint">
                  분석 {full.analysis.engine ?? '—'} · 파서 {full.parserVersion ?? '—'}
                </p>
              </details>
            </section>
          </>
        ) : (
          <div className="detail-loading">상세 불러오는 중…</div>
        )}
      </div>
    </aside>
  );
});
