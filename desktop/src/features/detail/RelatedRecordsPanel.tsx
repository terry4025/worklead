import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { getDatabaseEpoch } from '../../app/databaseCache';
import { usePort } from '../../app/PortContext';
import { reloadRelation, useRelationMutation } from '../../app/queries';
import { toPortError } from '../../data/port';
import type { LeadDetail, RelatedRecord } from '../../domain/model';
import { Button } from '../../ui/Button';
import { StatusMark } from '../../ui/StatusMark';

const RELATION_LABEL = { duplicate: '동일 본문 후보', repost: '재게시 후보', similar: '유사 글 후보' } as const;

function relationError(error: unknown): string {
  const e = toPortError(error);
  if (e.kind === 'connection' || e.code === 'unexpected') return '응답을 받지 못해 저장 결과를 확인해야 합니다. 현재 관계를 불러오세요.';
  if (e.code === 'relation_changed' || e.code.includes('conflict') || e.code.includes('stale') || e.code.includes('generation')) return '관계 상태나 복원된 데이터가 바뀌었습니다. 현재 관계를 불러온 뒤 다시 선택하세요.';
  return '관계를 저장하지 못했습니다. 현재 관계를 불러온 뒤 다시 선택하세요.';
}

function RelationRow({ leadId, relation }: { leadId: string; relation: RelatedRecord }) {
  const port = usePort();
  const qc = useQueryClient();
  const mutation = useRelationMutation();
  const locked = useRef(false);
  const alive = useRef(true);
  const primaryButton = useRef<HTMLButtonElement>(null);
  const reloadButton = useRef<HTMLButtonElement>(null);
  const focusSource = useRef<HTMLButtonElement | null>(null);
  const [pending, setPending] = useState<'save' | 'reload' | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const source = focusSource.current;
    if (pending || !source) return;
    if (document.activeElement === source || (!source.isConnected && document.activeElement === document.body)) {
      (error ? reloadButton : primaryButton).current?.focus();
    }
    focusSource.current = null;
  }, [pending, error]);
  const available = Boolean(relation.leadId && relation.leadId !== leadId);

  const change = async (action: 'confirm' | 'dismiss', source: HTMLButtonElement) => {
    if (locked.current || error || !available) return;
    if (document.activeElement === source) focusSource.current = source;
    locked.current = true;
    const epoch = getDatabaseEpoch(qc);
    setPending('save');
    try {
      await mutation.mutateAsync({ id: leadId, relatedId: relation.leadId!, relationId: relation.id, input: { action, expectedRevision: relation.revision, expectedGeneration: relation.databaseGeneration } });
      if (alive.current && epoch !== getDatabaseEpoch(qc)) setError('데이터가 복원되었습니다. 현재 관계를 다시 불러오세요.');
    } catch (err) {
      if (alive.current) setError(relationError(err));
    } finally {
      locked.current = false;
      if (alive.current) setPending(null);
    }
  };

  const reload = async (source: HTMLButtonElement) => {
    if (locked.current || !relation.leadId) return;
    if (document.activeElement === source) focusSource.current = source;
    locked.current = true;
    setPending('reload');
    try {
      await reloadRelation(qc, port, leadId, relation.leadId, relation.id);
      if (alive.current) setError(null);
    } catch {
      if (alive.current) setError('현재 관계를 불러오지 못했습니다. 연결을 확인한 뒤 다시 불러오세요.');
    } finally {
      locked.current = false;
      if (alive.current) setPending(null);
    }
  };

  return (
    <li aria-label={`${relation.title} 관계`} aria-busy={pending !== null || undefined}>
      <div>{RELATION_LABEL[relation.relation]}: {relation.title}</div>
      <div className="form-actions">
        <StatusMark tone={relation.dismissed ? 'muted' : relation.confirmed ? 'good' : 'unknown'}>
          {relation.dismissed ? '연결 해제' : relation.confirmed ? '내가 관계 확인' : '관계 미확인'}
        </StatusMark>
        <Button ref={primaryButton} size="sm" disabled={!available || pending !== null || error !== null} onClick={(event) => void change(relation.confirmed && !relation.dismissed ? 'dismiss' : 'confirm', event.currentTarget)}>
          {relation.dismissed ? '다시 연결 확인' : relation.confirmed ? '연결 해제' : '관계 확인'}
        </Button>
        {!relation.dismissed && !relation.confirmed ? (
          <Button size="sm" disabled={!available || pending !== null || error !== null} onClick={(event) => void change('dismiss', event.currentTarget)}>연결 해제</Button>
        ) : null}
        {pending === 'save' ? <span className="save-state" role="status">저장 중…</span> : null}
      </div>
      {relation.basis ? <p className="field-hint">{relation.basis}</p> : null}
      {!available ? <p className="field-hint">연결할 리드가 없어 관계를 변경할 수 없습니다.</p> : null}
      {error ? (
        <div>
          <p className="field-hint" role="alert">{error}</p>
          <Button ref={reloadButton} size="sm" busy={pending === 'reload'} disabled={pending !== null} onClick={(event) => void reload(event.currentTarget)}>
            {pending === 'reload' ? '조회 중…' : '현재 관계 불러오기'}
          </Button>
        </div>
      ) : null}
    </li>
  );
}

export function RelatedRecordsPanel({ lead }: { lead: LeadDetail }) {
  if (!lead.related.length) return null;
  return (
    <div>
      <p className="field-hint">관계만 표시합니다. 원문·메모·영업 상태는 합치지 않으며, 해제한 연결은 재수집으로 되살리지 않습니다.</p>
      <ul className="plain-list" aria-label="관련 글 관계">
        {lead.related.map((relation) => <RelationRow key={`${lead.id}-${relation.id}`} leadId={lead.id} relation={relation} />)}
      </ul>
    </div>
  );
}
