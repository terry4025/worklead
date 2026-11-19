import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Link, Square, Zap } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { usePortCtx } from '../../app/PortContext';
import { formatDateTime } from '../../domain/format';
import type { AutoApplyAttempt, AutoApplyCandidate, AutoApplyStatus } from '../../domain/jobs';
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
const candidateLabels: Record<AutoApplyCandidate['stage'], string> = {
  queued: '지원 후보 대기', source_refresh: '원문 상태 재확인', rejected: '지원 조건 제외',
  form_review: '지원 양식 확인 중', permit_issued: '제출 허가 · 결과 확인 중', submitted: '제출 확인',
  already_applied: '기존 지원 확인', retry_wait: '일시 실패 · 재시도 대기', held: '조건 확인 필요 · 보류',
  uncertain: '결과 미확인 · 재제출 중지',
};
const queueLabels: Record<string, string> = {
  starting: '자동 지원 연결을 준비하고 있습니다', login_required: '당근 로그인을 기다립니다',
  blocked: '자동 지원 연결을 확인해야 합니다', error: '자동 지원 창을 다시 열어야 합니다',
  stopped: '자동 지원이 꺼져 있습니다', paused: '계정·지원 정보 확인으로 일시 중지했습니다',
  connection_wait: '확장 연결을 기다립니다', connection_stale: '크롬 응답이 끊겼습니다',
  daily_limit: '오늘 제출 상한에 도달했습니다', processing: '공고의 지원 절차를 확인하고 있습니다',
  source_permission_unverified: '수집 허용 상태를 확인해야 합니다', source_refresh: '저장 공고 원문을 다시 확인하고 있습니다',
  retry_wait: '일시 실패 공고의 재시도를 기다립니다', no_candidates: '지금 지원 가능한 공고가 없습니다',
  ready: '조건에 맞는 공고가 대기 중입니다',
};
const queueHelp: Record<string, string> = {
  starting: 'Worklead가 전용 당근 창과 자동 지원 등록을 준비하고 있습니다.',
  login_required: '자동으로 열린 당근 창에서 로그인하면 Worklead가 연결을 이어갑니다.',
  blocked: '계정·소개·사이트 접근 상태와 표시된 중단 사유를 확인하세요.',
  error: '자동 연결·지원 재개를 눌러 전용 당근 창을 다시 준비하세요.',
  connection_wait: 'Worklead 실행 상태와 당근 전용 탭·확장 연결을 확인하세요.',
  connection_stale: 'Worklead 실행 상태와 당근 전용 탭·확장 연결을 확인하세요.',
  source_permission_unverified: '수집 설정에서 당근 소스의 허용 상태를 확인하세요.',
  paused: '처음 연결한 계정·기본 자기소개와 중단 사유를 확인하세요.',
};
const reasonLabels: Record<string, string> = {
  questions_required: '추가 질문이나 동의 항목 확인 필요', profile_changed: '저장된 자기소개 변경 확인 필요',
  condition_conflict: '공고 조건 확인 필요', condition_changed: '공고 조건이 변경됨', login_required: '당근 직접 로그인 필요',
  account_changed: '연결한 계정 변경 확인 필요', closed: '모집 마감', ui_unrecognized: '지원 양식 확인 필요', navigation_failed: '공고 열기 실패',
  submission_unconfirmed: '공식 제출 완료를 확인하지 못함', cancelled: '작업 중지', source_unverified: '원문 확인 필요', source_stale: '원문 상태 재확인 필요',
  connection_ended: '확장 연결 종료', lease_expired: '처리 가능 시간 만료', daily_limit: '오늘 제출 상한 도달', previous_permit: '기존 제출 시도 확인 필요',
  source_permission_unverified: '수집 소스 허용 상태 확인 필요', database_restored: '데이터 복원 후 다시 연결 필요', journal_unavailable: '지원 기록 저장 상태 확인 필요',
  completion_reconciled: '연결 복구 후 공식 완료 표시를 재대조함 · 재제출 없음',
  browser_timeout: '당근 탭 응답 지연', retry_exhausted: '자동 재시도 3회 상한 도달',
  ai_text_unavailable: '지원 문구를 만들지 못함',
  refresh_queued: '원문 재확인 작업 대기 중', refresh_running: '현재 원문을 읽는 중',
  refresh_failed: '원문 재확인 실패 · 수집 상태 확인 필요', refresh_cancelled: '원문 재확인 작업 중단',
  refresh_paused: '원문 재확인 일시 중지 · 실행 기록 확인 필요', refresh_partial: '원문 확인이 일부만 완료됨 · 확인 필요',
  refresh_succeeded: '최근 원문 확인 작업 완료 · 지원 조건 재평가 필요', refresh_backoff: '원문 읽기 실패 후 재확인 대기',
  detail_permission_blocked: '원문 상세 수집이 허용되지 않음', source_refresh_due: '원문 재확인 예약 대기',
};
const refreshLabels: Record<string, string> = { queued: '작업 대기', running: '원문 읽기 진행 중', paused: '일시 중지', failed: '실패', cancelled: '작업 중단', succeeded: '최근 작업 완료' };

function RetryInfo({ count, after, now }: { count?: number; after?: string | null; now: Date }) {
  if (!count && !after) return null;
  return <p className="field-hint auto-apply-retry">재시도 {count ?? 0} / 3회{after ? <> · 다음 재시도: <time dateTime={after}>{formatDateTime(after, now)}</time></> : null}</p>;
}

export function AutoApplyPanel({ now, onOpenLead }: { now: Date; onOpenLead: (id: string) => void }) {
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: autoApplyKey, queryFn: () => port.jobs.autoApplyStatus(), refetchInterval: 2000 });
  const [minScore, setMinScore] = useState('50');
  const [dailyLimit, setDailyLimit] = useState('20');
  const [remember, setRemember] = useState(true);
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
  useEffect(() => { if (q.data && (!q.data.enabled || q.data.provider === 'managed_edge')) clearCode(); }, [q.data?.enabled, q.data?.provider]);
  useEffect(() => { if (q.data?.enabled) setRemember(q.data.remembers_connection); }, [q.data?.enabled, q.data?.remembers_connection]);
  const setup = useMutation({
    mutationFn: async () => {
      clearCode();
      await qc.cancelQueries({ queryKey: autoApplyKey, exact: true });
      return port.jobs.autoApplySetup({ confirm: true, min_score: Number(minScore), daily_limit: Number(dailyLimit) });
    },
    onSuccess: async (status) => {
      await qc.cancelQueries({ queryKey: autoApplyKey, exact: true });
      fieldsEdited.current = false;
      qc.setQueryData(autoApplyKey, status);
    },
  });
  const start = useMutation({
    mutationFn: async () => {
      clearCode();
      await qc.cancelQueries({ queryKey: autoApplyKey, exact: true });
      const result = await port.jobs.autoApplyStart({ confirm: true, min_score: Number(minScore), daily_limit: Number(dailyLimit), remember_connection: remember });
      if (mounted.current) {
        code.current = result.connection ? JSON.stringify(result.connection) : null;
        setCodeReady(result.connection !== null);
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
  const forget = useMutation({
    mutationFn: async () => { clearCode(); await qc.cancelQueries({ queryKey: autoApplyKey, exact: true }); return port.jobs.autoApplyForget(); },
    onSuccess: async (status) => { await qc.cancelQueries({ queryKey: autoApplyKey, exact: true }); qc.setQueryData(autoApplyKey, status); },
  });
  const status = q.data;
  const managed = status?.provider === 'managed_edge';
  const busy = setup.isPending || start.isPending || stop.isPending || forget.isPending;
  const score = Number(minScore);
  const limit = Number(dailyLimit);
  const valid = minScore.trim() !== '' && dailyLimit.trim() !== '' && Number.isInteger(score) && score >= 0 && score <= 100 && Number.isInteger(limit) && limit >= 1 && limit <= 200;
  const working = !q.isError && status?.enabled && status.state === 'connected' && !status.needs_user_action;
  const processing = working && status.attempts.some((attempt) => ['claimed', 'ready', 'submitting'].includes(attempt.state));
  const hasError = q.isError || setup.isError || start.isError || stop.isError || forget.isError;
  const waitingLabel = status?.paired && status.remembers_connection ? '저장된 크롬 연결 자동 복구 중' : '크롬 자동 지원 탭 연결 대기';
  const managedLabel = status?.state === 'stopped' ? '자동 지원 중지'
    : status?.setup_stage === 'login_required' ? '당근 로그인 필요'
    : status?.setup_stage === 'starting' ? '자동 연결 준비 중'
    : status?.setup_stage === 'error' || status?.setup_stage === 'blocked' ? '자동 연결 확인 필요'
    : status?.state === 'paused' ? '자동 지원 일시 정지 · 확인 필요'
    : status?.state === 'connected' ? '자동 연결됨 · 대상 공고 대기' : '자동 지원 창 연결 대기';
  const queueLabel = managed && status?.queue_reason === 'connection_wait' ? '자동 지원 창을 연결하고 있습니다'
    : managed && status?.queue_reason === 'connection_stale' ? '자동 지원 창 응답을 다시 확인하고 있습니다'
    : queueLabels[status?.queue_reason ?? ''] ?? '지원 대기 사유를 확인해야 합니다';
  const help = managed && ['connection_wait', 'connection_stale'].includes(status?.queue_reason ?? '')
    ? 'Worklead가 전용 당근 창을 관리합니다. 로그인 안내가 표시되면 열린 창에서 직접 로그인하세요.'
    : queueHelp[status?.queue_reason ?? ''] ?? '공고별 보류 사유와 실행 기록을 확인하세요.';
  return <section className="auto-apply-panel" aria-labelledby="auto-apply-heading">
    <header className="auto-apply-head"><div><h2 id="auto-apply-heading"><Zap size={18} aria-hidden="true" /> 점수 기준 자동 지원</h2><p className="field-hint">Worklead를 켜 둔 동안 기준에 맞는 공고를 확인하고 당근에 저장된 기본 자기소개로 지원합니다.</p></div><StatusMark tone={hasError || status?.needs_user_action || status?.state === 'paused' ? 'warn' : working ? 'good' : 'muted'}>{q.isError ? '현재 자동 지원 상태 미확인' : processing ? '자동 지원 처리 중' : managed ? managedLabel : status?.needs_user_action ? '자동 지원 연결·상태 확인 필요' : status ? status.state === 'waiting' ? waitingLabel : stateLabels[status.state] : '자동 지원 상태 확인 중'}</StatusMark></header>
    <div className="auto-apply-columns"><div>
      <div className="auto-apply-fields"><label className="field-label">최소 점수<input aria-label="자동 지원 최소 점수" type="number" min={0} max={100} step={1} value={minScore} disabled={status?.enabled || busy} onChange={(e) => { fieldsEdited.current = true; setMinScore(e.target.value); }} /></label><label className="field-label">하루 제출 상한<input aria-label="자동 지원 하루 제출 상한" type="number" min={1} max={200} step={1} value={dailyLimit} disabled={status?.enabled || busy} onChange={(e) => { fieldsEdited.current = true; setDailyLimit(e.target.value); }} /></label></div>
      <p className="field-hint">{valid ? score > 0 ? `${score - 1}점은 제외하고 ${score}점부터 대상에 포함합니다.` : '0점부터 대상에 포함합니다.' : '점수는 0~100, 하루 상한은 1~200의 정수로 입력하세요.'} 점수 외에도 모집 상태·지원 이력·필수 질문을 확인합니다.</p>
      <p className="auto-apply-consent">시작하면 기준에 맞는 공고에 당근에 저장된 기본 자기소개로 개별 확인 없이 지원합니다. 확인 필요 공고도 포함하며, 추가 질문·새 약관·바뀐 양식은 보류합니다. Worklead의 편집 중인 초안은 전송하지 않습니다.</p>
      {managed ? <p className="field-hint" role="status">{status.browser_open ? '자동 지원 전용 당근 창이 열려 있습니다.' : status.enabled ? '자동 지원 전용 당근 창을 준비하고 있습니다.' : '자동 지원을 시작하면 저장된 연결로 전용 당근 창을 엽니다.'}</p> : null}
      <div className="draft-actions">
        <Button size="sm" variant="primary" icon={<Zap size={14} />} busy={setup.isPending} disabled={!status || q.isError || !valid || busy || mode === 'demo' || (managed && working)} onClick={() => { start.reset(); stop.reset(); forget.reset(); setup.mutate(); }}>{status?.enabled ? '자동 연결·지원 재개' : '설정 없이 자동 지원 시작'}</Button>
        {status?.enabled ? <Button size="sm" icon={<Square size={13} />} busy={stop.isPending} disabled={busy} onClick={() => { setup.reset(); start.reset(); forget.reset(); stop.mutate(); }}>자동 지원 중지</Button> : null}
        {status?.remembers_connection ? <Button size="sm" busy={forget.isPending} disabled={busy} onClick={() => { setup.reset(); start.reset(); stop.reset(); forget.mutate(); }}>저장 연결 해제</Button> : null}
      </div>
      {managed ? <p className="field-hint">{status.enabled ? '자동 지원이 등록되어 있습니다. 앱을 다시 켜면 저장된 로그인으로 재개합니다. 계정이나 기본 자기소개가 바뀌면 확인을 위해 멈춥니다.' : '자동 지원이 중지되어 있습니다. 시작을 누르면 저장된 로그인으로 연결합니다. 직접 중지한 상태는 다음 실행에도 유지합니다.'}</p> : <details className="auto-apply-advanced">
        <summary>크롬 확장 수동 연결</summary>
        <label className="auto-apply-remember"><input type="checkbox" checked={remember} disabled={status?.enabled || busy} onChange={(e) => setRemember(e.target.checked)} /> 다음 실행부터 자동 연결·지원 재개</label>
        <p className="field-hint">처음 한 번 확장 0.6.1에서 연결하면, 이후 앱·크롬을 다시 켰을 때 같은 계정의 전용 탭을 자동 복구합니다. 로그인 만료·계정 변경·사용자 중지 때는 멈춥니다.</p>
        {status?.state === 'paused' ? <p className="field-hint">처음 연결한 계정·자기소개를 확인한 뒤 이곳에서 재개하고, 확장이 일시 중지 상태면 팝업의 저장한 연결 재개를 누르세요. 사용할 계정이나 기본 소개를 바꾸려면 저장 연결 해제 후 처음 연결부터 진행하세요.</p> : null}
        <Button size="sm" icon={<Link size={14} />} busy={start.isPending} disabled={!status || q.isError || !valid || busy || mode === 'demo' || (status.enabled && status.state === 'connected' && !status.needs_user_action)} onClick={() => { setup.reset(); stop.reset(); forget.reset(); start.mutate(); }}>{status?.enabled ? status.paired ? '저장된 연결로 자동 지원 재개' : '자동 지원 연결 다시 준비' : '기준에 맞는 공고 자동 지원 시작'}</Button>
        {codeReady ? <div className="chrome-connection-code"><p className="field-hint">최초 한 번 로그인한 당근 탭의 Worklead 확장 팝업에서 <strong>자동 지원 연결</strong> 입력란에 붙여 넣고 ‘자동 지원 탭 연결’을 누르세요. 메시지 감시 연결코드와 별개입니다.</p><Button size="sm" icon={<Copy size={14} />} onClick={async () => { if (code.current) setCopied(await copyText(code.current) ? 'copied' : 'failed'); }}>자동 지원 연결코드 복사</Button><p className={copied === 'failed' ? 'field-error' : 'field-hint'} role="status">{copied === 'copied' ? '자동 지원 전용 코드를 복사했습니다.' : copied === 'failed' ? '복사하지 못했습니다. 다시 시도하세요.' : '실제 확장 연결 전에는 지원을 시작하지 않습니다.'}</p>{directory ? <p className="field-hint chrome-extension-path">확장 폴더: {directory}</p> : null}</div> : null}
        <p className="field-hint">{status?.remembers_connection ? '자동 연결을 기억합니다. 앱 종료 중에는 지원하지 않으며, 다시 켜면 저장한 연결로 복구합니다. 중지하면 다음 실행에도 중지를 유지합니다.' : '자동 연결 기억을 끄면 앱 재시작 후 새 코드로 연결해야 합니다.'}</p>
      </details>}
      <details className="auto-apply-how"><summary>작동 방식 자세히</summary>
      <p className="field-hint">새 공고부터 확인한 뒤 기존에 저장한 미지원 공고도 점검합니다. 오래된 원문은 본문·모집 상태와 지원 양식을 다시 확인합니다. 제출 허가 전의 명확한 연결·탭 오류만 1분·5분·15분 간격으로 최대 3회 다시 점검합니다. 추가 질문·조건 보류·기존 지원·결과 미확인 건은 자동 재제출하지 않습니다.</p>
      <p className="field-hint">Worklead가 전용 당근 창과 다음 실행의 자동 재개를 준비합니다. 확장 등록이나 연결코드 입력 없이 사용할 수 있습니다. 로그인이 필요하면 열린 당근 창에서 직접 로그인하세요.</p>
      <p className="field-hint">결과 미확인 건은 자동 재제출하지 않습니다.</p>
      <p className="field-hint">같은 연결에서 응답이 끊기면 완료 표시만 최대 3회 자동 대조합니다. 지원 버튼을 다시 누르지 않으며, 확인되지 않으면 결과 미확인을 유지합니다.</p>
      </details>
      {mode === 'demo' ? <p className="field-hint">데모에서는 자동 지원 연결·실제 제출을 실행하지 않습니다.</p> : null}
      {hasError ? <div role="alert"><p className="field-error">{forget.isError ? '저장 연결 해제 결과를 확인하지 못했습니다. 현재 연결 상태를 다시 확인하세요.' : setup.isError ? '자동 지원을 등록하지 못했습니다. 현재 상태를 확인한 뒤 다시 시도하세요.' : start.isError ? '자동 지원 연결을 준비하지 못했습니다.' : stop.isError ? '중지 결과를 확인하지 못했습니다. 현재 상태를 다시 확인한 뒤 중지를 재시도하세요.' : '현재 상태를 확인하지 못했습니다. 이전 기록을 정상 실행 상태로 보지 마세요.'}</p><Button size="sm" disabled={q.isFetching} onClick={() => void q.refetch()}>자동 지원 상태 다시 확인</Button></div> : null}
    </div><div className="auto-apply-results">
      {status?.queue_reason || status?.needs_user_action ? <div className={`auto-apply-queue${status.needs_user_action ? ' needs-action' : ''}`} role={status.needs_user_action && !q.isError ? 'alert' : 'status'}><strong>{q.isError ? '지원 대기 상태 미확인' : queueLabel}</strong>{status.needs_user_action && !q.isError ? <p className="field-hint">자동 지원이 정상 진행 중인 상태로 보지 마세요. {help}</p> : null}</div> : null}
      <p className="field-hint">{q.isError ? '마지막으로 확인한 상태' : status?.message ?? '아직 연결 상태를 확인하지 못했습니다.'}</p>
      {status ? <dl className="auto-apply-metrics"><div><dt>오늘 제출 확인</dt><dd>{status.today_submitted} / {status.daily_limit}</dd></div><div><dt>처리 중·예약</dt><dd>{status.today_reserved}</dd></div><div><dt>보류</dt><dd>{status.held}</dd></div><div><dt>결과 미확인</dt><dd>{status.uncertain}</dd></div>{status.retry_waiting !== undefined ? <div><dt>자동 재시도 대기</dt><dd>{status.retry_waiting}건</dd></div> : null}</dl> : null}
      {status?.last_connected_at ? <p className="field-hint">최근 {managed ? '자동 지원 창' : '크롬'} 응답: {formatDateTime(status.last_connected_at, now)}</p> : <p className="field-hint">{managed ? '자동 지원 창의 첫 작업 응답을 기다립니다.' : '크롬에서 받은 작업 요청이 아직 없습니다.'}</p>}
      {status?.candidates ? <section aria-label="공고별 진행 상태"><h3>공고별 진행 상태</h3><p className="field-hint">최근 후보와 진행 중인 공고를 최대 30건 표시합니다. 전체 공고는 리드 목록에서 확인하세요.</p>{status.candidates.length ? <ol className="auto-apply-attempts auto-apply-candidates">{status.candidates.map(candidate => <li key={candidate.lead_id}><div><Button size="sm" variant="ghost" onClick={() => onOpenLead(candidate.lead_id)}>{candidate.title}</Button><StatusMark tone={candidate.stage === 'submitted' || candidate.stage === 'already_applied' ? 'good' : ['held', 'uncertain', 'retry_wait', 'rejected'].includes(candidate.stage) ? 'warn' : 'muted'}>{candidateLabels[candidate.stage] ?? '지원 단계 확인 필요'}</StatusMark></div>{candidate.reason_code ? <p className="field-hint">{reasonLabels[candidate.reason_code] ?? '지원 조건 또는 처리 상태 확인 필요'}</p> : null}{candidate.refresh_state && !candidate.reason_code?.startsWith('refresh_') ? <p className="field-hint">원문 작업: {refreshLabels[candidate.refresh_state] ?? '상태 확인 필요'}</p> : null}{candidate.refresh_error_code ? <p className="field-hint">원문 수집 오류: {reasonLabels[candidate.refresh_error_code] ?? '실행 기록에서 원인 확인 필요'}</p> : null}{candidate.last_checked_at ? <p className="field-hint">마지막 원문 확인: <time dateTime={candidate.last_checked_at}>{formatDateTime(candidate.last_checked_at, now)}</time></p> : null}<RetryInfo count={candidate.retry_count} after={candidate.retry_after} now={now} />{candidate.stage === 'held' ? <p className="field-hint">이 공고는 자동 재시도하지 않습니다.</p> : null}</li>)}</ol> : <p className="field-hint">표시할 후보 진단이 없습니다. 탐색 상태와 기준 점수를 확인하세요.</p>}</section> : null}
      <h3>최근 처리 기록</h3>
      {!status?.attempts.length ? <p className="field-hint">처리 기록이 없습니다. 연결 대기는 지원 완료가 아닙니다.</p> : <ol className="auto-apply-attempts">{status.attempts.map((attempt) => <li key={attempt.id}><div><Button size="sm" variant="ghost" onClick={() => onOpenLead(attempt.lead_id)}>{attempt.title}</Button><StatusMark tone={attempt.state === 'submitted' || attempt.state === 'already_applied' ? 'good' : attempt.state === 'uncertain' || attempt.state === 'held' ? 'warn' : 'muted'}>{attempt.retry_after ? candidateLabels.retry_wait : attemptLabels[attempt.state]}</StatusMark></div>{attempt.reason_code && !['ready', 'submitted', 'already_applied'].includes(attempt.reason_code) ? <p className="field-hint">{reasonLabels[attempt.reason_code] ?? '지원 조건 또는 처리 결과 확인 필요'}</p> : null}<RetryInfo count={attempt.retry_count} after={attempt.retry_after} now={now} /><p className="field-hint">{formatDateTime(attempt.finished_at ?? attempt.created_at, now)}</p></li>)}</ol>}
    </div></div>
  </section>;
}
