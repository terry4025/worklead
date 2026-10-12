import { QueryClient, QueryClientProvider, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PortProvider } from '../app/PortContext';
import { qk, useLeadMutation } from '../app/queries';
import { useLiveEvents } from '../app/useLiveEvents';
import { resetDatabaseQueries } from '../app/databaseCache';
import { MockPort } from '../data/mock/mockPort';
import { PortError } from '../data/port';
import type { BackupInfo, BackupInspection, LeadDetail, RestoreResult, WorkleadEvent } from '../domain/model';
import { BackupPanel } from '../features/settings/BackupPanel';
import { SettingsDialog } from '../features/settings/SettingsDialog';
import { ToastProvider } from '../ui/Toasts';

const backup: BackupInfo = { id: 'b1', fileName: 'worklead-first.sqlite3', createdAt: '2026-09-30T03:00:00Z', sizeBytes: 24576 };
const inspected: BackupInspection = { backup, sha256: 'a'.repeat(64), revision: '0001', canRestore: true, problem: null, leadCount: 12 };
const restored: RestoreResult = { restoredBackupId: backup.id, safetyBackup: { ...backup, id: 'safety', fileName: 'worklead-before-restore.sqlite3' }, leadCount: 12, autoCollectionPaused: true };

function harness(mode: 'live' | 'demo' = 'live') {
  const p = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port: p, mode, scenario: null, setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  return { p, qc, wrapper };
}
function liveBackups(p: MockPort, items: BackupInfo[] = [backup]) {
  vi.spyOn(p, 'listBackups').mockResolvedValue({ directory: 'C:\\Worklead\\backups', items });
  vi.spyOn(p, 'inspectBackup').mockResolvedValue(inspected);
  vi.spyOn(p, 'restoreBackup').mockResolvedValue(restored);
}
async function selectBackup(id = backup.id) {
  fireEvent.change(await screen.findByLabelText('복원할 백업'), { target: { value: id } });
  await screen.findByText(/검증 완료/);
}
function confirmRestore() {
  fireEvent.click(screen.getByRole('checkbox', { name: /선택한 백업으로 데이터를 되돌리고/ }));
  fireEvent.click(screen.getByRole('button', { name: '선택한 백업으로 복원' }));
}
afterEach(() => { vi.restoreAllMocks(); Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal'); });

describe('백업·복원 설정', () => {
  it('데모는 실제 파일 백업처럼 성공하지 않고 미지원이라고 표시한다', async () => {
    const { p, qc, wrapper } = harness('demo');
    const list = vi.spyOn(p, 'listBackups');
    const view = render(<BackupPanel />, { wrapper });
    expect(screen.getByText(/데모에서는 실제 파일 백업·복원을 지원하지 않습니다/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '지금 백업 만들기' })).toBeNull();
    expect(list).not.toHaveBeenCalled();
    for (const request of [p.listBackups(), p.createBackup(), p.inspectBackup('b1'), p.restoreBackup('b1', { sha256: inspected.sha256, confirm: true })]) {
      await expect(request).rejects.toMatchObject({ kind: 'not_implemented', code: 'demo_backup_unsupported', retryable: false });
    }
    view.unmount(); qc.clear();
  });

  it('백업을 만들면 반환된 파일과 갱신된 목록을 표시한다', async () => {
    const { p, qc, wrapper } = harness();
    const list = vi.spyOn(p, 'listBackups').mockResolvedValueOnce({ directory: 'backup-dir', items: [] }).mockResolvedValue({ directory: 'backup-dir', items: [backup] });
    const create = vi.spyOn(p, 'createBackup').mockResolvedValue(backup);
    const view = render(<BackupPanel />, { wrapper });
    await screen.findByText('아직 백업이 없습니다.');
    fireEvent.click(screen.getByRole('button', { name: '지금 백업 만들기' }));
    await screen.findByText(`백업을 만들었습니다: ${backup.fileName}`);
    await screen.findByLabelText('복원할 백업');
    expect(create).toHaveBeenCalledOnce(); expect(list).toHaveBeenCalledTimes(2);
    view.unmount(); qc.clear();
  });

  it('검증만으로 복원하지 않으며 확인 뒤 검증된 해시로 한 번 실행한다', async () => {
    const { p, qc, wrapper } = harness(); liveBackups(p);
    let finish!: (r: RestoreResult) => void;
    const restore = vi.mocked(p.restoreBackup).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const cachedKeys = [['leads', 'old'], ['lead', 'old'], ['settings'], ['bootstrap'], ['sales'], ['sources'], ['runs'], ['counts']];
    cachedKeys.forEach((key) => qc.setQueryData(key, { old: true }));
    const view = render(<BackupPanel />, { wrapper });
    await selectBackup();
    const button = screen.getByRole('button', { name: '선택한 백업으로 복원' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true); expect(restore).not.toHaveBeenCalled();
    expect(screen.getByText(/대기·진행 중인 작업을 취소/)).toBeTruthy();
    expect(screen.getByText(/오늘 요청 사용량은 유지합니다/)).toBeTruthy();
    confirmRestore(); fireEvent.click(button);
    await waitFor(() => expect(restore).toHaveBeenCalledOnce());
    expect(restore).toHaveBeenCalledWith(backup.id, { sha256: inspected.sha256, confirm: true });
    expect(button.disabled).toBe(true);
    await act(async () => finish(restored));
    await screen.findByText(/복원했습니다. 리드 12건/);
    for (const key of cachedKeys) expect(qc.getQueryData(key)).toBeUndefined();
    expect(screen.getByText(/안전 백업: worklead-before-restore/)).toBeTruthy();
    view.unmount(); qc.clear();
  });

  it('검증에 실패한 백업은 복원 확인과 실행을 제공하지 않는다', async () => {
    const { p, qc, wrapper } = harness(); liveBackups(p);
    vi.mocked(p.inspectBackup).mockResolvedValue({ ...inspected, canRestore: false, problem: '데이터 버전이 호환되지 않습니다.', leadCount: null });
    const view = render(<BackupPanel />, { wrapper });
    fireEvent.change(await screen.findByLabelText('복원할 백업'), { target: { value: backup.id } });
    await screen.findByText('데이터 버전이 호환되지 않습니다.');
    expect(screen.queryByRole('button', { name: '선택한 백업으로 복원' })).toBeNull();
    expect(p.restoreBackup).not.toHaveBeenCalled();
    view.unmount(); qc.clear();
  });

  it('백업을 바꾸면 이전 확인을 취소하고 새로 검증한 해시를 사용한다', async () => {
    const { p, qc, wrapper } = harness();
    const second = { ...backup, id: 'b2', fileName: 'worklead-second.sqlite3' }; liveBackups(p, [backup, second]);
    vi.mocked(p.inspectBackup).mockImplementation(async (id) => ({ ...inspected, backup: id === backup.id ? backup : second, sha256: id === backup.id ? inspected.sha256 : 'b'.repeat(64) }));
    const view = render(<BackupPanel />, { wrapper });
    await selectBackup();
    expect(screen.getByText(`선택한 백업: ${backup.fileName}`)).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: /선택한 백업으로 데이터를 되돌리고/ }));
    fireEvent.change(screen.getByLabelText('복원할 백업'), { target: { value: second.id } });
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /선택한 백업으로 데이터를 되돌리고/ })).toHaveProperty('checked', false));
    await waitFor(() => expect(p.inspectBackup).toHaveBeenCalledWith(second.id));
    await waitFor(() => expect(screen.queryByText('선택한 백업 검증 중…')).toBeNull());
    expect(screen.getByText(`선택한 백업: ${second.fileName}`)).toBeTruthy();
    expect(screen.queryByText(`선택한 백업: ${backup.fileName}`)).toBeNull();
    confirmRestore();
    await waitFor(() => expect(p.restoreBackup).toHaveBeenCalledWith(second.id, { sha256: 'b'.repeat(64), confirm: true }));
    view.unmount(); qc.clear();
  });

  it('maintenance_busy 오류는 진행 작업 후 사용자가 다시 시도하게 한다', async () => {
    const { p, qc, wrapper } = harness(); liveBackups(p, []);
    const create = vi.spyOn(p, 'createBackup').mockRejectedValueOnce(new PortError({ kind: 'api', code: 'maintenance_busy', message: '다른 데이터 작업이 진행 중입니다.', retryable: false })).mockResolvedValue(backup);
    const view = render(<BackupPanel />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: '지금 백업 만들기' }));
    await screen.findByText('진행 중인 작업이 끝난 뒤 다시 시도하세요.');
    expect(create).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));
    await screen.findByText(`백업을 만들었습니다: ${backup.fileName}`);
    expect(create).toHaveBeenCalledTimes(2);
    view.unmount(); qc.clear();
  });

  it('복원 뒤 재조회 실패는 복원을 반복하지 않고 화면 조회만 다시 실행한다', async () => {
    const { p, qc, wrapper } = harness(); liveBackups(p);
    qc.setQueryData(['settings'], 'before-restore');
    let fail = true;
    const settings = renderHook(() => useQuery({ queryKey: ['settings'], staleTime: Infinity, queryFn: async () => { if (fail) throw new Error('offline'); return 'after-restore'; } }), { wrapper });
    const view = render(<BackupPanel />, { wrapper });
    await selectBackup(); confirmRestore();
    await screen.findByText('복원은 완료했지만 화면 데이터를 다시 불러오지 못했습니다.');
    expect(p.restoreBackup).toHaveBeenCalledOnce();
    expect(qc.getQueryData(['settings'])).toBeUndefined();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: '화면 데이터 다시 불러오기' }));
    await waitFor(() => expect(settings.result.current.data).toBe('after-restore'));
    expect(p.restoreBackup).toHaveBeenCalledOnce();
    view.unmount(); settings.unmount(); qc.clear();
  });

  it('백업 파일 변경 오류는 기존 동의를 취소하고 새 해시로 재검증하게 한다', async () => {
    const { p, qc, wrapper } = harness(); liveBackups(p);
    vi.mocked(p.restoreBackup).mockRejectedValueOnce(new PortError({ kind: 'api', code: 'backup_changed', message: '백업 파일이 변경되었습니다.' })).mockResolvedValue(restored);
    const view = render(<BackupPanel />, { wrapper });
    await selectBackup(); confirmRestore();
    await screen.findByText('백업 파일이 변경되었습니다.');
    expect(screen.queryByRole('button', { name: '선택한 백업으로 복원' })).toBeNull();
    expect(p.restoreBackup).toHaveBeenCalledOnce();
    vi.mocked(p.inspectBackup).mockResolvedValue({ ...inspected, sha256: 'c'.repeat(64) });
    fireEvent.click(screen.getByRole('button', { name: '백업 다시 검증' }));
    const check = await screen.findByRole('checkbox', { name: /선택한 백업으로 데이터를 되돌리고/ });
    expect(check).toHaveProperty('checked', false);
    confirmRestore();
    await waitFor(() => expect(p.restoreBackup).toHaveBeenLastCalledWith(backup.id, { sha256: 'c'.repeat(64), confirm: true }));
    view.unmount(); qc.clear();
  });

  it('복원 응답 유실은 결과 미확인으로 표시하고 조회 재시도에서 POST를 반복하지 않는다', async () => {
    const { p, qc, wrapper } = harness(); liveBackups(p);
    vi.mocked(p.restoreBackup).mockRejectedValue(new PortError({ kind: 'connection', code: 'connection_failed', message: '응답을 받지 못했습니다.', retryable: true }));
    const view = render(<BackupPanel />, { wrapper });
    await selectBackup(); confirmRestore();
    await screen.findByText(/복원 결과를 확인하지 못했습니다/);
    expect(p.restoreBackup).toHaveBeenCalledOnce();
    expect(screen.queryByRole('button', { name: '다시 시도' })).toBeNull();
    expect(screen.queryByRole('button', { name: '선택한 백업으로 복원' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '화면 데이터 다시 불러오기' }));
    await waitFor(() => expect(p.listBackups).toHaveBeenCalledTimes(2));
    expect(p.restoreBackup).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '백업 다시 검증' }));
    const check = await screen.findByRole('checkbox', { name: /선택한 백업으로 데이터를 되돌리고/ });
    expect(check).toHaveProperty('checked', false);
    expect(p.restoreBackup).toHaveBeenCalledOnce();
    view.unmount(); qc.clear();
  });

  it('bootstrap 초기화로 설정 화면이 내려가도 복원 완료·안전 백업 알림은 남는다', async () => {
    const { p, qc, wrapper: BaseWrapper } = harness(); liveBackups(p);
    qc.setQueryData(['bootstrap'], 'before-restore');
    let finish!: (value: string) => void;
    const boot = vi.fn().mockReturnValue(new Promise<string>((resolve) => { finish = resolve; }));
    const wrapper = ({ children }: { children: ReactNode }) => <BaseWrapper><ToastProvider>{children}</ToastProvider></BaseWrapper>;
    function Gate() {
      const q = useQuery({ queryKey: ['bootstrap'], queryFn: boot, staleTime: Infinity });
      return q.isPending ? <p>복원 후 연결 확인 중</p> : <BackupPanel />;
    }
    const view = render(<Gate />, { wrapper });
    await selectBackup(); confirmRestore();
    await screen.findByText('복원 후 연결 확인 중');
    expect(screen.queryByLabelText('복원할 백업')).toBeNull();
    expect(screen.getByText(/복원했습니다. 리드 12건.*복원 전 안전 백업: worklead-before-restore/)).toBeTruthy();
    await act(async () => finish('after-restore'));
    view.unmount(); qc.clear();
  });

  it('설정 화면은 복원 전 편집값 대신 복원된 설정을 보여 주고 저장한다', async () => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
    const { p, qc, wrapper } = harness(); liveBackups(p);
    const original = await p.getSettings();
    const after = structuredClone(original); after.profile.weeklyHours = 7;
    let current = original;
    vi.spyOn(p, 'getSettings').mockImplementation(async () => current);
    vi.mocked(p.restoreBackup).mockImplementation(async () => { current = after; return restored; });
    const save = vi.spyOn(p, 'updateSettings');
    const view = render(<SettingsDialog open onClose={() => undefined} fontScale="normal" setFontScale={() => undefined} theme="system" setTheme={() => undefined} />, { wrapper });
    fireEvent.change(await screen.findByLabelText('주당 가용 시간'), { target: { value: '99' } });
    await selectBackup(); confirmRestore();
    await screen.findByText(/복원했습니다. 리드 12건/);
    await waitFor(() => expect(screen.getByLabelText('주당 가용 시간')).toHaveProperty('value', '7'));
    fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ profile: expect.objectContaining({ weeklyHours: 7 }) })));
    view.unmount(); qc.clear();
  });

  it('복원 전 시작한 설정 저장 응답은 복원된 설정을 다시 덮지 않는다', async () => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
    const { p, qc, wrapper } = harness(); liveBackups(p);
    const original = await p.getSettings();
    const after = structuredClone(original); after.profile.weeklyHours = 7;
    let current = original;
    vi.spyOn(p, 'getSettings').mockImplementation(async () => current);
    let finish!: (s: typeof original) => void;
    const save = vi.spyOn(p, 'updateSettings').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const view = render(<SettingsDialog open onClose={() => undefined} fontScale="normal" setFontScale={() => undefined} theme="system" setTheme={() => undefined} />, { wrapper });
    fireEvent.change(await screen.findByLabelText('주당 가용 시간'), { target: { value: '99' } });
    fireEvent.click(screen.getByRole('button', { name: /^저장$/ }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    current = after;
    await act(async () => resetDatabaseQueries(qc));
    const late = structuredClone(original); late.profile.weeklyHours = 99;
    await act(async () => finish(late));
    await waitFor(() => expect(screen.getByLabelText('주당 가용 시간')).toHaveProperty('value', '7'));
    expect(qc.getQueryData<typeof original>(qk.settings)?.profile.weeklyHours).toBe(7);
    view.unmount(); qc.clear();
  });
});

describe('데이터 복원 이벤트 캐시 초기화', () => {
  it('stream.reset은 모든 DB 캐시를 비우고 활성 설정·bootstrap·영업 조회를 다시 한다', async () => {
    const { p, qc, wrapper } = harness();
    let emit!: (e: WorkleadEvent) => void;
    vi.spyOn(p, 'subscribe').mockImplementation((event) => { emit = event; return () => undefined; });
    const inactive = [['leads', 'old'], ['lead', 'old'], ['sources'], ['runs'], ['counts']];
    const active = ['settings', 'bootstrap', 'sales'];
    [...inactive, ...active.map((key) => [key])].forEach((key) => qc.setQueryData(key, 'old'));
    const fetches = active.map((key) => vi.fn(async () => `restored-${key}`));
    const queries = renderHook(() => active.map((key, i) => useQuery({ queryKey: [key], queryFn: fetches[i]!, staleTime: Infinity })), { wrapper });
    const events = renderHook(() => useLiveEvents(() => undefined), { wrapper });
    act(() => { emit({ id: 'created', seq: 100, at: backup.createdAt, type: 'lead.created', leadId: 'x', recommendation: 'recommended' }); emit({ id: 'reset', seq: 1, at: backup.createdAt, type: 'stream.reset' }); });
    await waitFor(() => expect(queries.result.current.map((q) => q.data)).toEqual(active.map((key) => `restored-${key}`)));
    for (const key of inactive) expect(qc.getQueryData(key)).toBeUndefined();
    fetches.forEach((fetch) => expect(fetch).toHaveBeenCalledOnce());
    expect(events.result.current.newLeads).toBe(0); expect(events.result.current.listStale).toBe(false);
    events.unmount(); queries.unmount(); qc.clear();
  });

  it('무한 목록은 복원 전 여러 페이지를 지우고 첫 페이지부터 다시 읽는다', async () => {
    const { qc, wrapper } = harness();
    const key = ['leads', 'all'];
    qc.setQueryData(key, { pages: ['old-first', 'old-next'], pageParams: [null, 'old-cursor'] });
    const get = vi.fn(async ({ pageParam }: { pageParam: string | null }) => pageParam === null ? 'restored-first' : 'wrong-old-next');
    const list = renderHook(() => useInfiniteQuery({ queryKey: key, staleTime: Infinity, initialPageParam: null as string | null, queryFn: get, getNextPageParam: () => 'restored-cursor' }), { wrapper });
    await act(async () => resetDatabaseQueries(qc));
    await waitFor(() => expect(list.result.current.data?.pages).toEqual(['restored-first']));
    expect(list.result.current.data?.pageParams).toEqual([null]);
    expect(get).toHaveBeenCalledOnce();
    list.unmount(); qc.clear();
  });

  it('복원 전 직접 시작한 상세 재조회가 늦게 끝나도 복원된 캐시를 덮지 않는다', async () => {
    const { p, qc, wrapper } = harness();
    const page = await p.listLeads({ queue: 'all', filter: { keyword: '', sourceId: null, category: null, remote: 'any', recruit: 'any', intent: 'any', sort: 'priority' }, cursor: null, limit: 1 });
    const lead = await p.getLead(page.items[0]!.id);
    qc.setQueryData(qk.lead(lead.id), lead);
    let finish!: (d: LeadDetail) => void;
    vi.spyOn(p, 'getLead').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    let emit!: (e: WorkleadEvent) => void;
    vi.spyOn(p, 'subscribe').mockImplementation((event) => { emit = event; return () => undefined; });
    const events = renderHook(() => useLiveEvents(() => undefined), { wrapper });
    act(() => emit({ id: 'update', seq: 99, at: backup.createdAt, type: 'lead.updated', leadId: lead.id }));
    await act(async () => { emit({ id: 'reset', seq: 1, at: backup.createdAt, type: 'stream.reset' }); });
    qc.setQueryData(qk.lead(lead.id), { ...lead, memo: 'restored memo' });
    await act(async () => finish({ ...lead, memo: 'pre-restore response' }));
    expect(qc.getQueryData<LeadDetail>(qk.lead(lead.id))?.memo).toBe('restored memo');
    events.unmount(); qc.clear();
  });

  it('복원 전 메모 저장의 늦은 성공 응답은 복원된 상세·목록을 덮지 않는다', async () => {
    const { p, qc, wrapper } = harness();
    const page = await p.listLeads({ queue: 'all', filter: { keyword: '', sourceId: null, category: null, remote: 'any', recruit: 'any', intent: 'any', sort: 'priority' }, cursor: null, limit: 1 });
    const lead = await p.getLead(page.items[0]!.id);
    let finish!: (d: LeadDetail) => void;
    const save = vi.fn().mockReturnValue(new Promise<LeadDetail>((resolve) => { finish = resolve; }));
    const mutation = renderHook(() => useLeadMutation((_port, _id, _memo: string) => save()), { wrapper });
    act(() => mutation.result.current.mutate({ id: lead.id, arg: 'saved before restore' }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    await act(async () => resetDatabaseQueries(qc));
    qc.setQueryData(qk.lead(lead.id), { ...lead, memo: 'restored memo' });
    const listKey = ['leads', 'all'];
    const restoredPages = { pages: [{ ...page, items: [{ ...page.items[0], title: 'restored title' }] }], pageParams: [null] };
    qc.setQueryData(listKey, restoredPages);
    await act(async () => finish({ ...lead, memo: 'saved before restore', title: 'old response title' }));
    await waitFor(() => expect(mutation.result.current.isSuccess).toBe(true));
    expect(qc.getQueryData<LeadDetail>(qk.lead(lead.id))?.memo).toBe('restored memo');
    expect(qc.getQueryData(listKey)).toEqual(restoredPages);
    mutation.unmount(); qc.clear();
  });
});
