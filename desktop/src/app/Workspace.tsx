import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { LeadFilter } from '../domain/model';
import { formatMoneyMinor, todayKst } from '../domain/format';
import { emptyCollectionMessage } from '../domain/collection';
import { VIEWS, type ViewId } from '../domain/queues';
import { CollectionPanel } from '../features/collection/CollectionPanel';
import { CafeWorkspace } from '../features/cafe/CafeWorkspace';
import { ApplicationWorkspace } from '../features/jobs/ApplicationWorkspace';
import { LeadDetailPane, type DetailHandle } from '../features/detail/LeadDetailPane';
import { CurrencyTotals } from '../features/detail/Records';
import { IssueStrip, deriveIssues } from '../features/header/IssueStrip';
import { TopBar } from '../features/header/TopBar';
import { ShortcutHelp } from '../features/help/ShortcutHelp';
import { DEFAULT_FILTER, FilterBar, isDefaultFilter } from '../features/leads/FilterBar';
import { LeadList, type ListSnapshot } from '../features/leads/LeadList';
import { FreshnessMetrics } from '../features/leads/FreshnessMetrics';
import { QueueTabs, viewCount } from '../features/leads/QueueTabs';
import { SettingsDialog } from '../features/settings/SettingsDialog';
import { Button } from '../ui/Button';
import { EmptyState } from '../ui/States';
import { usePortCtx } from './PortContext';
import { applyDisplayPrefs, useMediaQuery, usePref, type FontScale, type ThemePref } from './prefs';
import { qk, useBootstrap, useCounts, useRunStatus, useSales, useSources } from './queries';
import { useLiveEvents } from './useLiveEvents';

function isTyping(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

function useNow(ms = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

const EMPTY_SNAPSHOT: ListSnapshot = { ids: [], byId: new Map(), loading: true, error: null, hasMore: new Map() };

export function Workspace() {
  const [workspace, setWorkspace] = useState<'leads' | 'cafe' | 'jobs'>('leads');
  const { port, mode, scenario, setScenario } = usePortCtx();
  const cafeStatus = useQuery({ queryKey: ['cafe', 'status'], queryFn: () => port.cafe.status(), refetchInterval: 5000 });
  const qc = useQueryClient();
  const now = useNow();
  const [view, setView] = usePref<ViewId>('view', 'fresh');
  const [filter, setFilterPref] = usePref<LeadFilter>('filter', { ...DEFAULT_FILTER, sort: 'published' });
  const [fontScale, setFontScale] = usePref<FontScale>('fontScale', 'normal');
  const [theme, setTheme] = usePref<ThemePref>('theme', 'system');
  const [split, setSplit] = usePref<number>('split', 0.42);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [collectionOpen, setCollectionOpen] = useState(false);
  const [collectionTab, setCollectionTab] = useState<'sources' | 'import'>('sources');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<ListSnapshot>(EMPTY_SNAPSHOT);
  const lastDismissed = useRef<string | null>(null);
  const detailRef = useRef<DetailHandle>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const wide = useMediaQuery('(min-width: 1100px)');

  useEffect(() => applyDisplayPrefs(fontScale, theme), [fontScale, theme]);

  const boot = useBootstrap();
  const sources = useSources();
  const runs = useRunStatus();
  const counts = useCounts(filter);
  const sales = useSales(view === 'active' || view === 'fresh');
  const viewDef = VIEWS.find((v) => v.id === view) ?? (VIEWS[0] as (typeof VIEWS)[number]);

  // 알림·가져오기 등 목록 밖에서 연 리드는 목록에 아직 없어도 선택을 유지한다
  const pinnedRef = useRef<string | null>(null);
  // 24시간 경계로 목록에서 빠져도 열린 상세·작성 중인 초안은 유지한다.
  const minute = Math.floor(now.getTime() / 60_000);
  const freshRefreshMinute = useRef(minute);
  useEffect(() => {
    if (freshRefreshMinute.current === minute) return;
    freshRefreshMinute.current = minute;
    if (view !== 'fresh') return;
    pinnedRef.current = selectedId;
    void qc.invalidateQueries({ queryKey: qk.leads('fresh', filter) });
    void qc.invalidateQueries({ queryKey: qk.counts(filter) });
  }, [minute, view, qc, filter, selectedId]);
  const kstDay = todayKst(now);
  const previousKstDay = useRef(kstDay);
  useEffect(() => {
    if (previousKstDay.current === kstDay) return;
    previousKstDay.current = kstDay;
    // 마감으로 목록에서 빠져도 열린 상세와 작성 중인 메모는 유지한다.
    pinnedRef.current = selectedId;
    void qc.invalidateQueries({ queryKey: ['counts'] });
    void qc.invalidateQueries({ queryKey: qk.leadsAll });
    void qc.invalidateQueries({ queryKey: ['lead'] });
  }, [kstDay, qc, selectedId]);

  const openLead = useCallback((id: string) => {
    pinnedRef.current = id;
    setSelectedId(id);
    setOverlayOpen(true);
    window.setTimeout(() => detailRef.current?.focus(), 0);
  }, []);
  const live = useLiveEvents(openLead, () => setWorkspace('jobs'));
  useEffect(() => {
    // 자동 반영으로 행이 다른 대기열로 이동해도 현재 상세·편집 내용을 유지한다.
    if (live.listStale && selectedId) pinnedRef.current = selectedId;
  }, [live.listStale, selectedId]);

  const setFilter = useCallback(
    (f: LeadFilter) => {
      pinnedRef.current = null;
      setFilterPref(f);
    },
    [setFilterPref],
  );

  const categoryMap = useMemo(() => new Map((boot.data?.categories ?? []).map((c) => [c.id, c.label])), [boot.data]);
  const categoryLabel = useCallback((id: string) => categoryMap.get(id) ?? id, [categoryMap]);

  // 넓은 화면: 목록이 바뀌면 첫 리드를 자동 선택 (바로 비교·판단)
  useEffect(() => {
    if (snapshot.loading) return;
    if (selectedId && (snapshot.byId.has(selectedId) || pinnedRef.current === selectedId)) return;
    if (!wide) return;
    setSelectedId(snapshot.ids[0] ?? null);
  }, [snapshot, selectedId, wide]);

  const changeView = useCallback(
    (v: ViewId) => {
      pinnedRef.current = null;
      setView(v);
      if (v === 'fresh') setFilterPref({ ...filter, sort: 'published' });
    },
    [setView, setFilterPref, filter],
  );

  const move = useCallback(
    (delta: number) => {
      const ids = snapshot.ids;
      if (!ids.length) return;
      const idx = selectedId ? ids.indexOf(selectedId) : -1;
      const next = Math.max(0, Math.min(ids.length - 1, idx + delta));
      setSelectedId(ids[next] ?? null);
      if (next >= ids.length - 3) {
        const last = snapshot.byId.get(ids[ids.length - 1] ?? '');
        if (last) snapshot.hasMore.get(last.queue)?.();
      }
    },
    [snapshot, selectedId],
  );

  const refreshAll = useCallback(() => {
    void live.acknowledge();
    void qc.invalidateQueries({ queryKey: qk.sources });
    void qc.invalidateQueries({ queryKey: qk.runs });
  }, [live, qc]);

  const anyDialog = collectionOpen || settingsOpen || helpOpen;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r')) {
        e.preventDefault(); // 앱 전체 새로고침 대신 데이터만 다시 불러온다
        refreshAll();
        void qc.invalidateQueries({ queryKey: ['cafe'] });
        return;
      }
      if (workspace !== 'leads' || anyDialog || e.altKey || e.ctrlKey || e.metaKey) return;
      if (isTyping(e.target)) return;
      const k = e.key;
      const handled = (fn: () => void) => {
        e.preventDefault();
        fn();
      };
      if (k === 'ArrowDown' || k === 'j') return handled(() => move(1));
      if (k === 'ArrowUp' || k === 'k') return handled(() => move(-1));
      if (k === 'Home') return handled(() => setSelectedId(snapshot.ids[0] ?? null));
      if (k === 'End') return handled(() => setSelectedId(snapshot.ids[snapshot.ids.length - 1] ?? null));
      if (k === 'Enter' && selectedId) return handled(() => openLead(selectedId));
      if (k === 'Escape') {
        if (!wide && overlayOpen) return handled(() => setOverlayOpen(false));
        if (document.activeElement && document.activeElement !== document.body) {
          (document.activeElement as HTMLElement).blur?.();
          document.getElementById('lead-list')?.focus();
        }
        return;
      }
      if (k === '/') return handled(() => searchRef.current?.focus());
      if (k === '?') return handled(() => setHelpOpen(true));
      if (k === ',') return handled(() => setSettingsOpen(true));
      if (k === 'c') return handled(() => setCollectionOpen(true));
      const v = VIEWS.find((x) => x.key === k);
      if (v) return handled(() => changeView(v.id));
      if (!selectedId) return;
      if (k === 'o') return handled(() => detailRef.current?.openOriginal());
      if (k === 's') return handled(() => detailRef.current?.toggleInterested());
      if (k === 'x') return handled(() => detailRef.current?.toggleDismissed());
      if (k === 'r') return handled(() => detailRef.current?.recheck());
      if (k === 'm')
        return handled(() => {
          if (wide) detailRef.current?.focusMemo();
          else {
            setOverlayOpen(true);
            window.setTimeout(() => detailRef.current?.focusMemo(), 0);
          }
        });
      if (k === 'u' && lastDismissed.current) {
        const id = lastDismissed.current;
        return handled(() => {
          setSelectedId(id);
          window.setTimeout(() => detailRef.current?.toggleDismissed(), 0);
          lastDismissed.current = null;
        });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [workspace, qc, anyDialog, move, openLead, overlayOpen, refreshAll, selectedId, changeView, snapshot.ids, wide]);

  const onDismissed = useCallback(
    (id: string) => {
      lastDismissed.current = id;
      // 다음 리드로 자동 이동 (빠른 검토)
      const ids = snapshot.ids;
      const idx = ids.indexOf(id);
      const next = ids[idx + 1] ?? ids[idx - 1];
      if (next && wide) setSelectedId(next);
    },
    [snapshot.ids, wide],
  );

  const issues = deriveIssues({
    notices: boot.data?.notices,
    sources: sources.data ?? [],
    runs: runs.data ?? [],
    stream: live.stream,
    mode,
    openCollection: () => setCollectionOpen(true),
    now,
  });

  const totalAll = counts.data?.all ?? null;
  if (cafeStatus.data?.consent && cafeStatus.data.session.state !== 'logged_in') issues.push({ key: 'cafe-session', tone: 'warn', text: !cafeStatus.data.session.checked_at ? '당근 카페 세션 확인 전 · 먼저 세션을 확인하세요' : cafeStatus.data.session.state === 'needs_user' ? '당근 카페 작업 정지 · 브라우저에서 인증·차단 화면을 직접 확인하세요' : '당근 카페 로그인 필요', action: { label: '카페 보기', run: () => setWorkspace('cafe') } });
  const emptyNode = (() => {
    const sites = (sources.data ?? []).filter((s) => s.kind === 'site');
    if (totalAll === 0 && isDefaultFilter(filter)) {
      return (
        <EmptyState
          title="아직 리드가 없습니다"
          actions={
            <>
              <Button variant="primary" onClick={() => { setCollectionTab('import'); setCollectionOpen(true); }}>
                수동 입력으로 추가
              </Button>
              <Button onClick={() => setCollectionOpen(true)}>수집 상태 보기</Button>
            </>
          }
        >
          {sites.length ? sites.map((site) => <p key={site.id}>{sites.length > 1 ? `${site.name}: ` : ''}{emptyCollectionMessage(site, runs.data ?? [])}</p>) : <p>{emptyCollectionMessage(undefined, runs.data ?? [])}</p>}
        </EmptyState>
      );
    }
    if (!isDefaultFilter(filter)) {
      return (
        <EmptyState
          title="현재 필터에 맞는 리드가 없습니다"
          actions={
            <Button onClick={() => setFilter(DEFAULT_FILTER)} variant="primary">
              필터 초기화
            </Button>
          }
        >
          <p>{totalAll !== null ? `필터 적용 후 전체 ${totalAll}건 · ` : ''}이 보기에는 0건입니다.</p>
        </EmptyState>
      );
    }
    return <EmptyState title={viewDef.empty} />;
  })();

  const showDetail = wide || overlayOpen;
  const listPct = Math.round((1 - split) * 1000) / 10;

  return (
    <div className="app" data-mode={mode}>
      <a href={workspace === 'cafe' ? '#cafe-workspace' : workspace === 'jobs' ? '#jobs-workspace' : '#lead-list'} className="skip-link">
        목록으로 건너뛰기
      </a>
      <TopBar
        mode={mode}
        scenario={scenario}
        onScenario={setScenario}
        sources={sources.data ?? []}
        runs={runs.data ?? []}
        stream={live.stream}
        onCollection={() => setCollectionOpen(true)}
        onSettings={() => setSettingsOpen(true)}
        onHelp={() => setHelpOpen(true)}
        now={now}
      />
      <IssueStrip issues={issues} />
      <nav className="workspace-switch" aria-label="작업 공간"><Button pressed={workspace === 'leads'} onClick={() => setWorkspace('leads')}>리드</Button><Button pressed={workspace === 'jobs'} onClick={() => setWorkspace('jobs')}>지원 준비·메시지</Button><Button pressed={workspace === 'cafe'} onClick={() => setWorkspace('cafe')}>카페</Button></nav>
      {workspace === 'jobs' ? <ApplicationWorkspace now={now} onSettings={() => setSettingsOpen(true)} onOpenLead={(id) => { setWorkspace('leads'); changeView('all'); void qc.invalidateQueries({ queryKey: qk.leadsAll }); void qc.invalidateQueries({ queryKey: ['counts'] }); openLead(id); }} /> : workspace === 'cafe' ? <CafeWorkspace onOpenLead={(id) => { setWorkspace('leads'); changeView('all'); void qc.invalidateQueries({ queryKey: qk.leadsAll }); void qc.invalidateQueries({ queryKey: ['counts'] }); openLead(id); }} /> : <>

      <div className="toolbar">
        <QueueTabs view={view} onChange={changeView} counts={counts.data} />
        <FilterBar ref={searchRef} filter={filter} onChange={setFilter} sources={sources.data ?? []} categories={boot.data?.categories ?? []} />
      </div>

      {view === 'fresh' ? <div className="fresh-review-note" role="note">24시간 내 게시된 모집 중 제작 의뢰를 추천·확인 필요 구분 없이 보여줍니다. 재택·보수 등 확인할 조건은 그대로 남습니다. 오래되거나 게시 시각이 불명확한 건은 검토 탭에서 확인하세요.</div> : null}
      {view === 'fresh' ? <FreshnessMetrics metrics={sales.data?.freshness} sources={sources.data ?? []} /> : null}

      {view === 'active' && sales.data ? (
        <div className="sales-strip" aria-label="영업 성과">
          <span>
            현재 전체 리드 · 연락 <b className="num">{sales.data.contacted}</b> · 협상 <b className="num">{sales.data.negotiating}</b> · 수주 표시{' '}
            <b className="num">{sales.data.won}</b>
          </span>
          <span className="field-hint">{sales.data.periodStart && sales.data.periodEnd ? `발생일 기준 최근 ${sales.data.periodDays}일 · ${sales.data.periodStart} ~ ${sales.data.periodEnd} (한국 시간)` : '금액 집계 기간 확인 필요'}</span>
          {sales.data.byCurrency.length ? <CurrencyTotals groups={sales.data.byCurrency} /> : sales.data.periodStart ? <span>기간 내 계약·수금·환불·비용 기록 없음</span> : <span>
            KRW · 계약 확인 <b className="num">{sales.data.contractAmount === null ? '금액 확인 필요' : formatMoneyMinor(sales.data.contractAmount, 'KRW')}</b> · 실제 수금 <b className="num">{sales.data.collected === null ? '금액 확인 필요' : formatMoneyMinor(sales.data.collected, 'KRW')}</b> · 환불 <b className="num">{sales.data.refunded === null ? '금액 확인 필요' : formatMoneyMinor(sales.data.refunded, 'KRW')}</b> · 직접 비용 <b className="num">{sales.data.directCost === null ? '금액 확인 필요' : formatMoneyMinor(sales.data.directCost, 'KRW')}</b> · 미기록 건수 확인 불가
          </span>}
          <span className="field-hint">통화별 실제 기록만 합산 · 예상 금액·통화 환산 미포함</span>
        </div>
      ) : null}

      <main className={`work${showDetail ? ' has-detail' : ''}${!wide && overlayOpen ? ' is-overlay' : ''}`} style={wide ? ({ '--list-w': `${listPct}%` } as React.CSSProperties) : undefined}>
        <div className="pane-list" role="tabpanel" aria-label={viewDef.label}>
          {live.newLeads > 0 || live.listStale ? (
            <div className="new-banner" role="status">
              <span>{live.newLeads > 0 ? `새 리드 ${live.newLeads}건 · 목록 자동 반영 중` : '새 결과 자동 반영 · 오류 시 자동 재시도'}</span>
              <Button size="sm" variant="primary" icon={<RefreshCw size={13} />} onClick={refreshAll}>
                다시 불러오기
              </Button>
            </div>
          ) : null}
          <LeadList
            view={viewDef}
            filter={filter}
            counts={counts.data}
            selectedId={selectedId}
            onSelect={(id) => {
              setSelectedId(id);
              if (!wide) openLead(id);
            }}
            onOpen={openLead}
            onSnapshot={setSnapshot}
            categoryLabel={categoryLabel}
            empty={emptyNode}
            now={now}
          />
          <div className="list-foot" aria-live="polite">
            {snapshot.ids.length ? (
              <span className="num">
                {viewDef.label} {viewCount(view, counts.data) ?? snapshot.ids.length}건 중 {snapshot.ids.length}건 표시
              </span>
            ) : null}
            <span className="field-hint">
              <kbd>↑</kbd>
              <kbd>↓</kbd> 이동 · <kbd>S</kbd> 관심 · <kbd>X</kbd> 제외 · <kbd>O</kbd> 원문 · <kbd>?</kbd> 전체
            </span>
          </div>
        </div>
        {wide ? (
          <div
            className="splitter"
            role="separator"
            aria-orientation="vertical"
            aria-label="목록·상세 너비 조절"
            aria-valuemin={30}
            aria-valuemax={65}
            aria-valuenow={Math.round(split * 100)}
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'ArrowLeft') setSplit(Math.min(0.65, split + 0.02));
              if (e.key === 'ArrowRight') setSplit(Math.max(0.3, split - 0.02));
            }}
            onPointerDown={(e) => {
              const host = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect();
              const target = e.currentTarget;
              target.setPointerCapture(e.pointerId);
              const onMove = (ev: PointerEvent) => {
                const ratio = 1 - (ev.clientX - host.left) / host.width;
                setSplit(Math.max(0.3, Math.min(0.65, ratio)));
              };
              const onUp = () => {
                target.removeEventListener('pointermove', onMove);
                target.removeEventListener('pointerup', onUp);
              };
              target.addEventListener('pointermove', onMove);
              target.addEventListener('pointerup', onUp);
            }}
          />
        ) : null}
        {showDetail ? (
          <LeadDetailPane
            ref={detailRef}
            leadId={selectedId}
            sources={sources.data ?? []}
            categoryLabel={categoryLabel}
            now={now}
            overlay={!wide}
            onClose={() => {
              setOverlayOpen(false);
              document.getElementById('lead-list')?.focus();
            }}
            onDismissed={onDismissed}
          />
        ) : null}
      </main>
      </>}

      <CollectionPanel
        open={collectionOpen}
        initialTab={collectionTab}
        onClose={() => { setCollectionOpen(false); setCollectionTab('sources'); }}
        onOpenLead={(id) => {
          setCollectionOpen(false);
          setCollectionTab('sources');
          changeView('all');
          void live.acknowledge();
          openLead(id);
        }}
      />
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} fontScale={fontScale} setFontScale={setFontScale} theme={theme} setTheme={setTheme} />
      <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
