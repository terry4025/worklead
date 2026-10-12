import { describe, expect, it, vi } from 'vitest';
import backup from '../../../contracts/fixtures/backup.json';
import inspection from '../../../contracts/fixtures/backup-inspection.json';
import backups from '../../../contracts/fixtures/backups.json';
import restored from '../../../contracts/fixtures/restore.json';
import { LivePort } from '../data/live/livePort';

describe('백업 API 어댑터', () => {
  it('실제 disposable API fixture를 도메인으로 옮기고 확인·해시를 POST 본문에 보낸다', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(backups))
      .mockResolvedValueOnce(Response.json(backup, { status: 201 }))
      .mockResolvedValueOnce(Response.json(inspection))
      .mockResolvedValueOnce(Response.json(restored));
    const port = new LivePort({ baseUrl: 'http://127.0.0.1:41001', token: 'fixture-session' }, fetcher);
    expect(await port.listBackups()).toEqual({ directory: backups.directory, items: backups.items.map((b) => ({ id: b.id, fileName: b.file_name, createdAt: b.created_at, sizeBytes: b.size_bytes })) });
    expect(await port.createBackup()).toEqual({ id: backup.id, fileName: backup.file_name, createdAt: backup.created_at, sizeBytes: backup.size_bytes });
    expect(await port.inspectBackup(backup.id)).toMatchObject({ sha256: inspection.sha256, revision: inspection.revision, canRestore: inspection.can_restore, leadCount: inspection.lead_count, problem: inspection.problem });
    expect(await port.restoreBackup(backup.id, { sha256: inspection.sha256, confirm: true })).toMatchObject({ restoredBackupId: restored.restored_backup_id, safetyBackup: { id: restored.safety_backup.id, fileName: restored.safety_backup.file_name }, leadCount: restored.lead_count, autoCollectionPaused: true });
    expect(fetcher.mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'POST', 'GET', 'POST']);
    expect(fetcher.mock.calls[3]?.[0]).toBe(`http://127.0.0.1:41001/v1/backups/${encodeURIComponent(backup.id)}/restore`);
    expect(JSON.parse(String(fetcher.mock.calls[3]?.[1]?.body))).toEqual({ sha256: inspection.sha256, confirm: true });
    for (const [, init] of fetcher.mock.calls) expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer fixture-session');
  });

  it('유지보수 busy 응답을 재시도 여부를 보존한 포트 오류로 옮긴다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: 'maintenance_busy', message: '데이터 작업이 진행 중입니다.', retryable: true, request_id: 'fixture-request', details: null } }, { status: 503 }));
    const port = new LivePort({ baseUrl: 'http://127.0.0.1:41001', token: 'fixture-session' }, fetcher);
    await expect(port.createBackup()).rejects.toMatchObject({ kind: 'api', code: 'maintenance_busy', retryable: true, requestId: 'fixture-request' });
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
