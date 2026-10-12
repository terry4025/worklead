import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import confirmed from '../../../contracts/fixtures/relation-confirmed.json';
import { resetDatabaseQueries } from '../app/databaseCache';
import { PortProvider } from '../app/PortContext';
import { applyDetail, qk, reloadRelation, useLeadMutation, useRelationMutation } from '../app/queries';
import { mapDetail } from '../data/live/mapper';
import { MockPort } from '../data/mock/mockPort';
import { PortError } from '../data/port';
import type { LeadDetail, RelationUpdate } from '../domain/model';
import { RelatedRecordsPanel } from '../features/detail/RelatedRecordsPanel';

const resources: { qc: QueryClient; port: MockPort }[] = [];
afterEach(() => { vi.restoreAllMocks(); resources.splice(0).forEach(({ qc, port }) => { qc.clear(); port.dispose(); }); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function harness() {
  const pair = [confirmed.lead, confirmed.related_lead].map((value) => {
    const lead = mapDetail(value as Parameters<typeof mapDetail>[0]);
    return { ...lead, related: lead.related.map((r) => ({ ...r, confirmed: false, dismissed: false, revision: 0 })) };
  });
  const left = pair[0]!; const right = pair[1]!; const relation = left.related[0]!;
  const port = new MockPort({ scenario: 'normal', latency: [0, 0], initialLeads: pair });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  qc.setQueryData(qk.lead(left.id), left); qc.setQueryData(qk.lead(right.id), right);
  resources.push({ qc, port });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}><PortProvider value={{ port, mode: 'demo', scenario: 'normal', setScenario: () => undefined }}>{children}</PortProvider></QueryClientProvider>;
  const vars = { id: left.id, relatedId: right.id, relationId: relation.id, input: { action: 'confirm' as const, expectedRevision: 0, expectedGeneration: relation.databaseGeneration } };
  const response = (revision = 1): RelationUpdate => ({
    lead: { ...left, related: left.related.map((r) => ({ ...r, confirmed: true, revision })) },
    relatedLead: { ...right, related: right.related.map((r) => ({ ...r, confirmed: true, revision })) },
  });
  function Host() {
    const lead = useQuery({ queryKey: qk.lead(left.id), queryFn: () => port.getLead(left.id), staleTime: Infinity }).data;
    return lead ? <RelatedRecordsPanel lead={lead} /> : null;
  }
  return { port, qc, wrapper, left, right, relation, vars, response, Host };
}

describe('관련 글 관계 화면', () => {
  it('후보 확인·연결 해제·다시 확인을 표시하며 키보드 포커스를 유지한다', async () => {
    const { port, qc, wrapper, Host, right } = harness();
    const update = vi.spyOn(port, 'updateRelation');
    render(<Host />, { wrapper });
    expect(screen.getByText(/원문·메모·영업 상태는 합치지 않으며/)).toBeTruthy();
    const confirmButton = screen.getByRole('button', { name: '관계 확인' });
    confirmButton.focus(); fireEvent.click(confirmButton);
    await screen.findByText('내가 관계 확인');
    const unlinkButton = screen.getByRole('button', { name: '연결 해제' });
    expect(document.activeElement).toBe(unlinkButton);
    expect(qc.getQueryData<LeadDetail>(qk.lead(right.id))?.related[0]?.confirmed).toBe(true);
    fireEvent.click(unlinkButton);
    const reconnect = await screen.findByRole('button', { name: '다시 연결 확인' });
    await waitFor(() => expect((reconnect as HTMLButtonElement).disabled).toBe(false));
    expect(document.activeElement).toBe(reconnect);
    fireEvent.click(reconnect);
    await screen.findByText('내가 관계 확인');
    expect(update.mock.calls.map(([, , input]) => input.action)).toEqual(['confirm', 'dismiss', 'confirm']);
    expect(update.mock.calls.map(([, , input]) => input.expectedRevision)).toEqual([0, 1, 2]);
  });

  it('후보에서 곧바로 해제한 버튼의 포커스를 다시 연결 버튼으로 옮긴다', async () => {
    const { wrapper, Host } = harness();
    render(<Host />, { wrapper });
    const unlinkButton = screen.getByRole('button', { name: '연결 해제' });
    unlinkButton.focus(); fireEvent.click(unlinkButton);
    const reconnect = await screen.findByRole('button', { name: '다시 연결 확인' });
    await waitFor(() => expect(document.activeElement).toBe(reconnect));
  });

  it('같은 tick의 반복 입력은 한 번만 POST하고 해당 관계 동작을 잠근다', async () => {
    const { port, wrapper, Host, response } = harness(); const pending = deferred<RelationUpdate>();
    const update = vi.spyOn(port, 'updateRelation').mockReturnValue(pending.promise);
    render(<Host />, { wrapper });
    const button = screen.getByRole('button', { name: '관계 확인' });
    act(() => { fireEvent.click(button); fireEvent.click(button); });
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(screen.getByRole('status').textContent).toBe('저장 중…');
    expect((screen.getByRole('button', { name: '연결 해제' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => pending.resolve(response()));
    expect(update).toHaveBeenCalledOnce();
  });

  it('응답 손실 뒤에는 자동 변경 재시도 없이 양쪽 GET으로 결과를 확인한다', async () => {
    const { port, wrapper, Host } = harness(); const original = port.updateRelation.bind(port);
    const update = vi.spyOn(port, 'updateRelation').mockImplementation(async (...args) => {
      await original(...args);
      throw new PortError({ kind: 'connection', code: 'connection_failed', message: 'fixture response lost', retryable: true });
    });
    const read = vi.spyOn(port, 'getLead');
    render(<Host />, { wrapper });
    const source = screen.getByRole('button', { name: '관계 확인' }); source.focus(); fireEvent.click(source);
    await screen.findByRole('alert');
    expect(screen.getByRole('alert').textContent).toContain('저장 결과를 확인해야');
    expect((screen.getByRole('button', { name: '관계 확인' }) as HTMLButtonElement).disabled).toBe(true);
    const reload = screen.getByRole('button', { name: '현재 관계 불러오기' });
    expect(document.activeElement).toBe(reload);
    fireEvent.click(reload);
    await screen.findByText('내가 관계 확인');
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(update).toHaveBeenCalledOnce();
    expect(new Set(read.mock.calls.map(([id]) => id)).size).toBe(2);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '연결 해제' }));
  });

  it('실제 충돌 코드는 현재 상태 재조회 안내로 처리하고 재조회 실패도 잠금을 유지한다', async () => {
    const { port, wrapper, Host } = harness();
    const update = vi.spyOn(port, 'updateRelation').mockRejectedValue(new PortError({ kind: 'api', code: 'relation_changed', message: 'fixture conflict' }));
    render(<Host />, { wrapper }); fireEvent.click(screen.getByRole('button', { name: '관계 확인' }));
    await screen.findByRole('alert'); expect(screen.getByRole('alert').textContent).toContain('관계 상태나 복원된 데이터가 바뀌었습니다');
    vi.spyOn(port, 'getLead').mockRejectedValue(new PortError({ kind: 'connection', code: 'connection_failed', message: 'offline' }));
    fireEvent.click(screen.getByRole('button', { name: '현재 관계 불러오기' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('현재 관계를 불러오지 못했습니다'));
    expect((screen.getByRole('button', { name: '관계 확인' }) as HTMLButtonElement).disabled).toBe(true);
    expect(update).toHaveBeenCalledOnce();
  });

  it('원천 기록만 있고 연결할 리드가 없는 후보는 변경 버튼을 비활성화한다', () => {
    const { wrapper, left } = harness();
    render(<RelatedRecordsPanel lead={{ ...left, related: left.related.map((r) => ({ ...r, leadId: null })) }} />, { wrapper });
    for (const button of screen.getAllByRole('button')) expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('연결할 리드가 없어 관계를 변경할 수 없습니다.')).toBeTruthy();
  });
});

describe('관계 변경과 상세 캐시 경쟁', () => {
  it('관계 저장 후 시작한 GET의 이전 메모 응답이 나중 메모 저장을 되돌리지 않는다', async () => {
    const { port, qc, wrapper, left, vars } = harness();
    const before = await port.updateLead(left.id, { memo: 'GET이 읽은 이전 메모' });
    applyDetail(qc, before);
    const snapshotReady = deferred<LeadDetail>(); const read = deferred<LeadDetail>();
    const original = port.getLead.bind(port);
    vi.spyOn(port, 'getLead').mockImplementation(async (id) => {
      const snapshot = await original(id);
      if (id !== left.id) return snapshot;
      snapshotReady.resolve(snapshot);
      return read.promise;
    });
    const query = renderHook(() => useQuery({ queryKey: qk.lead(left.id), queryFn: () => port.getLead(left.id), staleTime: Infinity }), { wrapper });
    const relation = renderHook(() => useRelationMutation(), { wrapper });
    const memo = renderHook(() => useLeadMutation((p, id, text: string) => p.updateLead(id, { memo: text })), { wrapper });
    await act(async () => { await relation.result.current.mutateAsync(vars); });
    const captured = await snapshotReady.promise;
    expect(captured).toMatchObject({ memo: 'GET이 읽은 이전 메모', related: [{ confirmed: true, revision: 1 }] });
    expect(qc.getQueryState(qk.lead(left.id))?.fetchStatus).toBe('fetching');
    await act(async () => { await memo.result.current.mutateAsync({ id: left.id, arg: 'GET 시작 뒤 저장한 새 메모' }); });
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))?.memo).toBe('GET 시작 뒤 저장한 새 메모');
    await act(async () => read.resolve(captured));
    await waitFor(() => expect(qc.getQueryState(qk.lead(left.id))?.fetchStatus).toBe('idle'));
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))).toMatchObject({ memo: 'GET 시작 뒤 저장한 새 메모', related: [{ confirmed: true, revision: 1 }] });
    expect((await original(left.id)).memo).toBe('GET 시작 뒤 저장한 새 메모');
    query.unmount(); relation.unmount(); memo.unmount();
  });

  it('메모 저장 후 상세 GET 취소를 기다리는 동안 복원되면 저장 응답을 적용하지 않는다', async () => {
    const { port, qc, wrapper, left } = harness(); const cancel = deferred<void>();
    const original = qc.cancelQueries.bind(qc);
    const cancellation = vi.spyOn(qc, 'cancelQueries').mockImplementationOnce(() => cancel.promise).mockImplementation(original);
    const memo = renderHook(() => useLeadMutation((p, id, text: string) => p.updateLead(id, { memo: text })), { wrapper });
    act(() => memo.result.current.mutate({ id: left.id, arg: '복원 전 저장 응답' }));
    await waitFor(() => expect(cancellation).toHaveBeenCalledOnce());
    expect((await port.getLead(left.id)).memo).toBe('복원 전 저장 응답');
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))?.memo).toBe(left.memo);
    await act(async () => resetDatabaseQueries(qc));
    const restored = { ...left, memo: '복원된 메모', related: left.related.map((r) => ({ ...r, databaseGeneration: r.databaseGeneration + 1 })) };
    qc.setQueryData(qk.lead(left.id), restored);
    const pages = { pages: [{ items: [{ id: left.id, title: '복원된 제목' }] }], pageParams: [null] };
    qc.setQueryData(['leads', 'all'], pages);
    await act(async () => cancel.resolve()); await waitFor(() => expect(memo.result.current.isSuccess).toBe(true));
    expect(qc.getQueryData(qk.lead(left.id))).toEqual(restored);
    expect(qc.getQueryData(['leads', 'all'])).toEqual(pages);
    memo.unmount();
  });

  it('관계 응답의 오래된 메모·영업·다른 관계는 적용하지 않고 양쪽 상태만 갱신한다', async () => {
    const { port, qc, wrapper, left, right, vars, response } = harness(); const pending = deferred<RelationUpdate>();
    vi.spyOn(port, 'updateRelation').mockReturnValue(pending.promise);
    const mutation = renderHook(() => useRelationMutation(), { wrapper });
    act(() => mutation.result.current.mutate(vars));
    await waitFor(() => expect(port.updateRelation).toHaveBeenCalledOnce());
    qc.setQueryData(qk.lead(left.id), { ...left, memo: '새 메모', salesStage: 'negotiating', related: [...left.related, { ...left.related[0]!, id: 'other-relation', revision: 7 }] });
    qc.setQueryData(qk.lead(right.id), { ...right, memo: '다른 리드 새 메모' });
    await act(async () => pending.resolve(response()));
    await waitFor(() => expect(mutation.result.current.isSuccess).toBe(true));
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))).toMatchObject({ memo: '새 메모', salesStage: 'negotiating' });
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))?.related.map((r) => r.revision)).toEqual([1, 7]);
    expect(qc.getQueryData<LeadDetail>(qk.lead(right.id))).toMatchObject({ memo: '다른 리드 새 메모', related: [{ confirmed: true }] });
    mutation.unmount();
  });

  it('먼저 시작한 메모 저장이 늦게 끝나도 최신 관계 revision을 되돌리지 않는다', async () => {
    const { qc, wrapper, left, vars } = harness(); const pending = deferred<LeadDetail>();
    const memo = renderHook(() => useLeadMutation(() => pending.promise), { wrapper });
    const relation = renderHook(() => useRelationMutation(), { wrapper });
    act(() => memo.result.current.mutate({ id: left.id, arg: 'saved memo' }));
    await act(async () => { await relation.result.current.mutateAsync(vars); });
    await act(async () => pending.resolve({ ...left, memo: 'saved memo' }));
    await waitFor(() => expect(memo.result.current.isSuccess).toBe(true));
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))).toMatchObject({ memo: 'saved memo', related: [{ confirmed: true, revision: 1 }] });
    memo.unmount(); relation.unmount();
  });

  it('열지 않은 다른 리드 상세 캐시를 전체 응답으로 생성하지 않는다', async () => {
    const { qc, wrapper, right, vars } = harness(); qc.removeQueries({ queryKey: qk.lead(right.id) });
    const mutation = renderHook(() => useRelationMutation(), { wrapper });
    await act(async () => { await mutation.result.current.mutateAsync(vars); });
    expect(qc.getQueryData(qk.lead(right.id))).toBeUndefined(); mutation.unmount();
  });

  it('복원 이전 변경의 늦은 성공은 양쪽 상세·목록 캐시를 수정하지 않는다', async () => {
    const { port, qc, wrapper, left, right, vars, response } = harness(); const pending = deferred<RelationUpdate>();
    vi.spyOn(port, 'updateRelation').mockReturnValue(pending.promise);
    const mutation = renderHook(() => useRelationMutation(), { wrapper });
    act(() => mutation.result.current.mutate(vars)); await waitFor(() => expect(port.updateRelation).toHaveBeenCalledOnce());
    await act(async () => resetDatabaseQueries(qc));
    const restored = [left, right].map((lead) => ({ ...lead, memo: '복원된 메모', related: lead.related.map((r) => ({ ...r, databaseGeneration: r.databaseGeneration + 1 })) }));
    restored.forEach((lead) => qc.setQueryData(qk.lead(lead.id), lead));
    const page = { pages: [{ items: [{ id: left.id, title: '복원된 제목' }] }], pageParams: [null] };
    qc.setQueryData(['leads', 'all'], page);
    await act(async () => pending.resolve(response())); await waitFor(() => expect(mutation.result.current.isSuccess).toBe(true));
    expect(qc.getQueryData(qk.lead(left.id))).toEqual(restored[0]); expect(qc.getQueryData(qk.lead(right.id))).toEqual(restored[1]);
    expect(qc.getQueryData(['leads', 'all'])).toEqual(page); mutation.unmount();
  });

  it('변경 전 GET 취소를 기다리는 사이 복원되면 POST 자체를 보내지 않는다', async () => {
    const { port, qc, wrapper, vars } = harness(); const cancel = deferred<void>();
    const original = qc.cancelQueries.bind(qc);
    const spy = vi.spyOn(qc, 'cancelQueries').mockImplementationOnce(() => cancel.promise).mockImplementationOnce(() => Promise.resolve()).mockImplementation(original);
    const update = vi.spyOn(port, 'updateRelation');
    const mutation = renderHook(() => useRelationMutation(), { wrapper });
    act(() => mutation.result.current.mutate(vars)); await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await act(async () => resetDatabaseQueries(qc));
    await act(async () => cancel.resolve());
    await waitFor(() => expect(mutation.result.current.isError).toBe(true));
    expect(update).not.toHaveBeenCalled(); mutation.unmount();
  });

  it('관계 변경 이전 진행 중이던 GET의 늦은 결과는 변경된 관계를 덮지 않는다', async () => {
    const { port, qc, wrapper, left, vars } = harness(); const read = deferred<LeadDetail>();
    const request = vi.fn().mockReturnValueOnce(read.promise).mockImplementation(() => port.getLead(left.id));
    const query = renderHook(() => useQuery({ queryKey: qk.lead(left.id), queryFn: request, staleTime: Infinity }), { wrapper });
    act(() => { void query.result.current.refetch(); });
    await waitFor(() => expect(request).toHaveBeenCalledOnce());
    expect(qc.getQueryState(qk.lead(left.id))?.fetchStatus).toBe('fetching');
    const mutation = renderHook(() => useRelationMutation(), { wrapper });
    await act(async () => { await mutation.result.current.mutateAsync(vars); });
    await act(async () => read.resolve(left));
    await waitFor(() => expect(mutation.result.current.isSuccess).toBe(true));
    expect(request).toHaveBeenCalledTimes(2);
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))?.related[0]?.revision).toBe(1);
    query.unmount(); mutation.unmount();
  });

  it('재시작으로 작아진 generation도 현재 읽기 응답으로 갱신하고 메모를 보존한다', async () => {
    const { port, qc, left, right, relation } = harness();
    const before = { ...left, memo: '유지', related: left.related.map((r) => ({ ...r, databaseGeneration: 900, revision: 99 })) };
    qc.setQueryData(qk.lead(left.id), before);
    const after = { ...left, related: left.related.map((r) => ({ ...r, databaseGeneration: 1, revision: 0 })) };
    vi.spyOn(port, 'getLead').mockImplementation(async (id) => id === left.id ? after : right);
    await reloadRelation(qc, port, left.id, right.id, relation.id);
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))).toMatchObject({ memo: '유지', related: [{ databaseGeneration: 1, revision: 0 }] });
    applyDetail(qc, { ...after, related: after.related.map((r) => ({ ...r, revision: 2 })) });
    applyDetail(qc, { ...after, memo: '최신 메모', related: after.related.map((r) => ({ ...r, revision: 1 })) });
    expect(qc.getQueryData<LeadDetail>(qk.lead(left.id))).toMatchObject({ memo: '최신 메모', related: [{ databaseGeneration: 1, revision: 2 }] });
  });

  it('새 관계 선택보다 오래된 성공 응답은 양쪽 최신 revision을 낮추지 않는다', async () => {
    const { port, qc, wrapper, left, right, vars, response } = harness(); const pending = deferred<RelationUpdate>();
    vi.spyOn(port, 'updateRelation').mockReturnValue(pending.promise);
    const mutation = renderHook(() => useRelationMutation(), { wrapper });
    act(() => mutation.result.current.mutate(vars)); await waitFor(() => expect(port.updateRelation).toHaveBeenCalledOnce());
    for (const lead of [left, right]) qc.setQueryData(qk.lead(lead.id), { ...lead, related: lead.related.map((r) => ({ ...r, confirmed: false, dismissed: true, revision: 2 })) });
    await act(async () => pending.resolve(response(1))); await waitFor(() => expect(mutation.result.current.isSuccess).toBe(true));
    for (const lead of [left, right]) expect(qc.getQueryData<LeadDetail>(qk.lead(lead.id))?.related[0]).toMatchObject({ confirmed: false, dismissed: true, revision: 2 });
    mutation.unmount();
  });

  it('복원 전에 시작한 현재 관계 GET이 늦게 끝나도 복원된 양쪽 상태는 유지한다', async () => {
    const { port, qc, left, right, relation } = harness(); const read = deferred<LeadDetail>();
    vi.spyOn(port, 'getLead').mockReturnValue(read.promise);
    const result = reloadRelation(qc, port, left.id, right.id, relation.id).catch((error: unknown) => error);
    await resetDatabaseQueries(qc);
    const restored = [left, right].map((lead) => ({ ...lead, memo: '복원된 메모', related: lead.related.map((r) => ({ ...r, revision: 6, databaseGeneration: r.databaseGeneration + 1 })) }));
    restored.forEach((lead) => qc.setQueryData(qk.lead(lead.id), lead));
    read.resolve({ ...left, related: left.related.map((r) => ({ ...r, confirmed: true, revision: 1 })) });
    await expect(result).resolves.toMatchObject({ code: 'relation_generation_conflict' });
    for (const lead of restored) expect(qc.getQueryData(qk.lead(lead.id))).toEqual(lead);
  });
});
