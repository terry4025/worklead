import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Link, Square, Zap } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { usePortCtx } from '../../app/PortContext';
import { formatDateTime } from '../../domain/format';
import type { AutoApplyAttempt, AutoApplyStatus } from '../../domain/jobs';
import { copyText } from '../../platform/external';
import { Button } from '../../ui/Button';
import { StatusMark } from '../../ui/StatusMark';

export const autoApplyKey = ['jobs', 'auto-apply', 'status'] as const;
const stateLabels: Record<AutoApplyStatus['state'], string> = {
  stopped: '자동 지원 중지', waiting: '크롬 자동 지원 탭 연결 대기', connected: '크롬 연결됨 · 대상 공고 대기', paused: '자동 지원 일시 정지 · 확인 필요',
};
const attemptLabels: Record<AutoApplyAttempt['state'], string> = {
  claimed: '작업 준비 중', ready: '지원 양식 확인 중', submitting: '제출 결과 확인 중', submitted: '제출 확인',
  already_applied: '기존 지원 확인', held: '조건 확인 필요 · 보류', uncertain: '결과 미확인 · 재제출 중지',
};
const reasonLabels: Record<string, string> = {
  questions_required: '추가 질문이나 동의 항목 확인 필요', profile_changed: '저장된 자기소개 변경 확인 필요',
  condition_conflict: '공고 조건 확인 필요', condition_changed: '공고 조건이 변경됨', login_required: '당근 직접 로그인 필요',
  account_changed: '연결한 계정 변경 확인 필요', closed: '모집 마감', ui_unrecognized: '지원 양식 확인 필요', navigation_failed: '공고 열기 실패',
  submission_unconfirmed: '공식 제출 완료를 확인하지 못함', cancelled: '작업 중지', source_unverified: '원문 확인 필요', source_stale: '원문 상태 재확인 필요',
  connection_ended: '확장 연결 종료', lease_expired: '처리 가능 시간 만료', daily_limit: '오늘 제출 상한 도달', previous_permit: '기존 제출 시도 확인 필요',
  source_permission_unverified: '수집 소스 허용 상태 확인 필요', database_restored: '데이터 복원 후 다시 연결 필요', journal_unavailable: '지원 기록 저장 상태 확인 필요',
  completion_reconciled: '연결 복구 후 공식 완료 표시를 재대조함 · 재제출 없음',
};

export function AutoApplyPanel({ now, onOpenLead }: { now: Date; onOpenLead: (id: string) => void }) {
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: autoApplyKey, queryFn: () => port.jobs.autoApplyStatus(), refetchInterval: 2000 });
  const [minScore, setMinScore] = useState('50');
  const [dailyLimit, setDailyLimit] = useState('20');
  const [codeReady, setCodeReady] = useState(false);
  const [directory, setDirectory] = useState<string | null>(null);
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');
  // 자동 지원 전용 연결 키는 화면/캐시/저장소에 넣지 않고 복사할 때만 사용한다.
  const code = useRef<string | null>(null);
  const fieldsEdited = useRef(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; code.current = null; }; }, []);
  const clearCode = () => { code.current = null; setCodeReady(false); setCopied('idle'); };
  useEffect(() => {
    if (q.data && (!fieldsEdited.current || q.data.enabled)) {
      setMinScore(String(q.data.min_score)); setDailyLimit(String(q.data.daily_limit));
    }
  }, [q.data?.min_score, q.data?.daily_limit]);
  useEffect(() => { if (q.data && !q.data.enabled) clearCode(); }, [q.data?.enabled]);
  const start = useMutation({
    mutationFn: async () => {
      clearCode();
      await qc.cancelQueries({ queryKey: autoApplyKey, exact: true });
      const result = await port.jobs.autoApplyStart({ confirm: true, min_score: Number(minScore), daily_limit: Number(dailyLimit) });
      if (mounted.current) {
        code.current = JSON.stringify(result.connection);
        setCodeReady(true);
        setDirectory(result.extension_directory);
      }
      return result.status; // 비밀이 들어 있는 전체 응답을 mutation 캐시에 남기지 않는다.
    },
    onSuccess: async (status) => {
      await qc.cancelQueries({ queryKey: autoApplyKey, exact: true });
      fieldsEdited.current = false;
      qc.setQueryData(autoApplyKey, status);
    },
  });
  const stop = useMutation({
    mutationFn: async () => { clearCode(); await qc.cancelQueries({ queryKey: autoApplyKey, exact: true }); return port.jobs.autoApplyStop(); },
    onSuccess: async (status) => { await qc.cancelQueries({ queryKey: autoApplyKey, exact: true }); qc.setQueryData(autoApplyKey, status); },
  });
  const status = q.data;
  const score = Number(minScore);
  const limit = Number(dailyLimit);
  const valid = minScore.trim() !== '' && dailyLimit.trim() !== '' && Number.isInteger(score) && score >= 0 && score <= 100 && Number.isInteger(limit) && limit >= 1 && limit <= 200;
  const working = !q.isError && status?.enabled && status.state === 'connected';
  const processing = working && status.attempts.some((attempt) => ['claimed', 'ready', 'submitting'].includes(attempt.state));
  const hasError = q.isError || start.isError || stop.isError;
  return <section className="auto-apply-panel" aria-labelledby="auto-apply-heading">
    <header className="auto-apply-head"><div><h2 id="auto-apply-heading"><Zap size={18} aria-hidden="true" /> 점수 기준 자동 지원</h2><p className="field-hint">Worklead를 켜 둔 동안 기준에 맞는 공고를 확인하고, 연결한 당근 탭에서 지원합니다.</p></div><StatusMark tone={hasError || status?.state === 'paused' ? 'warn' : working ? 'good' : 'muted'}>{q.isError ? '현재 자동 지원 상태 미확인' : processing ? '자동 지원 처리 중' : status ? stateLabels[status.state] : '자동 지원 상태 확인 중'}</StatusMark></header>
    <div className="auto-apply-columns"><div>
      <div className="auto-apply-fields"><label className="field-label">최소 점수<input aria-label="자동 지원 최소 점수" type="number" min={0} max={100} step={1} value={minScore} disabled={status?.enabled || start.isPending} onChange={(e) => { fieldsEdited.current = true; setMinScore(e.target.value); }} /></label><label className="field-label">하루 제출 상한<input aria-label="자동 지원 하루 제출 상한" type="number" min={1} max={200} step={1} value={dailyLimit} disabled={status?.enabled || start.isPending} onChange={(e) => { fieldsEdited.current = true; setDailyLimit(e.target.value); }} /></label></div>
      <p className="field-hint">{valid ? score > 0 ? `${score - 1}점은 제외하고 ${score}점부터 대상에 포함합니다.` : '0점부터 대상에 포함합니다.' : '점수는 0~100, 하루 상한은 1~200의 정수로 입력하세요.'} 점수 외에도 모집 상태·지원 이력·필수 질문을 확인합니다.</p>
      <p className="auto-apply-consent">시작하면 기준에 맞는 공고에 당근에 저장된 기본 자기소개로 개별 확인 없이 지원합니다. 확인 필요 공고도 포함하며, 추가 질문·새 약관·바뀐 양식은 보류합니다. Worklead의 편집 중인 초안은 전송하지 않습니다.</p>
      <p className="field-hint">새 공고부터 확인한 뒤 기존에 저장한 미지원 공고도 점검합니다. 오래된 공고는 현재 지원 양식을 확인해야 진행하며, 기존 지원·보류·결과 미확인 건은 다시 제출하지 않습니다.</p>
      <div className="draft-actions"><Button size="sm" variant="primary" icon={<Link size={14} />} busy={start.isPending} disabled={!status || q.isError || !valid || stop.isPending || mode === 'demo' || (status.enabled && status.state === 'connected')} onClick={() => { stop.reset(); start.mutate(); }}>{status?.enabled ? '자동 지원 연결 다시 준비' : '기준에 맞는 공고 자동 지원 시작'}</Button>{status?.enabled ? <Button size="sm" icon={<Square size={13} />} busy={stop.isPending} disabled={start.isPending} onClick={() => { start.reset(); stop.mutate(); }}>자동 지원 중지</Button> : null}</div>
      {codeReady ? <div className="chrome-connection-code"><p className="field-hint">로그인한 당근 탭의 Worklead 확장 팝업에서 <strong>자동 지원 연결</strong> 입력란에 붙여 넣고 ‘자동 지원 탭 연결’을 누르세요. 메시지 감시 연결코드와 별개입니다.</p><Button size="sm" icon={<Copy size={14} />} onClick={async () => { if (code.current) setCopied(await copyText(code.current) ? 'copied' : 'failed'); }}>자동 지원 연결코드 복사</Button><p className={copied === 'failed' ? 'field-error' : 'field-hint'} role="status">{copied === 'copied' ? '자동 지원 전용 코드를 복사했습니다.' : copied === 'failed' ? '복사하지 못했습니다. 다시 시도하세요.' : '실제 확장 연결 전에는 지원을 시작하지 않습니다.'}</p>{directory ? <p className="field-hint chrome-extension-path">확장 폴더: {directory}</p> : null}</div> : null}
      <p className="field-hint">앱을 종료하면 자동 지원이 중지됩니다. 다시 시작한 뒤 새 코드로 연결하세요. 결과 미확인 건은 자동 재제출하지 않습니다.</p>
      <p className="field-hint">같은 연결에서 응답이 끊기면 완료 표시만 최대 3회 자동 대조합니다. 지원 버튼을 다시 누르지 않으며, 확인되지 않으면 결과 미확인을 유지합니다.</p>
      {mode === 'demo' ? <p className="field-hint">데모에서는 자동 지원 연결·실제 제출을 실행하지 않습니다.</p> : null}
      {hasError ? <div role="alert"><p className="field-error">{start.isError ? '자동 지원 연결을 준비하지 못했습니다.' : stop.isError ? '중지 결과를 확인하지 못했습니다. 현재 상태를 다시 확인한 뒤 중지를 재시도하세요.' : '현재 상태를 확인하지 못했습니다. 이전 기록을 정상 실행 상태로 보지 마세요.'}</p><Button size="sm" disabled={q.isFetching} onClick={() => void q.refetch()}>자동 지원 상태 다시 확인</Button></div> : null}
    </div><div className="auto-apply-results">
      <p className="field-hint">{q.isError ? '마지막으로 확인한 상태' : status?.message ?? '아직 연결 상태를 확인하지 못했습니다.'}</p>
      {status ? <dl className="auto-apply-metrics"><div><dt>오늘 제출 확인</dt><dd>{status.today_submitted} / {status.daily_limit}</dd></div><div><dt>처리 중·예약</dt><dd>{status.today_reserved}</dd></div><div><dt>보류</dt><dd>{status.held}</dd></div><div><dt>결과 미확인</dt><dd>{status.uncertain}</dd></div></dl> : null}
      <h3>최근 처리 기록</h3>
      {!status?.attempts.length ? <p className="field-hint">처리 기록이 없습니다. 연결 대기는 지원 완료가 아닙니다.</p> : <ol className="auto-apply-attempts">{status.attempts.map((attempt) => <li key={attempt.id}><div><Button size="sm" variant="ghost" onClick={() => onOpenLead(attempt.lead_id)}>{attempt.title}</Button><StatusMark tone={attempt.state === 'submitted' || attempt.state === 'already_applied' ? 'good' : attempt.state === 'uncertain' || attempt.state === 'held' ? 'warn' : 'muted'}>{attemptLabels[attempt.state]}</StatusMark></div>{attempt.reason_code && !['ready', 'submitted', 'already_applied'].includes(attempt.reason_code) ? <p className="field-hint">{reasonLabels[attempt.reason_code] ?? '지원 조건 또는 처리 결과 확인 필요'}</p> : null}<p className="field-hint">{formatDateTime(attempt.finished_at ?? attempt.created_at, now)}</p></li>)}</ol>}
    </div></div>
  </section>;
}
