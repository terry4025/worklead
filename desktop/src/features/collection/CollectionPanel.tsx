import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pause, Play, Search, Square, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { formatCount, formatDateTime, formatRelative } from '../../domain/format';
import { healthLabel, policyLabel, runKindLabel, runStateLabel, supportLabel } from '../../domain/labels';
import type { ManualImportInput, RunInfo, RunKind, SourceInfo } from '../../domain/model';
import { SourceMonitoring } from './SourceMonitoring';
import type { Tone } from '../../domain/present';
import { usePort, usePortCtx } from '../../app/PortContext';
import { qk, useRuns, useRunStatus, useSources } from '../../app/queries';
import { getDatabaseEpoch } from '../../app/databaseCache';
import { toPortError } from '../../data/port';
import { Button } from '../../ui/Button';
import { Dialog } from '../../ui/Dialog';
import { EmptyState, ErrorState, errorMessage } from '../../ui/States';
import { StatusMark } from '../../ui/StatusMark';
import { useToast } from '../../ui/Toasts';
import { openExternal, safeExternalUrl } from '../../platform/external';

type Tab = 'sources' | 'runs' | 'import' | 'export';

const HEALTH_TONE: Record<SourceInfo['health']['status'], Tone> = { ok: 'good', degraded: 'warn', blocked: 'bad', error: 'bad', paused: 'muted', unknown: 'unknown' };
const RUN_TONE: Record<RunInfo['state'], Tone> = { queued: 'unknown', running: 'good', paused: 'muted', succeeded: 'good', partial: 'warn', failed: 'bad', cancelled: 'muted' };
const ACTIVE_RUN_STATES: RunInfo['state'][] = ['queued', 'running', 'paused'];
const TERMINAL_RUN_STATES: RunInfo['state'][] = ['succeeded', 'partial', 'failed', 'cancelled'];
type RunScope = 'collection' | 'export' | 'all';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function countValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function textValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function exportFileName(run: RunInfo): string | null {
  const name = isRecord(run.result) ? textValue(run.result.file_name) : null;
  return name && name.length <= 255 && !/[<>:"/\\|?*\u0000-\u001f]/.test(name) ? name : null;
}

function ExportFile({ run }: { run: RunInfo }) {
  if (run.kind !== 'export' || !['succeeded', 'partial'].includes(run.state)) return null;
  const name = exportFileName(run);
  const rows = isRecord(run.result) ? countValue(run.result.rows) : null;
  return (
    <>
      <p className={`field-hint${name ? '' : ' tone-warn'}`} style={{ overflowWrap: 'anywhere' }}>
        {name ? `저장됨: ${name}` : '완료 기록에 저장 파일명이 없습니다. 실행 기록을 다시 확인하세요.'}
      </p>
      {rows !== null ? <p className="field-hint num">내보낸 리드 {formatCount(rows)}건</p> : null}
    </>
  );
}

function RunDetails({ run }: { run: RunInfo }) {
  const toast = useToast();
  const result = isRecord(run.result) ? run.result : {};
  const discovery = run.kind === 'discovery' && isRecord(result.discovery) ? result.discovery : null;
  const titleDiscovery = run.sourceId === 'daangn-alba' ? discovery : null;
  const discoveryCounts = ([
    ['core_candidates', '핵심 후보', '개'],
    ['exploration_candidates', '추가 확인 후보', '개'],
    ['exploration_requests', '추가 상세 요청', '회'],
    ['exploration_deferred', '다음 탐색 보류', '개'],
    ['watch_detail_requests', '이번 감시 상세 요청', '회'],
    ['watch_refresh_requests', '기존 상세 갱신 요청', '회'],
    ['watch_deferred_details', '다음 감시로 넘긴 후보', '개'],
  ] as const).flatMap(([key, label, unit]) => {
    const count = countValue(titleDiscovery?.[key]);
    return count === null ? [] : [`${label} ${formatCount(count)}${unit}`];
  });
  const explorationSettings = ([
    ['exploration_limit', '이번 실행 추가 요청 한도'],
    ['exploration_reserve', '다른 작업에 남길 요청 예산'],
    ['detail_requests_per_watch', '신규 목록 감시당 상세 요청'],
  ] as const).flatMap(([key, label]) => {
    const count = countValue(titleDiscovery?.[key]);
    return count === null ? [] : [`${label} ${formatCount(count)}회`];
  });
  const created = countValue(run.counts.created);
  const excluded = countValue(run.counts.excluded);
  const reviewCandidates = created !== null && excluded !== null && excluded <= created ? created - excluded : null;
  const progress = isRecord(result.detail_progress) ? result.detail_progress : null;
  const done = countValue(progress?.done);
  const total = countValue(progress?.total);
  const hasProgress = done !== null && total !== null && total > 0;
  const deferred = countValue(result.deferred_details);
  const issues = Array.isArray(result.detail_issues)
    ? result.detail_issues.filter((item): item is Record<string, unknown> => isRecord(item) && !!textValue(item.code) && !!textValue(item.message)).slice(0, 50)
    : [];
  const openOriginal = async (url: string) => {
    const opened = await openExternal(url);
    if (opened === 'copied') toast.show({ tone: 'info', text: '원문 주소를 복사했습니다. 브라우저에 붙여 넣으세요.' });
    if (opened === 'rejected') toast.show({ tone: 'bad', text: '원문 주소를 열지 못했습니다.' });
  };
  return (
    <>
      {run.kind === 'discovery' && reviewCandidates !== null ? (
        <div aria-label="신규 저장 분류">
          <p className="run-counts num">신규 저장 {formatCount(created!)}건 · 자동 제외 {formatCount(excluded!)}건 · 검토할 후보 {formatCount(reviewCandidates)}건</p>
          <p className="field-hint">검토할 후보는 자동 제외되지 않은 신규 공고입니다. 건당 지급·원격 가능·모집 여부는 원문에서 확인하세요.</p>
        </div>
      ) : null}
      {discoveryCounts.length || explorationSettings.length ? (
        <div aria-label="제목 선별과 추가 확인 범위">
          {discoveryCounts.length ? <p className="field-hint num">{discoveryCounts.join(' · ')}</p> : null}
          {explorationSettings.length ? <p className="field-hint num">{explorationSettings.join(' · ')}</p> : null}
          <p className="field-hint">제목 선별과 일부 후보의 본문 확인을 사용하며, 전체 본문 검색은 아닙니다. 공개 목록에 없는 글이나 의뢰 표현이 본문에만 있는 글은 누락될 수 있습니다. 후보 수는 적합한 의뢰 수와 다릅니다.</p>
          <p className="field-hint">추가 상세 요청 수는 정책 확인·주소 이동 요청도 포함합니다. 보류 후보의 다음 확인 여부는 탐색 예산과 원문 상태에 따라 달라집니다.</p>
        </div>
      ) : null}
      {run.sourceId === 'iboss' && discovery ? <BoardDiscoveryDetails discovery={discovery} state={run.state} continuationPending={run.state === 'partial' && !run.error && run.counts.fetchFailures === 0 && run.counts.parseFailures === 0 && run.counts.policyStops === 0 && result.continuation_pending === true} /> : null}
      {hasProgress ? (
        <>
          <p className="field-hint num">상세 후보 진행 {formatCount(Math.min(done, total))}/{formatCount(total)}{textValue(progress?.title) ? ` · ${textValue(progress?.title)}` : ''}</p>
          <div className="run-progress" aria-label={`상세 후보 진행 ${Math.min(done, total)}/${total}`}><span style={{ width: `${Math.min(100, (done / total) * 100)}%` }} /></div>
        </>
      ) : run.progress.label ? <p className="field-hint">{run.progress.label}</p> : null}
      {deferred ? <p className="field-hint num">재조회 유예 {formatCount(deferred)}건 · 재시도 예정 시각 전까지 상세 요청을 생략합니다.</p> : null}
      {issues.length ? (
        <details className="policy">
          <summary>공고별 오류·수집 제외 {formatCount(issues.length)}건</summary>
          <ul className="cap-list">
            {issues.map((issue, index) => {
              const url = safeExternalUrl(textValue(issue.url));
              const retryAt = textValue(issue.retry_at);
              const validRetryAt = retryAt && Number.isFinite(Date.parse(retryAt)) ? retryAt : null;
              const status = countValue(issue.http_status);
              return (
                <li key={`${textValue(issue.source_post_id) ?? url ?? 'detail'}-${index}`}>
                  <strong>{textValue(issue.title) ?? textValue(issue.source_post_id) ?? `공고 ${index + 1}`}</strong>
                  <p className="field-hint">{textValue(issue.message)}{status !== null && status >= 100 && status <= 599 ? ` (HTTP ${status})` : ''} · {textValue(issue.code)}</p>
                  {validRetryAt ? <p className="field-hint">{formatDateTime(validRetryAt)} 이후 재시도</p> : null}
                  {url ? <button type="button" className="link-btn" title={url} onClick={() => void openOriginal(url)}>원문 열기</button> : null}
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
    </>
  );
}

function BoardDiscoveryDetails({ discovery, state, continuationPending }: { discovery: Record<string, unknown>; state: RunInfo['state']; continuationPending: boolean }) {
  const progress = ([
    ['page', '현재 목록', '페이지'],
    ['listed_posts', '현재 목록 공고', '건'],
    ['pages_completed', '완료한 목록', '페이지'],
    ['posts_considered', '이번 실행 확인 대상', '건'],
    ['batch_request_limit', '이번 실행 요청 한도', '회'],
  ] as const).flatMap(([key, label, unit]) => {
    const count = countValue(discovery[key]);
    return count === null ? [] : [`${label} ${formatCount(count)}${unit}`];
  });
  const next = countValue(discovery.next_page);
  return (
    <div aria-label="공개 대행의뢰 목록 확인 범위">
      {progress.length ? <p className="field-hint num">{progress.join(' · ')}</p> : null}
      {discovery.resumed === true ? <p className="field-hint">{state === 'running' ? '저장된 목록 페이지에서 이어서 확인 중입니다.' : '저장된 목록 페이지에서 이어서 확인한 실행입니다.'}</p> : null}
      {state === 'running' && discovery.has_next === true ? <p className="field-hint">현재 목록을 확인한 뒤 다음 페이지로 계속 진행합니다.</p> : null}
      {continuationPending ? <p className="field-hint">이번 실행의 확인을 마쳤습니다. 남은 공개 목록은 다음 실행에서 이어서 확인합니다.</p> : null}
      {discovery.has_next === true && next !== null && next > 0 ? <p className="field-hint num">다음 목록 {formatCount(next)}페이지</p> : null}
      <p className="field-hint">목록 공고 수는 적합한 의뢰 수와 다릅니다. 상세 확인·기존 중복·제외 결과를 함께 확인하세요.</p>
    </div>
  );
}

export function CollectionPanel({ open, onClose, initialTab = 'sources', onOpenLead }: { open: boolean; onClose: () => void; initialTab?: Tab; onOpenLead: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [runScope, setRunScope] = useState<RunScope>('collection');
  const exportTask = useExportTask(open && tab === 'export');
  useEffect(() => {
    if (open) setTab(initialTab);
  }, [open, initialTab]);
  const tabs: { id: Tab; label: string }[] = [
    { id: 'sources', label: '소스·범위' },
    { id: 'runs', label: '실행 기록' },
    { id: 'import', label: '수동 입력' },
    { id: 'export', label: '내보내기' },
  ];
  return (
    <Dialog open={open} onClose={onClose} title="수집" variant="drawer" width="min(760px, 96vw)" description="자동 연락·지원은 하지 않습니다. 허용되지 않은 소스는 실행하지 않고, 차단되면 우회하지 않고 멈춥니다.">
      <div className="subtabs" role="tablist" aria-label="수집 메뉴">
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`subtab${tab === t.id ? ' is-active' : ''}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'sources' ? <SourcesTab /> : null}
      {tab === 'runs' ? <RunsTab scope={runScope} onScopeChange={setRunScope} /> : null}
      {tab === 'import' ? <ImportTab onOpenLead={onOpenLead} /> : null}
      {tab === 'export' ? <ExportTab task={exportTask} onOpenRuns={() => { setRunScope('export'); setTab('runs'); }} /> : null}
    </Dialog>
  );
}

function SourcesTab() {
  const q = useSources();
  const runs = useRunStatus();
  const hasActiveRun = runs.data?.some((run) => ACTIVE_RUN_STATES.includes(run.state)) ?? false;
  const { refetch } = runs;
  useEffect(() => {
    if (!hasActiveRun) return;
    // SSE progress updates can keep resetting QueryObserver's refetch timer.
    // Keep this timer stable so full counts/result refresh during a running job.
    const timer = window.setInterval(() => { void refetch({ cancelRefetch: false }); }, 3000);
    return () => window.clearInterval(timer);
  }, [hasActiveRun, refetch]);
  if (q.isPending) return <p className="muted-text">불러오는 중…</p>;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return (
    <div className="source-list">
      {q.data.map((s) => (
        <SourceCard key={s.id} source={s} runs={runs.data ?? []} />
      ))}
      <ManualSourceLinks />
    </div>
  );
}

function ManualSourceLinks() {
  const toast = useToast();
  const openSite = async (url: string) => {
    const result = await openExternal(url);
    if (result === 'copied') toast.show({ tone: 'info', text: '사이트 주소를 복사했습니다. 브라우저에 붙여 넣으세요.' });
    if (result === 'rejected') toast.show({ tone: 'bad', text: '사이트 주소를 열지 못했습니다.' });
  };
  return (
    <section className="source-card" aria-labelledby="manual-source-links">
      <header className="source-head"><h3 id="manual-source-links">다른 외주 사이트에서 직접 찾기</h3></header>
      <p className="field-hint">자동 수집 미연결 · 링크를 누르면 브라우저에서 직접 확인합니다.</p>
      <div className="source-head">
        {([
          ['위시켓', 'https://www.wishket.com/project/'],
          ['프리모아', 'https://www.freemoa.net/m4/s41'],
          ['이랜서 턴키', 'https://www.elancer.co.kr/list-partner?pf=%ED%84%B4%ED%82%A4'],
        ] as const).map(([name, url]) => (
          <a key={url} className="link-btn" href={url} onClick={(event) => { event.preventDefault(); void openSite(url); }}>{name} 공고 열기</a>
        ))}
      </div>
      <p className="field-hint">원격 목록에도 월급 공고가 있으므로 건당·프로젝트 지급 여부는 원문에서 확인하세요.</p>
    </section>
  );
}

function SourceCard({ source: s, runs }: { source: SourceInfo; runs: RunInfo[] }) {
  const port = usePort();
  const qc = useQueryClient();
  const toast = useToast();
  const [policyOpen, setPolicyOpen] = useState(false);
  const [pStatus, setPStatus] = useState<'permission_pending' | 'allowed' | 'restricted' | 'blocked'>(s.policy.status === 'not_required' ? 'permission_pending' : s.policy.status);
  const [pBasis, setPBasis] = useState(s.policy.basis ?? '');
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.sources });
    void qc.invalidateQueries({ queryKey: qk.runs });
  };
  const update = useMutation({
    mutationFn: (patch: Parameters<typeof port.updateSource>[1]) => port.updateSource(s.id, patch),
    onSuccess: refresh,
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }, 7000),
  });
  const start = useMutation({
    mutationFn: (kind: 'discovery' | 'recheck_recent') => port.startRun({ sourceId: s.id, kind }),
    onSuccess: () => {
      refresh();
      toast.show({ tone: 'info', text: '실행을 요청했습니다. 진행 상황은 위쪽 막대와 실행 기록에 표시됩니다.' });
    },
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }, 7000),
  });
  const allowed = s.policy.status === 'allowed' || s.policy.status === 'restricted';
  const blockedReason = !allowed
    ? `자동 수집 권한이 확인되지 않았습니다 (${policyLabel[s.policy.status]})`
    : !s.research.ready
      ? '사이트 조사가 끝나지 않았습니다'
      : s.stoppedReason
        ? '정지된 소스입니다'
        : null;
  const active = runs.find((r) => r.sourceId === s.id && ['queued', 'running', 'paused'].includes(r.state));
  const lastDiscovery = runs.find((r) => r.sourceId === s.id && r.kind === 'discovery' && r.finishedAt);
  const discovered = isRecord(lastDiscovery?.result) && isRecord(lastDiscovery.result.discovery) ? lastDiscovery.result.discovery : null;
  const isDaangn = s.id === 'daangn-alba';
  const isBoard = s.id === 'iboss';
  const discoveryLabel = isDaangn ? '전국 탐색' : '공개 목록 탐색';
  const c = s.coverage;

  return (
    <article className="source-card" aria-labelledby={`src-${s.id}`}>
      <header className="source-head">
        <h3 id={`src-${s.id}`}>{s.name}</h3>
        <StatusMark tone={HEALTH_TONE[s.health.status]}>{healthLabel[s.health.status]}</StatusMark>
        {s.kind === 'site' ? <StatusMark tone={allowed ? 'good' : s.policy.status === 'blocked' ? 'bad' : 'unknown'}>{policyLabel[s.policy.status]}</StatusMark> : null}
      </header>
      {s.scopeNote ? <p className="muted-text">{s.scopeNote}</p> : null}
      {isBoard ? <p className="field-hint">대행의뢰 전체 분류의 활성 공고(마감 제외)를 제목으로 거르지 않고 공개 원문에서 확인합니다. AI 없이 규칙으로 건당·프로젝트 개발·제작 의뢰를 분류하며, 지급 방식·재택 가능 여부는 원문 근거로 확인합니다.</p> : null}
      {s.health.message ? <p className={`inline-note tone-${HEALTH_TONE[s.health.status]}`}>{s.health.message}</p> : null}

      {s.kind === 'site' ? (
        <>
          <SourceMonitoring source={s} />
          {!s.research.ready ? (
            <div className="inline-alert inline-alert-info">
              <div>
                <strong>사이트 조사 미완료</strong> — 확인 전에는 선택자·지역 ID·숨은 API 를 추측해 실행하지 않습니다.
                <ul className="missing">
                  {s.research.missing.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
                <span className="field-hint">조사 기록: docs/SOURCE_RESEARCH.md</span>
              </div>
            </div>
          ) : null}

          {c ? (
            <div className="coverage">
              <div className="cov-row">
                <span className="cov-label">탐색 목표</span>
                <span>
                  {c.targetLabel}
                  {isDaangn && c.targetUnitsTotal ? ` (${c.targetUnitsTotal}개 시·도)` : ''}
                </span>
              </div>
              <div className="cov-row">
                <span className="cov-label">{isDaangn ? '지역 목록' : '목록 방식'}</span>
                <span>
                  {isDaangn && c.targetUnitsCovered !== null ? `${c.targetUnitsCovered}/${c.targetUnitsTotal ?? '?'} 시·도 확보 · ` : ''}
                  {{ verified: '전체성 확인됨', unverified: '전체성 미확인 → 부분 탐색으로 표시', not_applicable: isDaangn ? '지역 구분 없이 전국 공개 목록 탐색' : '공개 목록 페이지 순환', unknown: '미확인' }[c.regionListStatus]}
                </span>
              </div>
              {isBoard && c.depthLimit === 0 ? <div className="cov-row"><span className="cov-label">탐색 깊이</span><span>공개 목록 끝까지 · 실행이 나뉘면 저장된 페이지에서 이어갑니다.</span></div> : null}
              <div className="cov-row">
                <span className="cov-label">이번 주기{c.scanCycle ? ` #${c.scanCycle}` : ''}</span>
                {c.planned === null ? (
                  <span className="muted-text">계획 없음</span>
                ) : (
                  <span className="num">
                    계획 {c.planned} · 완료 {c.completed} · 대기 {c.pending} · 실패 {c.failed} · 차단 {c.blocked}
                    <span className="cov-bar" aria-hidden="true">
                      <span className="seg-done" style={{ width: `${(c.completed / Math.max(1, c.planned)) * 100}%` }} />
                      <span className="seg-fail" style={{ width: `${((c.failed + c.blocked) / Math.max(1, c.planned)) * 100}%` }} />
                    </span>
                  </span>
                )}
              </div>
              <div className="cov-row">
                <span className="cov-label">시장 포괄률</span>
                <span>알 수 없음 <span className="field-hint">(원천 전체 공고 수를 모름 — 작업 완료율과 다름)</span></span>
              </div>
              {c.budget ? (
                <div className="cov-row">
                  <span className="cov-label">앱 요청 예산</span>
                  <span className="num">
                    오늘 {formatCount(c.budget.used)}/{c.budget.limit ?? '∞'} {c.budget.unitLabel}
                    {c.estimatedCycleDays ? ` · ${isDaangn ? '전국' : '공개 목록'} 한 바퀴 약 ${c.estimatedCycleDays}일 (추정)` : ''}
                  </span>
                </div>
              ) : null}
              {c.nextUp || c.lastVisitedAt ? (
                <div className="cov-row">
                  <span className="cov-label">순환</span>
                  <span>
                    {c.lastVisitedAt ? `마지막 방문 ${formatRelative(c.lastVisitedAt)}` : ''}
                    {c.nextUp ? ` · 다음 차례 ${c.nextUp}` : ''}
                  </span>
                </div>
              ) : null}
              {c.notes.length ? (
                <ul className="cov-notes">
                  {c.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {lastDiscovery ? (
            <div className="coverage" aria-label={`최근 ${discoveryLabel} 결과`}>
              <div className="cov-row"><span className="cov-label">최근 탐색</span><span><StatusMark tone={RUN_TONE[lastDiscovery.state]}>{runStateLabel[lastDiscovery.state]}</StatusMark> · {lastDiscovery.finishedAt ? formatDateTime(lastDiscovery.finishedAt) : ''}</span></div>
              <p className="run-counts num">신규 {lastDiscovery.counts.created} · 기존 중복 {lastDiscovery.counts.duplicates} · 요청 실패 {lastDiscovery.counts.fetchFailures} · 해석 실패 {lastDiscovery.counts.parseFailures}</p>
              {isDaangn && countValue(discovered?.listed_urls) !== null && countValue(discovered?.title_candidates) !== null ? <p className="field-hint">공개 목록 {formatCount(discovered!.listed_urls as number)}개 주소에서 관련 제목 {formatCount(discovered!.title_candidates as number)}개 확인</p> : null}
              <RunDetails run={lastDiscovery} />
              {lastDiscovery.error ? <p className="field-hint">{lastDiscovery.error.message}</p> : null}
            </div>
          ) : null}

          {active?.kind === 'discovery' ? (
            <div className="coverage" aria-label={`진행 중 ${discoveryLabel}`}>
              <div className="cov-row"><span className="cov-label">진행 중 탐색</span><StatusMark tone={RUN_TONE[active.state]}>{runStateLabel[active.state]}</StatusMark></div>
              <RunDetails run={active} />
            </div>
          ) : null}

          <div className="source-controls">
            <Button size="sm" variant="primary" icon={<Search size={14} />} disabled={!!blockedReason || !!active} busy={start.isPending} title={blockedReason ?? (active ? '진행 중인 실행이 있습니다' : `지금 ${discoveryLabel} 한 번 실행`)} onClick={() => start.mutate('discovery')}>
              지금 탐색
            </Button>
            <Button size="sm" disabled={!!blockedReason} busy={start.isPending} title={blockedReason ?? '최근 후보의 모집 상태 재확인'} onClick={() => start.mutate('recheck_recent')}>
              최근 후보 재확인
            </Button>
            <label className="toggle" title={blockedReason ?? undefined}>
              <input
                type="checkbox"
                checked={s.autoCollect.enabled}
                disabled={(!!blockedReason && !s.autoCollect.enabled) || update.isPending}
                onChange={(e) => update.mutate({ autoCollectEnabled: e.target.checked })}
              />
              <span>자동 수집 (앱 실행 중에만)</span>
            </label>
            <label className="select">
              <span className="sr-only">수집 주기</span>
              <select
                aria-label="수집 주기"
                value={s.autoCollect.intervalMinutes ?? 360}
                disabled={update.isPending}
                onChange={(e) => update.mutate({ intervalMinutes: Number(e.target.value) })}
              >
                {[30, 60, 180, 360, 720, 1440].map((m) => (
                  <option key={m} value={m}>
                    {m < 60 ? `${m}분마다` : `${m / 60}시간마다`}
                  </option>
                ))}
              </select>
            </label>
            {s.stoppedReason ? (
              <Button size="sm" variant="danger" onClick={() => update.mutate({ clearStop: true })} title="원인을 확인한 뒤에만 해제하세요. 차단을 우회하지 않습니다.">
                정지 해제
              </Button>
            ) : null}
          </div>
          {blockedReason ? <p className="field-hint">실행할 수 없음: {blockedReason}</p> : null}

          <details className="policy" open={policyOpen} onToggle={(e) => setPolicyOpen((e.target as HTMLDetailsElement).open)}>
            <summary>수집 정책 검토 기록</summary>
            <p className="field-hint">공개 열람 가능성은 자동 수집 허용과 같지 않습니다. 약관·운영정책·robots.txt 검토 근거를 남기세요. robots.txt 는 실행 중에도 계속 지킵니다.</p>
            {s.policy.reviewedAt ? (
              <p className="muted-text">
                현재: {policyLabel[s.policy.status]} · {formatDateTime(s.policy.reviewedAt)} · 근거: {s.policy.basis ?? '없음'}
              </p>
            ) : null}
            <form
              className="policy-form"
              onSubmit={(e) => {
                e.preventDefault();
                update.mutate({ policy: { status: pStatus, basis: pBasis.trim() || null, note: null } });
              }}
            >
              <label className="select">
                <span className="sr-only">정책 상태</span>
                <select value={pStatus} onChange={(e) => setPStatus(e.target.value as typeof pStatus)} aria-label="정책 상태">
                  <option value="permission_pending">권한 확인 대기</option>
                  <option value="allowed">자동 수집 허용</option>
                  <option value="restricted">제한적 허용</option>
                  <option value="blocked">자동 수집 불가</option>
                </select>
              </label>
              <textarea aria-label="판단 근거" rows={2} placeholder="근거 (약관 조항, 허가 문서, 확인 날짜 등) — 허용·제한에는 필수" value={pBasis} onChange={(e) => setPBasis(e.target.value)} />
              <Button type="submit" size="sm" busy={update.isPending} disabled={(pStatus === 'allowed' || pStatus === 'restricted') && !pBasis.trim()}>
                기록
              </Button>
            </form>
          </details>

          <details className="caps">
            <summary>기능 선언 ({s.capabilities.filter((x) => x.support === 'supported').length}개 지원 · {s.capabilities.filter((x) => x.support === 'unverified').length}개 미확인)</summary>
            <ul className="cap-list">
              {s.capabilities.map((cap) => (
                <li key={cap.key}>
                  <StatusMark tone={cap.support === 'supported' ? 'good' : cap.support === 'unsupported' ? 'muted' : 'unknown'}>
                    {cap.label} · {supportLabel[cap.support]}
                  </StatusMark>
                  {cap.note ? <span className="field-hint"> {cap.note}</span> : null}
                </li>
              ))}
            </ul>
          </details>
        </>
      ) : (
        <ul className="cap-list">
          {s.capabilities.map((cap) => (
            <li key={cap.key}>
              <StatusMark tone={cap.support === 'supported' ? 'good' : 'muted'}>
                {cap.label} · {supportLabel[cap.support]}
              </StatusMark>
              {cap.note ? <span className="field-hint"> {cap.note}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

function RunsTab({ scope, onScopeChange }: { scope: RunScope; onScopeChange: (scope: RunScope) => void }) {
  const kinds: RunKind[] | undefined = scope === 'collection' ? ['discovery', 'recheck_recent', 'recheck_stale'] : scope === 'export' ? ['export'] : undefined;
  const q = useRuns(kinds);
  const sources = useSources();
  const port = usePort();
  const qc = useQueryClient();
  const toast = useToast();
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'cancel' }) => port.runAction(id, action),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.runs }),
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }),
  });
  return (
    <>
    <label className="select">기록 종류 <select aria-label="실행 기록 종류" value={scope} onChange={(e) => onScopeChange(e.target.value as RunScope)}><option value="collection">사이트 탐색·자동 재확인</option><option value="export">내보내기</option><option value="all">전체 (개별 확인·분석 포함)</option></select></label>
    <p className="field-hint">선택한 종류의 최근 20개 실행입니다.</p>
    {q.isPending ? <p className="muted-text">불러오는 중…</p> : null}
    {q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
    {q.data?.length === 0 ? <EmptyState title="실행 기록이 없습니다">선택한 종류의 실행이 아직 없습니다.</EmptyState> : null}
    <ul className="run-list">
      {(q.data ?? []).map((r) => (
        <li key={r.id} className="run-item">
          <div className="run-line">
            <StatusMark tone={RUN_TONE[r.state]}>{runStateLabel[r.state]}</StatusMark>
            <span className="run-kind">{runKindLabel[r.kind]}</span>
            {r.sourceId ? <span className="muted-text">{sources.data?.find((source) => source.id === r.sourceId)?.name ?? r.sourceId}</span> : null}
            <span className="muted-text">{r.trigger === 'schedule' ? '예약' : '수동'}</span>
            <span className="muted-text num">{r.startedAt ? formatDateTime(r.startedAt) : '대기'}</span>
            <span className="run-actions">
              {r.state === 'running' || r.state === 'queued' ? (
                <Button size="sm" variant="ghost" icon={<Pause size={13} />} onClick={() => act.mutate({ id: r.id, action: 'pause' })}>
                  일시정지
                </Button>
              ) : null}
              {r.state === 'paused' ? (
                <Button size="sm" variant="ghost" icon={<Play size={13} />} onClick={() => act.mutate({ id: r.id, action: 'resume' })}>
                  재개
                </Button>
              ) : null}
              {['running', 'queued', 'paused'].includes(r.state) ? (
                <Button size="sm" variant="ghost" icon={<Square size={13} />} onClick={() => act.mutate({ id: r.id, action: 'cancel' })}>
                  취소
                </Button>
              ) : null}
            </span>
          </div>
          {r.progress.total ? (
            <div className="run-progress" aria-label={`진행 ${r.progress.done}/${r.progress.total}`}>
              <span style={{ width: `${Math.min(100, (r.progress.done / r.progress.total) * 100)}%` }} />
            </div>
          ) : null}
          <p className="run-counts num">
            요청 {r.counts.requests} · 상세 {r.counts.detailsFetched} · 신규 {r.counts.created} · 수정 {r.counts.updated} · 중복 {r.counts.duplicates} · 제외 {r.counts.excluded}
            {r.counts.skipped ? <span> · 본문 수집 제외 {r.counts.skipped}</span> : null}
            {r.counts.parseFailures ? <span className="tone-warn"> · 파싱 실패 {r.counts.parseFailures}</span> : null}
            {r.counts.fetchFailures ? <span className="tone-warn"> · 요청 실패 {r.counts.fetchFailures}</span> : null}
            {r.counts.policyStops ? <span className="tone-bad"> · 정책 정지 {r.counts.policyStops}</span> : null}
            {r.counts.aiFailures ? <span className="tone-warn"> · AI 실패 {r.counts.aiFailures}</span> : null}
          </p>
          <RunDetails run={r} />
          <ExportFile run={r} />
          {r.error ? (
            <p className="run-error">
              {r.error.code}: {r.error.message}
              {r.error.retryAfter ? ` · ${formatDateTime(r.error.retryAfter)} 이후 재시도` : ''}
            </p>
          ) : null}
          {r.note ? <p className="field-hint">{r.note}</p> : null}
        </li>
      ))}
    </ul>
    </>
  );
}

const MAX_BYTES = 2 * 1024 * 1024;

function ImportTab({ onOpenLead }: { onOpenLead: (id: string) => void }) {
  const port = usePort();
  const { mode } = usePortCtx();
  const toast = useToast();
  const qc = useQueryClient();
  const [format, setFormat] = useState<ManualImportInput['format']>('text');
  const [content, setContent] = useState('');
  const [url, setUrl] = useState('');
  const [fileName, setFileName] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useQuery({
    queryKey: ['import-run', jobId],
    queryFn: () => port.getRun(jobId!),
    enabled: !!jobId,
    refetchInterval: (q) => q.state.data && ['succeeded', 'failed', 'partial', 'cancelled'].includes(q.state.data.state) ? false : 1000,
  });
  const jobRun = job.data;
  const jobActive = !!jobId && (!jobRun || ['queued', 'running', 'paused'].includes(jobRun.state));
  useEffect(() => {
    if (jobRun && !jobActive) {
      void qc.invalidateQueries({ queryKey: ['counts'] });
      void qc.invalidateQueries({ queryKey: qk.runs });
    }
  }, [jobRun, jobActive, qc]);
  const fileRef = useRef<HTMLInputElement>(null);
  const size = new Blob([content]).size;

  const submit = useMutation({
    mutationFn: () => port.importManual({ format, content, fileName, originalUrl: url.trim() || null }),
    onSuccess: (accepted) => {
      toast.show({ tone: 'info', text: '분석을 시작했습니다' });
      setJobId(accepted.runId ?? accepted.jobId);
    },
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }, 7000),
  });

  const leadIds = (jobRun?.result?.lead_ids as string[] | undefined) ?? [];

  return (
    <form
      className="import-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!content.trim() || size > MAX_BYTES || jobActive || submit.isPending) return;
        setJobId(null);
        submit.mutate();
      }}
    >
      <p className="field-hint">직접 확보한 글만 입력하세요. 원문 주소를 대신 내려받지 않으며, 정책 제한을 우회하는 수집 용도로 쓰지 않습니다.</p>
      <div className="import-row">
        <label className="select">
          <span className="sr-only">형식</span>
          <select value={format} onChange={(e) => setFormat(e.target.value as ManualImportInput['format'])} aria-label="형식">
            <option value="text">텍스트 붙여넣기 (글 1개)</option>
            <option value="csv">CSV (title, body, url, region, published_at)</option>
            <option value="json">JSON (같은 필드의 배열)</option>
          </select>
        </label>
        <input ref={fileRef} type="file" accept=".txt,.csv,.json,text/plain,text/csv,application/json" className="sr-only" id="import-file" onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          if (f.size > MAX_BYTES) {
            toast.show({ tone: 'bad', text: '파일이 2MB 를 넘습니다' });
            return;
          }
          setFileName(f.name);
          setContent(await f.text());
          if (f.name.endsWith('.csv')) setFormat('csv');
          else if (f.name.endsWith('.json')) setFormat('json');
        }} />
        <Button size="sm" icon={<Upload size={14} />} onClick={() => fileRef.current?.click()}>
          파일 선택
        </Button>
        {fileName ? <span className="muted-text">{fileName}</span> : null}
      </div>
      <textarea aria-label="입력 내용" rows={10} value={content} onChange={(e) => setContent(e.target.value)} placeholder={format === 'text' ? '첫 줄은 제목, 나머지는 본문으로 분석합니다' : '파일을 선택하거나 내용을 붙여 넣으세요'} />
      {format === 'text' ? <input aria-label="원문 주소 (선택)" placeholder="원문 주소 (선택, http·https 만)" value={url} onChange={(e) => setUrl(e.target.value)} /> : null}
      <div className="form-actions">
        <Button type="submit" variant="primary" size="sm" busy={submit.isPending || jobActive} disabled={!content.trim() || size > MAX_BYTES}>
          분석에 추가
        </Button>
        <span className={`field-hint${size > MAX_BYTES ? ' field-error' : ''}`}>{(size / 1024).toFixed(1)} KB / 2 MB</span>
        {mode === 'demo' ? <span className="field-hint">데모: 텍스트만 처리</span> : null}
      </div>
      {job.isError ? <p role="status" className="field-hint">진행 상태를 다시 확인하는 중입니다. 입력을 다시 제출하지 않아도 실행 기록에 작업이 남아 있습니다.</p> : null}
      {jobRun ? (
        <div className={`inline-alert inline-alert-${jobRun.state === 'succeeded' ? 'good' : jobRun.state === 'failed' ? 'bad' : 'info'}`} role="status">
          {jobRun.state === 'succeeded' || jobRun.state === 'partial' ? (
            <span>
              {runStateLabel[jobRun.state]} — 신규 {jobRun.counts.created}건, 중복 {jobRun.counts.duplicates}건
              {jobRun.error ? ` · ${jobRun.error.message}` : ''}
              {leadIds[0] ? (
                <button type="button" className="link-btn" onClick={() => onOpenLead(leadIds[0] as string)}>
                  리드 보기
                </button>
              ) : null}
            </span>
          ) : jobRun.state === 'failed' ? (
            <span>실패 — {jobRun.error?.message}</span>
          ) : (
            <span>{runStateLabel[jobRun.state]}…</span>
          )}
        </div>
      ) : null}
    </form>
  );
}

// CollectionPanel stays mounted when its tabs/dialog close. Keep the accepted
// job here so changing views cannot lose an in-flight operation or submit it again.
function useExportTask(visible: boolean) {
  const port = usePort();
  const qc = useQueryClient();
  const epoch = getDatabaseEpoch(qc);
  const [tracked, setTracked] = useState<{ id: string | null; epoch: number } | null>(null);
  const [requestError, setRequestError] = useState<{ epoch: number; message: string; uncertain: boolean } | null>(null);
  const submitting = useRef(false);
  const history = useQuery({
    queryKey: [...qk.runs, 'export-status', epoch],
    queryFn: async () => {
      if (getDatabaseEpoch(qc) !== epoch) return { active: [], recent: [] };
      const [active, recent] = await Promise.all([
        port.listRuns({ kinds: ['export'], states: ACTIVE_RUN_STATES, limit: 100 }),
        port.listRuns({ kinds: ['export'], limit: 20 }),
      ]);
      return getDatabaseEpoch(qc) === epoch ? { active, recent } : { active: [], recent: [] };
    },
    enabled: visible,
    staleTime: 0,
    retry: false,
    refetchInterval: 10_000,
  });
  const currentTracked = tracked?.epoch === epoch ? tracked : null;
  const currentError = requestError?.epoch === epoch ? requestError : null;
  const selectedId = currentTracked?.id ?? history.data?.active[0]?.id ?? (currentTracked ? null : history.data?.recent[0]?.id) ?? null;
  const job = useQuery({
    queryKey: [...qk.runs, 'export-job', epoch, selectedId],
    queryFn: async () => {
      // resetDatabaseQueries can refetch an old observer before React switches
      // to the restored epoch. Do not look up that old ID in the new database.
      if (getDatabaseEpoch(qc) !== epoch) return null;
      const run = await port.getRun(selectedId!);
      if (getDatabaseEpoch(qc) !== epoch) return null;
      if (run.kind !== 'export') throw new Error('내보내기 실행을 확인할 수 없습니다.');
      return run;
    },
    enabled: !!selectedId,
    retry: false,
    refetchInterval: (q) => q.state.data && TERMINAL_RUN_STATES.includes(q.state.data.state) ? false : 1000,
  });
  const record = job.data ?? history.data?.active.find((r) => r.id === selectedId) ?? history.data?.recent.find((r) => r.id === selectedId);
  const hasActive = !!history.data?.active.length || (!!selectedId && (!record || ACTIVE_RUN_STATES.includes(record.state)));
  useEffect(() => {
    if (record && TERMINAL_RUN_STATES.includes(record.state)) void qc.invalidateQueries({ queryKey: [...qk.runs, 'export-status'] });
  }, [record?.id, record?.state, qc]);
  useEffect(() => {
    if (currentError?.uncertain && history.data?.active[0]) {
      setTracked({ id: history.data.active[0].id, epoch });
      setRequestError(null);
    }
  }, [currentError?.uncertain, history.data, epoch]);
  const start = useMutation({
    retry: false,
    mutationFn: async (request: { kind: 'leads_csv' | 'leads_json' | 'diagnostics'; epoch: number; posted: boolean }) => {
      // Recheck immediately before POST. This also catches a task started while
      // the dialog was closed or from another view of this local application.
      const active = await port.listRuns({ kinds: ['export'], states: ACTIVE_RUN_STATES, limit: 100 });
      if (getDatabaseEpoch(qc) !== request.epoch) throw new Error('데이터가 복원되었습니다. 현재 상태를 다시 확인하세요.');
      if (active[0]) return active[0].id;
      request.posted = true;
      const accepted = await port.exportData({ kind: request.kind });
      return accepted.runId ?? accepted.jobId;
    },
    onSuccess: (id, request) => {
      if (getDatabaseEpoch(qc) !== request.epoch) return;
      setTracked({ id, epoch: request.epoch });
      setRequestError(null);
      void qc.invalidateQueries({ queryKey: [...qk.runs, 'export-status'] });
      void qc.invalidateQueries({ queryKey: [...qk.runs, 'history'] });
    },
    onError: (error, request) => {
      if (getDatabaseEpoch(qc) !== request.epoch) return;
      const err = toPortError(error);
      setRequestError({ epoch: request.epoch, message: err.message, uncertain: request.posted && (err.kind === 'connection' || err.code === 'unexpected') });
      void qc.invalidateQueries({ queryKey: [...qk.runs, 'export-status'] });
    },
    onSettled: () => { submitting.current = false; },
  });
  const request = (kind: 'leads_csv' | 'leads_json' | 'diagnostics') => {
    if (submitting.current || hasActive || currentError?.uncertain || history.isPending || history.isError) return;
    submitting.current = true;
    setTracked({ id: null, epoch });
    setRequestError(null);
    start.mutate({ kind, epoch, posted: false });
  };
  return {
    request, record: start.isPending ? undefined : record,
    busy: start.isPending || hasActive,
    blocked: history.isPending || history.isError || !!currentError?.uncertain,
    checking: history.isPending,
    statusError: job.isError ? job.error : history.isError ? history.error : null,
    requestError: currentError,
    canPrepareNew: !!currentError?.uncertain && history.isSuccess && !history.isFetching && !hasActive,
    prepareNew: () => setRequestError(null),
    refresh: () => {
      void history.refetch();
      if (selectedId) void job.refetch();
    },
  };
}

function ExportTab({ task, onOpenRuns }: { task: ReturnType<typeof useExportTask>; onOpenRuns: () => void }) {
  const record = task.record;
  const terminal = record && TERMINAL_RUN_STATES.includes(record.state);
  const name = record ? exportFileName(record) : null;
  const tone = record?.state === 'failed' ? 'bad' : record?.state === 'partial' || (record?.state === 'succeeded' && !name) ? 'warn' : record?.state === 'succeeded' ? 'good' : 'info';
  const stateText = record?.state === 'failed'
    ? `내보내기 실패 — ${record.error?.message ?? '파일을 저장하지 못했습니다.'}`
    : record?.state === 'cancelled'
      ? '내보내기가 취소되었습니다. 완료 파일은 확인되지 않았습니다.'
      : record ? `내보내기 ${runStateLabel[record.state]}${terminal ? '' : '…'}${record.state === 'partial' && record.error ? ` — ${record.error.message}` : ''}` : '내보내기를 요청하는 중…';
  return (
    <div className="export">
      <p className="field-hint">파일은 사용자 데이터 폴더의 exports 하위 폴더(기본: AppData\Local\Worklead\exports)에 저장됩니다. CSV 는 수식 주입을 막도록 위험한 칸 앞에 ' 를 붙입니다. API 키 등 비밀값은 포함하지 않습니다.</p>
      <div className="form-actions">
        <Button size="sm" busy={task.busy} disabled={task.blocked} onClick={() => task.request('leads_csv')}>
          리드 CSV
        </Button>
        <Button size="sm" busy={task.busy} disabled={task.blocked} onClick={() => task.request('leads_json')}>
          리드 JSON
        </Button>
        <Button size="sm" busy={task.busy} disabled={task.blocked} onClick={() => task.request('diagnostics')}>
          진단 정보
        </Button>
      </div>
      <p className="field-hint">진단 정보에는 설정 상태·오류 코드·작업 집계만 담습니다. 공고·메모·검색어 원문과 원본 로그는 포함하지 않습니다.</p>
      {task.checking ? <p role="status" className="field-hint">기존 내보내기 작업을 확인하는 중…</p> : null}
      {task.requestError ? (
        <div role="alert" className="inline-alert inline-alert-warn">
          <div>
            <p>{task.requestError.uncertain ? '내보내기 요청 응답을 확인하지 못했습니다. 실행 기록에서 작업을 확인하세요. 자동으로 다시 요청하지 않습니다.' : `내보내기를 요청하지 못했습니다 — ${task.requestError.message}`}</p>
            {task.canPrepareNew ? <Button size="sm" onClick={task.prepareNew}>기록 확인 후 새 요청 준비</Button> : null}
          </div>
        </div>
      ) : null}
      {task.statusError ? (
        <div role="alert" className="inline-alert inline-alert-warn">
          <div>
            <p>내보내기 상태를 확인하지 못했습니다. 작업 실패 여부는 확인되지 않았습니다. {errorMessage(task.statusError)}</p>
            <Button size="sm" onClick={task.refresh}>상태 다시 확인</Button>
          </div>
        </div>
      ) : null}
      {record || (task.busy && !task.checking) ? (
        <div role="status" className={`inline-alert inline-alert-${tone}`}>
          <div>
            <p>{stateText}</p>
            {record ? <><RunDetails run={record} /><ExportFile run={record} /></> : null}
          </div>
        </div>
      ) : null}
      <Button size="sm" onClick={onOpenRuns}>내보내기 실행 기록</Button>
    </div>
  );
}
