import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, Search, RefreshCw, MessageSquare, Send, Clock, Settings2, FolderOpen, Plus, ShieldCheck } from 'lucide-react';
import { usePort } from '../../app/PortContext';
import type { CafeEntry, CafeSnapshot } from '../../domain/cafe';
import { Button } from '../../ui/Button';
import { StatusGlyph } from '../../ui/StatusMark';
import { openExternal } from '../../platform/external';
import { TemplateEditor } from './TemplateEditor';
import { Inquiry } from './Inquiry';
import { ScheduleSettings } from './ScheduleSettings';
import './cafe.css';

const states: Record<CafeEntry['status'], string> = { candidate: '판정 대기', needs_join: '직접 가입 필요', eligible: '승인 대기', approved: '승인됨', excluded: '제외됨' };
const jobsLabel = { check: '세션 확인', login: '로그인', discover: '카페 찾기', inspect: '규칙 다시 조사', collect: '글 수집', post: '게시', comments: '댓글 확인' };
const jobsState = { queued: '대기', running: '진행 중', succeeded: '완료', failed: '실패', cancelled: '취소' };
const attemptLabels = { pending: '게시 준비', posted: '게시 확인', already_exists: '같은 제목 있어 건너뜀', posted_mismatch: '게시 내용 확인 필요', failed: '실패 · 자동 재시도 없음', removed: '삭제·숨김 확인 · 재게시 중지', skipped_rule: '규칙상 건너뜀' };
type Tab = 'cafes' | 'attempts' | 'inquiries' | 'jobs' | 'settings';

export function CafeWorkspace({ onOpenLead }: { onOpenLead?: (id: string) => void }) {
  const port = usePort().cafe;
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>('cafes');
  const [url, setUrl] = useState('');
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState('');
  const lastActiveJob = useRef<string | null>(null);
  const snapshot = useQuery({ queryKey: ['cafe', 'status'], queryFn: () => port.status(), refetchInterval: 2000 });
  const cafes = useQuery({ queryKey: ['cafe', 'cafes'], queryFn: () => port.list(), refetchInterval: snapshot.data?.active_job ? 2000 : false });
  const jobs = useQuery({ queryKey: ['cafe', 'jobs'], queryFn: () => port.jobs(), refetchInterval: snapshot.data?.active_job ? 2000 : false });
  const template = useQuery({ queryKey: ['cafe', 'template'], queryFn: () => port.template() });
  const inquiries = useQuery({ queryKey: ['cafe', 'comments'], queryFn: () => port.comments(), refetchInterval: snapshot.data?.active_job ? 2000 : false });
  const attempts = useQuery({ queryKey: ['cafe', 'attempts'], queryFn: () => port.attempts(), refetchInterval: snapshot.data?.active_job ? 2000 : false });
  const command = useMutation({ mutationFn: async (action: () => Promise<unknown>) => action(), onSuccess: (result) => { if (result && typeof result === 'object' && 'state' in result && 'id' in result && typeof result.id === 'string' && (result.state === 'queued' || result.state === 'running')) lastActiveJob.current = result.id; setError(''); void qc.invalidateQueries({ queryKey: ['cafe'] }); void qc.invalidateQueries({ queryKey: ['sources'] }); }, onError: (e) => setError(e.message) });
  useEffect(() => {
    if (!snapshot.data) return;
    const current = snapshot.data.active_job?.id ?? null;
    if (lastActiveJob.current && lastActiveJob.current !== current) {
      for (const key of ['cafes', 'comments', 'attempts', 'jobs']) void qc.invalidateQueries({ queryKey: ['cafe', key], exact: true });
      void qc.invalidateQueries({ queryKey: ['sources'] });
    }
    lastActiveJob.current = current;
  }, [snapshot.data?.active_job?.id, snapshot.dataUpdatedAt, qc]);
  const run = (action: () => Promise<unknown>) => command.mutate(action);
  const data = snapshot.data;
  const busy = command.isPending || Boolean(data?.active_job);
  const rows = (cafes.data ?? []).filter((row) => filter === 'all' || row.status === filter);
  const tabs = [
    { id: 'cafes', label: '카페 목록', icon: FolderOpen, count: cafes.data?.length },
    { id: 'attempts', label: '게시 기록', icon: Send },
    { id: 'inquiries', label: `문의${inquiries.data?.unread_count ? ` (${inquiries.data.unread_count})` : ''}`, icon: MessageSquare },
    { id: 'jobs', label: '실행 기록', icon: Clock },
    { id: 'settings', label: '카페 설정', icon: Settings2 },
  ] as const;
  if (snapshot.isPending) return <main tabIndex={-1} id="cafe-workspace" className="cafe-workspace"><div className="cafe-shell cafe-loading" role="status"><h1>카페</h1><p>카페 상태를 불러오는 중…</p><div className="cafe-loading-line" /><div className="cafe-loading-line" /></div></main>;
  if (!data) return <main tabIndex={-1} id="cafe-workspace" className="cafe-workspace"><div className="cafe-shell"><EmptyState title="카페에 연결하지 못했습니다" body={<span role="alert">카페 상태 조회 실패: {snapshot.error?.message}</span>} /><Button onClick={() => void snapshot.refetch()}>다시 불러오기</Button></div></main>;
  const sessionLabel = !data.session.checked_at ? '세션 확인 전' : data.session.state === 'logged_in' ? '로그인 확인됨' : data.session.state === 'needs_user' ? '사용자 확인 필요 · 카페 작업 정지' : '로그인 필요';
  return <main tabIndex={-1} id="cafe-workspace" className="cafe-workspace"><div className="cafe-shell">
    <header className="cafe-heading"><div><h1>카페</h1><p>게시할 곳을 관리하고, 새 의뢰와 문의를 확인하세요.</p></div>{data.consent ? <Button icon={<RefreshCw size={15} />} disabled={busy || !data.capabilities.collection || data.session.state !== 'logged_in'} onClick={() => run(() => port.action('collect'))}>카페 글 수집</Button> : null}</header>
    {!data.consent ? <section className="cafe-consent" aria-label="카페 기능 위험 안내"><ShieldCheck size={28} aria-hidden="true" /><h2>시작 전에 확인해 주세요</h2><p>{data.consent_text}</p><Button variant="primary" busy={command.isPending} onClick={() => run(async () => { await port.consent(data.consent_version); await port.action('check'); })}>위험 안내 확인</Button></section> : <>
      <section className="cafe-session" aria-label="카페 로그인 상태"><div className="cafe-session-state"><StatusGlyph tone={data.session.state === 'logged_in' ? 'good' : 'warn'} /><span>{sessionLabel}</span><Button variant="ghost" size="sm" icon={<RefreshCw size={14} />} disabled={busy} onClick={() => run(() => port.action('check'))}>세션 확인</Button>{data.session.checked_at && data.session.state !== 'logged_in' ? <Button variant="primary" size="sm" disabled={busy} onClick={() => run(() => port.action('login'))}>브라우저 열어 로그인</Button> : null}</div><div className="cafe-usage"><span>오늘 페이지 <b className="num">{data.usage.page_count}<small> / {data.usage.daily_page_limit}</small></b></span><span>게시 <b className="num">{data.usage.post_count}<small> / {data.usage.daily_post_limit}</small></b></span><label className="cafe-auto"><input type="checkbox" checked={data.settings.auto_post} disabled={!data.capabilities.posting || busy} onChange={(e) => run(() => port.settings({ auto_post: e.target.checked }))} /> 자동 게시 <strong>{data.settings.auto_post ? '켜짐' : '꺼짐'}</strong></label></div></section>
      {data.active_job ? <p className="cafe-notice" role="status">{jobsLabel[data.active_job.kind]} 진행 중 · {data.active_job.kind === 'login' ? '열린 Edge에서 직접 로그인해 주세요.' : `페이지 이동 간격 ${data.settings.min_navigation_seconds}초`}</p> : null}
      {error ? <p role="alert" className="cafe-error">{error}</p> : null}
      <div className="cafe-workarea"><nav className="cafe-tabs" aria-label="카페 보기" role="tablist">{tabs.map(({ id, label, icon: Icon, ...rest }, index) => <button key={id} role="tab" type="button" id={`cafe-tab-${id}`} aria-controls={`cafe-panel-${id}`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className={`cafe-tab${tab === id ? ' is-current' : ''}`} onClick={() => setTab(id)} onKeyDown={(e) => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return; e.preventDefault(); const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (index + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; setTab(tabs[next]!.id); document.getElementById(`cafe-tab-${tabs[next]!.id}`)?.focus(); }}><Icon size={16} aria-hidden="true" /><span>{label}</span>{'count' in rest && rest.count ? <span className="cafe-tab-count num">{rest.count}</span> : null}</button>)}</nav>
      <section className="cafe-panel" id={`cafe-panel-${tab}`} role="tabpanel" aria-labelledby={`cafe-tab-${tab}`}>
      {tab === 'cafes' ? <div className="cafe-management"><div className="cafe-directory"><div className="cafe-toolbar"><div><h2>등록 카페 <span className="num">{cafes.data?.length ?? 0}</span></h2><p>규칙과 게시판을 확인한 뒤 게시 대상을 승인하세요.</p></div><div className="cafe-actions"><label className="cafe-filter">상태 <select value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">전체</option>{Object.entries(states).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><Button icon={<Search size={15} />} variant="primary" disabled={busy || data.session.state !== 'logged_in'} onClick={() => run(() => port.action('discover'))}>카페 찾기</Button><Button icon={<RefreshCw size={15} />} disabled={busy || data.session.state !== 'logged_in'} onClick={() => run(() => port.action('inspect'))}>등록 카페 다시 조사</Button></div></div>
        <form className="cafe-add" onSubmit={(e) => { e.preventDefault(); run(async () => { await port.add(url); setUrl(''); }); }}><label htmlFor="cafe-url">카페 URL 직접 추가</label><div><input id="cafe-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://cafe.daangn.com/카페이름" /><Button icon={<Plus size={15} />} type="submit" disabled={busy}>추가</Button></div></form>
        <p className="cafe-discovery-hint">카페 찾기는 공개 카테고리에서 설정한 검색어로 후보를 찾습니다.</p>
        {cafes.error ? <p role="alert">목록 조회 실패: {cafes.error.message}</p> : null}
        <div className="cafe-list">{rows.map((row) => <article key={row.id} className="cafe-row"><div className="cafe-row-main"><div className="cafe-row-heading"><h3>{row.name}</h3><span className={`cafe-state cafe-state-${row.status}`}>{states[row.status]}</span></div><p className="cafe-row-meta">{row.category ?? '분류 미확인'} · {row.region ?? '지역 미확인'}{row.member_count !== null ? ` · 멤버 ${row.member_count.toLocaleString()}명` : ''}</p><p>{row.status_reason ?? '규칙 조사 전'}</p>{row.rule_evidence ? <details><summary>공지 규칙 근거</summary><p className="cafe-evidence">{row.rule_evidence}</p></details> : null}{row.boards.map((board) => <div className="cafe-board" key={board.id}><span><strong>{board.name}</strong><small>{board.post_allowed ? '게시 허용 근거 있음' : '게시 허용 미확인'}</small></span><Button size="sm" disabled={busy || row.status !== 'approved' || !board.post_allowed} pressed={board.approved} onClick={() => run(() => port.board(board.id, !board.approved))}>게시판 {board.approved ? '승인 취소' : '승인'}</Button></div>)}</div><div className="cafe-row-actions"><Button size="sm" icon={<ArrowUpRight size={14} />} onClick={() => { void openExternal(row.url).catch((e: Error) => setError(e.message)); }}>카페 열기</Button><Button size="sm" disabled={busy || row.status !== 'eligible'} onClick={() => run(() => port.state(row.id, 'approved'))}>게시 대상 승인</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => run(() => port.state(row.id, row.status === 'excluded' ? 'candidate' : 'excluded'))}>{row.status === 'excluded' ? '제외 취소' : '제외'}</Button></div></article>)}{!cafes.isPending && rows.length === 0 ? <EmptyState title={filter === 'all' ? '첫 카페를 등록해 보세요' : '이 상태의 카페가 없습니다'} body={filter === 'all' ? '카페 찾기로 후보를 살펴보거나, 알고 있는 카페 홈 URL을 추가하세요. 규칙과 게시판 승인을 마치면 이곳에서 관리할 수 있습니다.' : '다른 상태를 선택하면 등록된 카페를 다시 볼 수 있습니다.'} /> : null}</div>
      </div><aside className="cafe-publish" aria-label="카페 게시"><h2>템플릿 게시</h2><p>승인한 카페와 게시판에 저장된 템플릿을 게시합니다.</p><PublishActions cafes={cafes.data ?? []} enabled={data.capabilities.posting && !busy && data.session.state === 'logged_in'} publish={(id) => run(() => port.post(id))} /><dl><div><dt>중복 확인</dt><dd>같은 제목이 있으면 건너뜀</dd></div><div><dt>자동 게시</dt><dd>{data.settings.auto_post ? '켜짐' : '꺼짐'} · 카페별 승인 필요</dd></div><div><dt>오늘 남은 게시</dt><dd className="num">{Math.max(0, data.usage.daily_post_limit - data.usage.post_count)}회</dd></div></dl><Button variant="ghost" onClick={() => setTab('settings')}>게시 템플릿 편집</Button><p className="field-hint">수집한 글은 리드 목록의 ‘당근 카페’ 소스에서 확인하세요. 템플릿 제목 또는 seyj.kr 링크가 있는 글은 제외합니다.</p></aside></div> : null}
      {tab === 'jobs' ? <section aria-label="카페 실행 기록"><SectionHeading title="실행 기록" body="카페 작업의 진행 상태와 실패 이유를 확인하세요." />{jobs.data?.length ? jobs.data.map((job) => <article className="cafe-row cafe-record" key={job.id}><div><h3>{jobsLabel[job.kind]} <span className={`cafe-state${job.state === 'failed' ? ' cafe-state-excluded' : ''}`}>{jobsState[job.state]}</span></h3>{job.error_message ? <p>{job.error_message}</p> : null}</div><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString('ko-KR')}</time></article>) : <EmptyState title="아직 실행한 작업이 없습니다" body="세션 확인, 카페 찾기, 수집과 게시 작업의 결과가 여기에 남습니다." />}</section> : null}
      {tab === 'inquiries' ? <section aria-label="카페 문의"><SectionHeading title="문의함" body="웹에 보이는 댓글과 펼칠 수 있는 답글을 확인합니다." action={<Button icon={<RefreshCw size={15} />} disabled={busy || !data.capabilities.visible_comments || data.session.state !== 'logged_in'} onClick={() => run(() => port.action('comments'))}>댓글 지금 확인</Button>} />{inquiries.error ? <p role="alert">문의 조회 실패: {inquiries.error.message}</p> : null}{inquiries.data?.coverage?.filter((coverage) => coverage.needs_app_check).map((coverage) => <div className="cafe-coverage" role="status" key={coverage.attempt_id}><div><strong>앱에서 전체 확인 필요</strong><p>{coverage.cafe_name} · 웹에서 {coverage.read_count}개 읽음{coverage.reported_count !== null ? ` / 글에 표시된 댓글 ${coverage.reported_count}개` : ''}</p></div><Button size="sm" onClick={() => { void openExternal(coverage.post_url); }}>게시글 열기</Button></div>)}{inquiries.data?.items.map((item) => <Inquiry key={item.id} item={item} port={port} busy={command.isPending} run={run} openLead={onOpenLead} />)}{inquiries.data?.items.length === 0 ? <EmptyState title="아직 저장된 문의가 없습니다" body="게시한 글의 댓글을 확인하면 문의가 쌓입니다. 댓글이 모두 보이지 않는 글은 앱 확인 안내를 표시합니다." /> : null}</section> : null}
      {tab === 'attempts' ? <section aria-label="카페 게시 기록"><SectionHeading title="게시 기록" body="게시 결과와 중복으로 건너뛴 글을 확인하세요." />{attempts.data?.length ? attempts.data.map((item) => <article className="cafe-row cafe-record" key={item.id}><div><h3>{cafes.data?.find((c) => c.id === item.cafe_id)?.name ?? '카페'}</h3><p>{attemptLabels[item.status]} · 템플릿 버전 {item.template_version}</p><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('ko-KR')}</time>{item.error_message ? <p>{item.error_message}</p> : null}</div><div className="cafe-row-actions">{item.status === 'removed' ? <Button disabled={busy || !data.capabilities.posting} onClick={() => run(() => port.repost(item.id))}>삭제·숨김 글 직접 다시 게시</Button> : null}{item.post_url ? <Button icon={<ArrowUpRight size={14} />} onClick={() => { void openExternal(item.post_url); }}>게시글 열기</Button> : null}</div></article>) : <EmptyState title="첫 게시를 준비하세요" body="카페 목록에서 게시 대상과 게시판을 승인한 뒤 ‘지금 게시’를 실행하면 결과가 여기에 기록됩니다. 같은 제목의 글이 있으면 자동으로 건너뜁니다." />}</section> : null}
      {tab === 'settings' ? <><SectionHeading title="카페 설정" body="수집 범위, 실행 한도와 게시 내용을 관리하세요." /><div className="cafe-settings-grid"><CafeSettingsForm settings={data.settings} busy={busy} onSave={(patch) => run(() => port.settings(patch))} /><ScheduleSettings settings={data.settings} busy={busy} save={(patch) => run(() => port.settings(patch))} /></div>{template.data ? <TemplateEditor template={template.data} busy={command.isPending} save={(body) => run(() => port.updateTemplate(body))} /> : template.error ? <p role="alert">템플릿 조회 실패: {template.error.message}</p> : <p role="status">템플릿을 불러오는 중…</p>}</> : null}
      </section></div>
    </>}{!data.consent && error ? <p role="alert" className="cafe-error">{error}</p> : null}
  </div></main>;
}

function SectionHeading({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return <div className="cafe-section-heading"><div><h2>{title}</h2><p>{body}</p></div>{action}</div>;
}
function EmptyState({ title, body }: { title: string; body: ReactNode }) {
  return <div className="cafe-empty"><FolderOpen size={28} aria-hidden="true" /><h3>{title}</h3><p>{body}</p></div>;
}
function CafeSettingsForm({ settings, busy, onSave }: { settings: CafeSnapshot['settings']; busy: boolean; onSave: (patch: import('@contracts/index').Schemas['CafeSettingsPatch']) => void }) {
  const [keywords, setKeywords] = useState((settings.keywords ?? []).join(', '));
  const [interval, setInterval] = useState(settings.min_navigation_seconds);
  const [limit, setLimit] = useState(settings.daily_page_limit);
  const [approve, setApprove] = useState(settings.auto_approve);
  return <form className="cafe-settings" onSubmit={(e) => { e.preventDefault(); onSave({ keywords: keywords.split(',').map((s) => s.trim()).filter(Boolean), min_navigation_seconds: interval, daily_page_limit: limit, auto_approve: approve }); }}><h3>검색과 페이지 한도</h3><label>검색어 (쉼표로 구분)<input value={keywords} onChange={(e) => setKeywords(e.target.value)} /></label><div className="cafe-form-pair"><label>페이지 이동 최소 간격 (초)<input type="number" min={8} max={3600} required value={interval} onChange={(e) => setInterval(Number(e.target.value))} /></label><label>하루 페이지 이동 한도<input type="number" min={1} max={10000} required value={limit} onChange={(e) => setLimit(Number(e.target.value))} /></label></div><label className="cafe-checkbox"><input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} /> 가입과 명시적인 게시 허용 규칙이 확인된 카페 자동 승인</label><Button type="submit" disabled={busy}>카페 설정 저장</Button></form>;
}
function PublishActions({ cafes, enabled, publish }: { cafes: CafeEntry[]; enabled: boolean; publish: (id: string) => void }) {
  const [id, setId] = useState('');
  const targets = cafes.filter((c) => c.status === 'approved' && c.boards.some((b) => b.approved && b.post_allowed));
  return <div className="cafe-publish-form"><label>게시 대상<select value={id} onChange={(e) => setId(e.target.value)}><option value="">승인한 카페 선택</option>{targets.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><Button variant="primary" icon={<Send size={15} />} disabled={!enabled || !targets.some((c) => c.id === id)} onClick={() => publish(id)}>지금 게시</Button>{!targets.length ? <p className="field-hint">카페와 게시판을 모두 승인하면 선택할 수 있습니다.</p> : null}</div>;
}
