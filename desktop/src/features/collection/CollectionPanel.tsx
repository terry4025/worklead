import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Pause, Play, Search, Square, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { formatCount, formatDateTime, formatRelative } from '../../domain/format';
import { healthLabel, policyLabel, runKindLabel, runStateLabel, supportLabel } from '../../domain/labels';
import type { ManualImportInput, RunInfo, SourceInfo } from '../../domain/model';
import type { Tone } from '../../domain/present';
import { usePort, usePortCtx } from '../../app/PortContext';
import { qk, useRuns, useSources } from '../../app/queries';
import { Button } from '../../ui/Button';
import { Dialog } from '../../ui/Dialog';
import { EmptyState, ErrorState, errorMessage } from '../../ui/States';
import { StatusMark } from '../../ui/StatusMark';
import { useToast } from '../../ui/Toasts';

type Tab = 'sources' | 'runs' | 'import' | 'export';

const HEALTH_TONE: Record<SourceInfo['health']['status'], Tone> = { ok: 'good', degraded: 'warn', blocked: 'bad', error: 'bad', paused: 'muted', unknown: 'unknown' };
const RUN_TONE: Record<RunInfo['state'], Tone> = { queued: 'unknown', running: 'good', paused: 'muted', succeeded: 'good', partial: 'warn', failed: 'bad', cancelled: 'muted' };

export function CollectionPanel({ open, onClose, initialTab = 'sources', onOpenLead }: { open: boolean; onClose: () => void; initialTab?: Tab; onOpenLead: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>(initialTab);
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
      {tab === 'runs' ? <RunsTab /> : null}
      {tab === 'import' ? <ImportTab onOpenLead={onOpenLead} /> : null}
      {tab === 'export' ? <ExportTab /> : null}
    </Dialog>
  );
}

function SourcesTab() {
  const q = useSources();
  const runs = useRuns();
  if (q.isPending) return <p className="muted-text">불러오는 중…</p>;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return (
    <div className="source-list">
      {q.data.map((s) => (
        <SourceCard key={s.id} source={s} runs={runs.data ?? []} />
      ))}
    </div>
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
  const c = s.coverage;

  return (
    <article className="source-card" aria-labelledby={`src-${s.id}`}>
      <header className="source-head">
        <h3 id={`src-${s.id}`}>{s.name}</h3>
        <StatusMark tone={HEALTH_TONE[s.health.status]}>{healthLabel[s.health.status]}</StatusMark>
        {s.kind === 'site' ? <StatusMark tone={allowed ? 'good' : s.policy.status === 'blocked' ? 'bad' : 'unknown'}>{policyLabel[s.policy.status]}</StatusMark> : null}
      </header>
      {s.scopeNote ? <p className="muted-text">{s.scopeNote}</p> : null}
      {s.health.message ? <p className={`inline-note tone-${HEALTH_TONE[s.health.status]}`}>{s.health.message}</p> : null}

      {s.kind === 'site' ? (
        <>
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
                  {c.targetUnitsTotal ? ` (${c.targetUnitsTotal}개 시·도)` : ''}
                </span>
              </div>
              <div className="cov-row">
                <span className="cov-label">지역 목록</span>
                <span>
                  {c.targetUnitsCovered !== null ? `${c.targetUnitsCovered}/${c.targetUnitsTotal ?? '?'} 시·도 확보 · ` : ''}
                  {c.regionListStatus === 'not_applicable'
                    ? `지역 구분 없이 전국 (${c.unitLabel})`
                    : { verified: '전체성 확인됨', unverified: '전체성 미확인 → 부분 탐색으로 표시', unknown: '미확인' }[c.regionListStatus]}
                </span>
              </div>
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
                  <span className="cov-label">요청 예산</span>
                  <span className="num">
                    오늘 {formatCount(c.budget.used)}/{c.budget.limit ?? '∞'} {c.budget.unitLabel}
                    {c.estimatedCycleDays ? ` · 전국 한 바퀴 약 ${c.estimatedCycleDays}일 (추정)` : ''}
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

          <div className="source-controls">
            <Button size="sm" variant="primary" icon={<Search size={14} />} disabled={!!blockedReason || !!active} busy={start.isPending} title={blockedReason ?? (active ? '진행 중인 실행이 있습니다' : '지금 전국 탐색 한 번 실행')} onClick={() => start.mutate('discovery')}>
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

function RunsTab() {
  const q = useRuns();
  const port = usePort();
  const qc = useQueryClient();
  const toast = useToast();
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'cancel' }) => port.runAction(id, action),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.runs }),
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }),
  });
  if (q.isPending) return <p className="muted-text">불러오는 중…</p>;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data.length) return <EmptyState title="실행 기록이 없습니다">자동 수집을 켜거나 수동 입력을 하면 여기에 기록됩니다.</EmptyState>;
  return (
    <ul className="run-list">
      {q.data.map((r) => (
        <li key={r.id} className="run-item">
          <div className="run-line">
            <StatusMark tone={RUN_TONE[r.state]}>{runStateLabel[r.state]}</StatusMark>
            <span className="run-kind">{runKindLabel[r.kind]}</span>
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
            {r.counts.parseFailures ? <span className="tone-warn"> · 파싱 실패 {r.counts.parseFailures}</span> : null}
            {r.counts.fetchFailures ? <span className="tone-warn"> · 요청 실패 {r.counts.fetchFailures}</span> : null}
            {r.counts.policyStops ? <span className="tone-bad"> · 정책 정지 {r.counts.policyStops}</span> : null}
            {r.counts.aiFailures ? <span className="tone-warn"> · AI 실패 {r.counts.aiFailures}</span> : null}
          </p>
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
  const [jobRun, setJobRun] = useState<RunInfo | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const size = new Blob([content]).size;

  const submit = useMutation({
    mutationFn: () => port.importManual({ format, content, fileName, originalUrl: url.trim() || null }),
    onSuccess: async (job) => {
      toast.show({ tone: 'info', text: '분석을 시작했습니다' });
      // 짧게 결과를 확인해 새 리드로 바로 갈 수 있게 한다
      for (let i = 0; i < 10; i += 1) {
        await new Promise((r) => setTimeout(r, 500));
        try {
          const run = await port.getRun(job.jobId);
          setJobRun(run);
          if (['succeeded', 'failed', 'partial'].includes(run.state)) break;
        } catch {
          break;
        }
      }
      void qc.invalidateQueries({ queryKey: ['counts'] });
    },
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }, 7000),
  });

  const leadIds = (jobRun?.result?.lead_ids as string[] | undefined) ?? [];

  return (
    <form
      className="import-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!content.trim() || size > MAX_BYTES) return;
        setJobRun(null);
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
        <Button type="submit" variant="primary" size="sm" busy={submit.isPending} disabled={!content.trim() || size > MAX_BYTES}>
          분석에 추가
        </Button>
        <span className={`field-hint${size > MAX_BYTES ? ' field-error' : ''}`}>{(size / 1024).toFixed(1)} KB / 2 MB</span>
        {mode === 'demo' ? <span className="field-hint">데모: 텍스트만 처리</span> : null}
      </div>
      {jobRun ? (
        <div className={`inline-alert inline-alert-${jobRun.state === 'succeeded' ? 'good' : jobRun.state === 'failed' ? 'bad' : 'info'}`} role="status">
          {jobRun.state === 'succeeded' ? (
            <span>
              완료 — 신규 {jobRun.counts.created}건, 중복 {jobRun.counts.duplicates}건
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

function ExportTab() {
  const port = usePort();
  const toast = useToast();
  const [result, setResult] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: async (kind: 'leads_csv' | 'leads_json' | 'diagnostics') => {
      const job = await port.exportData({ kind });
      for (let i = 0; i < 20; i += 1) {
        await new Promise((r) => setTimeout(r, 400));
        const r = await port.getRun(job.jobId);
        if (r.state === 'succeeded') return String(r.result.file_name ?? '');
        if (r.state === 'failed') throw new Error(r.error?.message ?? '내보내기 실패');
      }
      throw new Error('내보내기가 오래 걸립니다. 실행 기록에서 확인하세요.');
    },
    onSuccess: (name) => setResult(name),
    onError: (err) => toast.show({ tone: 'bad', text: errorMessage(err) }),
  });
  return (
    <div className="export">
      <p className="field-hint">파일은 사용자 데이터 폴더(AppData\Local\Worklead\exports)에 저장됩니다. CSV 는 수식 주입을 막도록 위험한 칸 앞에 ' 를 붙입니다. API 키 등 비밀값은 포함하지 않습니다.</p>
      <div className="form-actions">
        <Button size="sm" busy={run.isPending} onClick={() => run.mutate('leads_csv')}>
          리드 CSV
        </Button>
        <Button size="sm" busy={run.isPending} onClick={() => run.mutate('leads_json')}>
          리드 JSON
        </Button>
        <Button size="sm" busy={run.isPending} onClick={() => run.mutate('diagnostics')}>
          진단 정보 (로그 마스킹)
        </Button>
      </div>
      {result ? <p className="inline-alert inline-alert-good">저장됨: {result}</p> : null}
    </div>
  );
}
