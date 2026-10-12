import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { usePort } from '../../app/PortContext';
import type { CafeEntry } from '../../domain/cafe';
import { Button } from '../../ui/Button';
import { StatusGlyph } from '../../ui/StatusMark';
import { openExternal } from '../../platform/external';
import './cafe.css';
import { TemplateEditor } from './TemplateEditor';
import { Inquiry } from './Inquiry';
import { ScheduleSettings } from './ScheduleSettings';

const states: Record<CafeEntry['status'], string> = { candidate: '판정 대기', needs_join: '직접 가입 필요', eligible: '승인 대기', approved: '승인됨', excluded: '제외됨' };
const jobsLabel = { check:'세션 확인', login:'로그인', discover:'카페 찾기', inspect:'규칙 다시 조사', collect:'글 수집', post:'게시', comments:'댓글 확인' };
const jobsState = { queued:'대기', running:'진행 중', succeeded:'완료', failed:'실패', cancelled:'취소' };

export function CafeWorkspace({ onOpenLead }: { onOpenLead?: (id: string) => void }) {
  const port = usePort().cafe;
  const qc = useQueryClient();
  const [tab, setTab] = useState<'cafes' | 'attempts' | 'inquiries' | 'jobs' | 'settings'>('cafes');
  const [url, setUrl] = useState('');
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState('');
  const snapshot = useQuery({ queryKey: ['cafe', 'status'], queryFn: () => port.status(), refetchInterval: 2000 });
  const cafes = useQuery({ queryKey: ['cafe', 'cafes'], queryFn: () => port.list(), refetchInterval: snapshot.data?.active_job ? 2000 : false });
  const jobs = useQuery({ queryKey: ['cafe', 'jobs'], queryFn: () => port.jobs(), refetchInterval: snapshot.data?.active_job ? 2000 : false });
  const template = useQuery({ queryKey: ['cafe', 'template'], queryFn: () => port.template() });
  const inquiries = useQuery({ queryKey: ['cafe', 'comments'], queryFn: () => port.comments() });
  const attempts = useQuery({ queryKey: ['cafe', 'attempts'], queryFn: () => port.attempts(), refetchInterval: snapshot.data?.active_job ? 2000 : false });
  const command = useMutation({ mutationFn: async (action: () => Promise<unknown>) => action(), onSuccess: () => { setError(''); void qc.invalidateQueries({ queryKey: ['cafe'] }); void qc.invalidateQueries({ queryKey: ['sources'] }); }, onError: (e) => setError(e.message) });
  const run = (action: () => Promise<unknown>) => command.mutate(action);
  const data = snapshot.data;
  const busy = command.isPending || Boolean(data?.active_job);
  if (snapshot.isPending) return <main tabIndex={-1} id="cafe-workspace" className="cafe-workspace" role="status">카페 상태를 불러오는 중…</main>;
  if (!data) return <main tabIndex={-1} id="cafe-workspace" className="cafe-workspace"><p role="alert">카페 상태 조회 실패: {snapshot.error?.message}</p><Button onClick={() => void snapshot.refetch()}>다시 불러오기</Button></main>;
  return <main tabIndex={-1} id="cafe-workspace" className="cafe-workspace">
    <div className="cafe-heading"><h1>카페</h1><span className="num">오늘 페이지 {data.usage.page_count}/{data.usage.daily_page_limit} · 게시 {data.usage.post_count}/{data.usage.daily_post_limit}</span></div>
    {!data.consent ? <section className="cafe-consent" aria-label="카페 기능 위험 안내"><h2>시작 전에 확인해 주세요</h2><p>{data.consent_text}</p><Button variant="primary" busy={command.isPending} onClick={() => run(async () => { await port.consent(data.consent_version); await port.action('check'); })}>위험 안내 확인</Button></section> : <>
      <section className="cafe-session" aria-label="카페 로그인 상태">
        <span><StatusGlyph tone={data.session.state === 'logged_in' ? 'good' : 'warn'} /> {!data.session.checked_at ? '세션 확인 전' : data.session.state === 'logged_in' ? '로그인 확인됨' : data.session.state === 'needs_user' ? '사용자 확인 필요 · 카페 작업 정지' : '로그인 필요'}</span>
        <Button disabled={busy} onClick={() => run(() => port.action('check'))}>세션 확인</Button>
        {data.session.checked_at && data.session.state !== 'logged_in' ? <Button variant="primary" disabled={busy} onClick={() => run(() => port.action('login'))}>브라우저 열어 로그인</Button> : null}
        <label><input type="checkbox" checked={data.settings.auto_post} disabled={!data.capabilities.posting || busy} onChange={(e) => run(() => port.settings({ auto_post: e.target.checked }))} /> 자동 게시</label>
      </section>
      {!data.capabilities.own_posts || !data.capabilities.full_comments ? <p className="cafe-limitation" role="status">내 글 대조·전체 댓글 조회 경로를 확보하지 못했습니다. 게시와 댓글 자동 확인은 중지되어 있습니다.</p> : null}
      <p className="field-hint">카페 글은 기존 리드 목록의 ‘당근 카페’ 소스로 연결됩니다. 내 글 제외·작성 시각을 확인하기 전 수집은 보류됩니다.</p>
      <Button disabled={busy || !data.capabilities.collection} onClick={() => run(() => port.action('collect'))}>카페 글 수집</Button>
      {data.active_job ? <p role="status">{data.active_job.kind === 'login' ? '열린 Edge에서 직접 로그인해 주세요.' : '카페 작업 진행 중'} · 페이지 이동 간격 {data.settings.min_navigation_seconds}초</p> : null}
      <nav className="cafe-tabs" aria-label="카페 보기">{([['cafes', '카페 목록'], ['attempts', '게시 기록'], ['inquiries', `문의${inquiries.data?.unread_count ? ` (${inquiries.data.unread_count})` : ''}`], ['jobs', '실행 기록'], ['settings', '카페 설정']] as const).map(([id, label]) => <Button key={id} pressed={tab === id} onClick={() => setTab(id)}>{label}</Button>)}</nav>
      {tab === 'cafes' ? <>
        <PublishActions cafes={cafes.data ?? []} enabled={data.capabilities.posting && !busy} publish={(id) => run(() => port.post(id))} />
        <div className="cafe-actions"><Button variant="primary" disabled={busy || data.session.state !== 'logged_in'} onClick={() => run(() => port.action('discover'))}>카페 찾기</Button><Button disabled={busy || data.session.state !== 'logged_in'} onClick={() => run(() => port.action('inspect'))}>등록 카페 다시 조사</Button><label>상태 <select value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">전체</option>{Object.entries(states).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label></div>
        <p className="field-hint">공개 카테고리에 표시된 카페를 검색어로 걸러냅니다. 검색·지역 조회 화면은 확인되지 않았습니다.</p>
        <form className="cafe-add" onSubmit={(e) => { e.preventDefault(); run(async () => { await port.add(url); setUrl(''); }); }}><label htmlFor="cafe-url">카페 URL 직접 추가</label><input id="cafe-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://cafe.daangn.com/카페이름" /><Button type="submit" disabled={busy}>추가</Button></form>
        {cafes.error ? <p role="alert">목록 조회 실패: {cafes.error.message}</p> : null}
        <div className="cafe-list">{cafes.data?.filter((row) => filter === 'all' || row.status === filter).map((row) => <article key={row.id} className="cafe-row"><div><h2>{row.name}</h2><span>{states[row.status]} · {row.category ?? '분류 미확인'} · {row.region ?? '지역 미확인'}</span><p>{row.status_reason ?? '규칙 조사 전'}</p>{row.rule_evidence ? <details><summary>공지 규칙 근거</summary><p className="cafe-evidence">{row.rule_evidence}</p></details> : null}{row.boards.map((board) => <div className="cafe-board" key={board.id}><span>{board.name} · {board.post_allowed ? '게시 허용 근거 있음' : '게시 허용 미확인'}</span><Button size="sm" disabled={busy || row.status !== 'approved' || !board.post_allowed} pressed={board.approved} onClick={() => run(() => port.board(board.id, !board.approved))}>게시판 {board.approved ? '승인 취소' : '승인'}</Button></div>)}</div><div className="cafe-row-actions"><Button size="sm" onClick={() => { void openExternal(row.url).catch((e: Error) => setError(e.message)); }}>카페 열기</Button><Button size="sm" disabled={busy || row.status !== 'eligible'} onClick={() => run(() => port.state(row.id, 'approved'))}>게시 대상 승인</Button><Button size="sm" disabled={busy} onClick={() => run(() => port.state(row.id, row.status === 'excluded' ? 'candidate' : 'excluded'))}>{row.status === 'excluded' ? '제외 취소' : '제외'}</Button></div></article>)}{!cafes.isPending && cafes.data?.filter((r) => filter === 'all' || r.status === filter).length === 0 ? <p className="cafe-empty">등록된 카페가 없습니다. 카페 찾기를 실행하거나 카페 홈 URL을 추가하세요.</p> : null}</div>
      </> : null}
      {tab === 'jobs' ? <section aria-label="카페 실행 기록">{jobs.data?.length ? jobs.data.map((job) => <article className="cafe-row" key={job.id}><span>{jobsLabel[job.kind]} · {jobsState[job.state]}</span><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString('ko-KR')}</time>{job.error_message ? <p>{job.error_message}</p> : null}</article>) : <p className="cafe-empty">실행 기록이 없습니다.</p>}</section> : null}
      {tab === 'inquiries' ? <section aria-label="카페 문의"><Button disabled={busy || !data.capabilities.full_comments} onClick={() => run(() => port.action('comments'))}>댓글 지금 확인</Button><p className="field-hint">전체 댓글 경로가 미확보이면 자동 확인을 중지합니다. 보이는 일부 댓글을 전체 조회 완료로 처리하지 않습니다.</p>{inquiries.error ? <p role="alert">문의 조회 실패: {inquiries.error.message}</p> : null}{inquiries.data?.items.map((item) => <Inquiry key={item.id} item={item} port={port} busy={command.isPending} run={run} openLead={onOpenLead} />)}{inquiries.data?.items.length === 0 ? <p className="cafe-empty">저장된 문의가 없습니다. 실제 전체 댓글 감시는 지원 경로 확보가 필요합니다.</p> : null}</section> : null}
      {tab === 'attempts' ? <section aria-label="카페 게시 기록">{attempts.data?.length ? attempts.data.map((item) => <article className="cafe-row" key={item.id}><div><h2>{cafes.data?.find((c) => c.id === item.cafe_id)?.name ?? '카페'}</h2><p>{({ pending: '게시 준비', posted: '게시 확인', already_exists: '기존 글 있어 건너뜀', posted_mismatch: '게시 내용 확인 필요', failed: '실패 · 자동 재시도 없음', removed: '삭제·숨김 확인 · 재게시 중지', skipped_rule: '규칙상 건너뜀' })[item.status]} · 템플릿 버전 {item.template_version}</p><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('ko-KR')}</time>{item.error_message ? <p>{item.error_message}</p> : null}</div>{item.status === 'removed' ? <Button disabled={busy || !data.capabilities.posting} onClick={() => run(() => port.repost(item.id))}>삭제·숨김 글 직접 다시 게시</Button> : null}{item.post_url ? <Button onClick={() => { void openExternal(item.post_url); }}>게시글 열기</Button> : null}</article>) : <p className="cafe-empty">게시 기록이 없습니다. 내 글 대조 경로 확보 후 승인한 카페에 게시할 수 있습니다.</p>}</section> : null}
      {tab === 'settings' ? <><CafeSettingsForm settings={data.settings} busy={busy} onSave={(patch) => run(() => port.settings(patch))} /><ScheduleSettings settings={data.settings} busy={busy} save={(patch) => run(() => port.settings(patch))} />{template.data ? <TemplateEditor template={template.data} busy={command.isPending} save={(body) => run(() => port.updateTemplate(body))} /> : template.error ? <p role="alert">템플릿 조회 실패: {template.error.message}</p> : <p role="status">템플릿을 불러오는 중…</p>}</> : null}
    </>}
    {error ? <p role="alert" className="cafe-error">{error}</p> : null}
  </main>;
}

function CafeSettingsForm({ settings, busy, onSave }: { settings: import('../../domain/cafe').CafeSnapshot['settings']; busy: boolean; onSave: (patch: import('@contracts/index').Schemas['CafeSettingsPatch']) => void }) {
  const [keywords, setKeywords] = useState((settings.keywords ?? []).join(', '));
  const [interval, setInterval] = useState(settings.min_navigation_seconds);
  const [limit, setLimit] = useState(settings.daily_page_limit);
  const [approve, setApprove] = useState(settings.auto_approve);
  return <form className="cafe-settings" onSubmit={(e) => { e.preventDefault(); onSave({ keywords: keywords.split(',').map((s) => s.trim()).filter(Boolean), min_navigation_seconds: interval, daily_page_limit: limit, auto_approve: approve }); }}><h2>검색과 페이지 한도</h2><label>검색어 (쉼표로 구분)<input value={keywords} onChange={(e) => setKeywords(e.target.value)} /></label><label>페이지 이동 최소 간격 (초)<input type="number" min={8} max={3600} required value={interval} onChange={(e) => setInterval(Number(e.target.value))} /></label><label>하루 페이지 이동 한도<input type="number" min={1} max={10000} required value={limit} onChange={(e) => setLimit(Number(e.target.value))} /></label><label className="cafe-checkbox"><input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} /> 가입과 명시적인 게시 허용 규칙이 확인된 카페 자동 승인</label><Button type="submit" variant="primary" disabled={busy}>카페 설정 저장</Button></form>;
}

function PublishActions({ cafes, enabled, publish }: { cafes: CafeEntry[]; enabled: boolean; publish: (id: string) => void }) {
  const [id, setId] = useState('');
  const targets = cafes.filter((c) => c.status === 'approved' && c.boards.some((b) => b.approved && b.post_allowed));
  return <div className="cafe-actions"><label>게시 대상 <select value={id} onChange={(e) => setId(e.target.value)}><option value="">승인한 카페 선택</option>{targets.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><Button disabled={!enabled || !targets.some((c) => c.id === id)} onClick={() => publish(id)}>지금 게시</Button></div>;
}
