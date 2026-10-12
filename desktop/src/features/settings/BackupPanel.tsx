import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { usePortCtx } from '../../app/PortContext';
import { resetDatabaseQueries } from '../../app/databaseCache';
import { toPortError } from '../../data/port';
import { formatCount, formatFull } from '../../domain/format';
import type { RestoreResult } from '../../domain/model';
import { Button } from '../../ui/Button';
import { useToast } from '../../ui/Toasts';

function restoreNotice(result: RestoreResult): string {
  return `복원했습니다. 리드 ${formatCount(result.leadCount)}건 · 복원 전 안전 백업: ${result.safetyBackup.fileName}. 자동 수집과 AI 분석은 꺼졌습니다. 수집 화면에서 자동 수집을 직접 다시 켜고, AI 설정에서 전송·요금 확인 후 AI를 다시 켜세요.`;
}

function uncertainRestore(error: unknown): boolean {
  const code = toPortError(error).code;
  return code !== 'maintenance_busy' && code !== 'restore_failed' && code !== 'backup_changed' && code !== 'invalid_backup' && code !== 'backup_not_found' && code !== 'validation_error';
}

function BackupError({ error, retry }: { error: unknown; retry: () => void }) {
  const e = toPortError(error);
  const canRetry = e.retryable || e.code === 'maintenance_busy';
  return (
    <div role="alert">
      <p className="field-error">{e.message}</p>
      {canRetry ? <p className="field-hint">진행 중인 작업이 끝난 뒤 다시 시도하세요.</p> : null}
      {canRetry ? <Button size="sm" onClick={retry}>다시 시도</Button> : null}
    </div>
  );
}

export function BackupPanel({ onRestored, onBusyChange, disabled = false }: {
  onRestored?: (result: RestoreResult, refreshFailed: boolean) => void;
  onBusyChange?: (busy: boolean) => void;
  disabled?: boolean;
}) {
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const [selectedId, setSelectedId] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [mustReinspect, setMustReinspect] = useState(false);
  const operationLocked = useRef(false);
  const backups = useQuery({ queryKey: ['backups'], queryFn: () => port.listBackups(), enabled: mode === 'live', retry: false });
  const inspection = useQuery({
    queryKey: ['backup-inspection', selectedId],
    queryFn: () => port.inspectBackup(selectedId),
    enabled: mode === 'live' && !!selectedId,
    retry: false,
  });
  const create = useMutation({
    mutationFn: () => port.createBackup(),
    onSuccess: (b) => {
      setNotice(`백업을 만들었습니다: ${b.fileName}`);
      void qc.invalidateQueries({ queryKey: ['backups'] });
    },
    onSettled: () => { operationLocked.current = false; },
  });
  const restore = useMutation({
    mutationFn: async ({ id, sha256 }: { id: string; sha256: string }) => {
      const result = await port.restoreBackup(id, { sha256, confirm: true });
      // ToastProvider lives outside Gate, so this survives Workspace unmount
      // while bootstrap is cleared and refetched after the database restore.
      toast.show({ tone: 'good', text: restoreNotice(result) }, 60_000);
      // A failed refresh must not turn a completed restore into a failed restore
      // or offer another destructive restore as the refresh retry.
      try {
        await resetDatabaseQueries(qc);
        return { result, refreshFailed: false };
      } catch {
        return { result, refreshFailed: true };
      }
    },
    onSuccess: ({ result, refreshFailed: failed }) => {
      setSelectedId('');
      setConfirmed(false);
      setRefreshFailed(failed);
      setNotice(restoreNotice(result));
      if (failed) toast.show({ tone: 'warn', text: '복원은 완료했지만 화면 데이터를 다시 불러오지 못했습니다.', action: { label: '화면 다시 불러오기', run: () => { void resetDatabaseQueries(qc).catch(() => toast.show({ tone: 'bad', text: '화면 데이터를 불러오지 못했습니다. 연결을 확인해 주세요.' })); } } }, 60_000);
      onRestored?.(result, failed);
    },
    onError: (error) => {
      setConfirmed(false);
      setMustReinspect(true);
      if (uncertainRestore(error)) toast.show({ tone: 'warn', text: '복원 결과를 확인하지 못했습니다. 복원을 반복하지 말고 화면 데이터를 다시 불러와 현재 상태를 확인하세요.', action: { label: '화면 다시 불러오기', run: () => { void resetDatabaseQueries(qc).catch(() => toast.show({ tone: 'bad', text: '화면 데이터를 불러오지 못했습니다. 연결을 확인해 주세요.' })); } } }, 60_000);
    },
    onSettled: () => { operationLocked.current = false; },
  });
  const refresh = useMutation({
    mutationFn: () => resetDatabaseQueries(qc),
    onSuccess: () => setRefreshFailed(false),
  });
  const operationBusy = create.isPending || restore.isPending || refresh.isPending;
  const busy = disabled || operationBusy;
  useEffect(() => {
    onBusyChange?.(operationBusy);
    return () => onBusyChange?.(false);
  }, [operationBusy, onBusyChange]);
  useEffect(() => { setConfirmed(false); }, [selectedId, inspection.dataUpdatedAt]);

  const verified = inspection.data?.backup.id === selectedId ? inspection.data : null;
  const canRestore = !!verified?.canRestore && !inspection.isFetching && !inspection.isError && !mustReinspect;
  const doCreate = () => {
    if (busy || operationLocked.current) return;
    operationLocked.current = true;
    setNotice(null);
    create.mutate();
  };
  const doRestore = () => {
    if (busy || operationLocked.current || !confirmed || !canRestore || !verified) return;
    operationLocked.current = true;
    restore.mutate({ id: selectedId, sha256: verified.sha256 });
  };
  const retryInspection = () => {
    if (busy) return;
    setConfirmed(false);
    void inspection.refetch().then((r) => { if (r.isSuccess) { setMustReinspect(false); restore.reset(); } });
  };

  return (
    <fieldset>
      <legend>백업·복원</legend>
      {mode === 'demo' ? <p className="field-hint">데모에서는 실제 파일 백업·복원을 지원하지 않습니다. 실제 데이터 모드에서 사용할 수 있습니다.</p> : (
        <>
          <p className="field-hint">현재 리드·메모·영업 기록·설정을 파일로 백업합니다.</p>
          <div className="field-inline">
            <Button busy={create.isPending} disabled={busy} onClick={doCreate}>지금 백업 만들기</Button>
            <Button size="sm" disabled={busy || backups.isFetching} onClick={() => void backups.refetch()}>백업 목록 새로고침</Button>
          </div>
          {backups.data ? <p className="field-hint">저장 위치: {backups.data.directory}</p> : null}
          {backups.isPending ? <p className="muted-text">백업 목록 불러오는 중…</p> : null}
          {backups.isError ? <BackupError error={backups.error} retry={() => void backups.refetch()} /> : null}
          {create.isError ? <BackupError error={create.error} retry={doCreate} /> : null}
          {backups.data?.items.length === 0 ? <p className="muted-text">아직 백업이 없습니다.</p> : null}
          {backups.data?.items.length ? (
            <label className="field">
              <span className="field-label">복원할 백업</span>
              <select value={selectedId} disabled={busy} onChange={(e) => { setSelectedId(e.target.value); setConfirmed(false); setMustReinspect(false); restore.reset(); setNotice(null); }}>
                <option value="">백업 선택</option>
                {backups.data.items.map((b) => <option key={b.id} value={b.id}>{formatFull(b.createdAt)} · {b.fileName} · {formatCount(b.sizeBytes)}바이트</option>)}
              </select>
            </label>
          ) : null}
          {selectedId && inspection.isFetching ? <p className="muted-text">선택한 백업 검증 중…</p> : null}
          {selectedId && inspection.isError ? <BackupError error={inspection.error} retry={retryInspection} /> : null}
          {verified ? (
            <>
              <p className="field-hint">{verified.canRestore ? '검증 완료' : '복원할 수 없는 백업'} · 리드 {verified.leadCount === null ? '확인 불가' : `${formatCount(verified.leadCount)}건`}{verified.revision ? ` · 데이터 버전 ${verified.revision}` : ''}</p>
              <p className="field-hint">선택한 백업: {verified.backup.fileName}</p>
              {verified.problem ? <p className="field-error" role="alert">{verified.problem}</p> : null}
              {canRestore ? (
                <>
                  <p className="field-hint">복원하면 리드·메모·영업 기록·설정이 선택한 백업 시점으로 돌아갑니다. 복원 직전에 현재 데이터의 안전 백업을 만듭니다. 오늘 요청 사용량은 유지합니다. AI 비용 기록·OS에 저장한 키도 유지합니다. 자동 수집과 AI 분석을 끄고 대기·진행 중인 작업을 취소합니다. 수집 화면에서 자동 수집을 직접 다시 켜고, AI 설정에서 전송·요금 확인 후 AI를 다시 켜세요.</p>
                  <label className="toggle">
                    <input type="checkbox" checked={confirmed} disabled={busy} onChange={(e) => setConfirmed(e.target.checked)} />
                    <span>선택한 백업으로 데이터를 되돌리고 자동 수집을 끄는 데 동의합니다</span>
                  </label>
                  <Button variant="danger" busy={restore.isPending} disabled={!confirmed || busy} onClick={doRestore}>선택한 백업으로 복원</Button>
                </>
              ) : null}
            </>
          ) : null}
          {restore.isError ? (
            <div role="alert">
              <p className="field-error">{toPortError(restore.error).message}</p>
              {uncertainRestore(restore.error) ? <><p className="field-hint">복원 결과를 확인하지 못했습니다. 화면 데이터를 다시 불러와 현재 상태를 확인하세요. 새로 복원하려면 백업을 다시 검증하고 다시 동의해야 합니다.</p><Button size="sm" busy={refresh.isPending} disabled={busy} onClick={() => refresh.mutate()}>화면 데이터 다시 불러오기</Button></> : <p className="field-hint">{toPortError(restore.error).code === 'maintenance_busy' ? '진행 중인 작업이 끝난 뒤 다시 시도하세요. ' : ''}백업을 다시 검증하고 동의해 주세요.</p>}
              <Button size="sm" disabled={busy || inspection.isFetching} onClick={retryInspection}>백업 다시 검증</Button>
            </div>
          ) : null}
          {notice && (!restore.isSuccess || !onRestored) ? <p className="field-hint" role="status">{notice}</p> : null}
          {refreshFailed && !onRestored ? <><p className="field-error" role="alert">복원은 완료했지만 화면 데이터를 다시 불러오지 못했습니다.</p><Button size="sm" busy={refresh.isPending} onClick={() => refresh.mutate()}>화면 데이터 다시 불러오기</Button></> : null}
          {refresh.isError ? <BackupError error={refresh.error} retry={() => refresh.mutate()} /> : null}
        </>
      )}
    </fieldset>
  );
}
