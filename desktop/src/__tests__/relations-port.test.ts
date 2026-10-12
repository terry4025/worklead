import { describe, expect, it, vi } from 'vitest';
import candidate from '../../../contracts/fixtures/relation-candidate.json';
import confirmed from '../../../contracts/fixtures/relation-confirmed.json';
import dismissed from '../../../contracts/fixtures/relation-dismissed.json';
import { LivePort } from '../data/live/livePort';
import { mapDetail } from '../data/live/mapper';
import { MockPort } from '../data/mock/mockPort';
import type { LeadDetail } from '../domain/model';

function map(value: unknown) { return mapDetail(value as Parameters<typeof mapDetail>[0]); }
function relationPair(): [LeadDetail, LeadDetail] {
  return [map(confirmed.lead), map(confirmed.related_lead)].map((lead) => ({
    ...lead, related: lead.related.map((r) => ({ ...r, confirmed: false, dismissed: false, revision: 0 })),
  })) as [LeadDetail, LeadDetail];
}

function mock() {
  const [left, right] = relationPair();
  const port = new MockPort({ scenario: 'normal', latency: [0, 0], initialLeads: [left, right] });
  const relation = left.related[0]!;
  const input = { action: 'confirm' as const, expectedRevision: relation.revision, expectedGeneration: relation.databaseGeneration };
  return { port, left, right, relation, input };
}

describe('관계 API 어댑터', () => {
  it('실제 임시 API fixture 상태와 양쪽 리드를 매핑하고 CAS 정보를 전송한다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(confirmed)).mockResolvedValueOnce(Response.json(dismissed));
    const port = new LivePort({ baseUrl: 'http://127.0.0.1:41001', token: 'fixture-session' }, fetcher);
    const lead = map(candidate); const relation = lead.related[0]!;
    const result = await port.updateRelation(lead.id, relation.id, { action: 'confirm', expectedRevision: relation.revision, expectedGeneration: relation.databaseGeneration });
    expect(result.lead.related[0]).toMatchObject({ confirmed: true, dismissed: false, revision: 1 });
    expect(result.relatedLead.related[0]).toMatchObject({ id: relation.id, confirmed: true, revision: 1 });
    expect(fetcher.mock.calls[0]?.[0]).toBe(`http://127.0.0.1:41001/v1/leads/${encodeURIComponent(lead.id)}/relations/${encodeURIComponent(relation.id)}`);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({ action: 'confirm', expected_revision: relation.revision, expected_generation: relation.databaseGeneration });
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get('Authorization')).toBe('Bearer fixture-session');
    const unlinked = await port.updateRelation(result.relatedLead.id, relation.id, { action: 'dismiss', expectedRevision: 1, expectedGeneration: relation.databaseGeneration });
    expect(unlinked.lead.related[0]).toMatchObject({ confirmed: false, dismissed: true, revision: 2 });
  });

  it('충돌 응답은 자동 POST 재시도 없이 오류 코드를 보존한다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: 'relation_changed', message: '현재 관계를 다시 읽으세요', retryable: false, request_id: 'fixture-request', details: null } }, { status: 409 }));
    const port = new LivePort({ baseUrl: 'http://127.0.0.1:41001', token: 'fixture-session' }, fetcher);
    await expect(port.updateRelation('a', 'b', { action: 'confirm', expectedRevision: 0, expectedGeneration: 0 })).rejects.toMatchObject({ kind: 'api', code: 'relation_changed', retryable: false });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('이전 상세 fixture도 관계 상태 기본값으로 읽는다', () => {
    const legacy = structuredClone(candidate) as Record<string, unknown>;
    legacy.related = [{ ...candidate.related[0], confirmed: undefined, dismissed: undefined, revision: undefined, database_generation: undefined }];
    expect(map(legacy).related[0]).toMatchObject({ confirmed: false, dismissed: false, revision: 0, databaseGeneration: 0 });
  });
});

describe('관계 데모 동작', () => {
  it('양방향 확인·해제·다시 확인을 동기화하며 개별 기록은 보존한다', async () => {
    const { port, left, right, relation, input } = mock();
    await port.updateLead(left.id, { memo: '왼쪽 메모', salesStage: 'negotiating', userMark: 'interested', draftText: '직접 쓴 초안' });
    await port.updateLead(right.id, { memo: '오른쪽 메모', salesStage: 'contacted' });
    const before = [await port.getLead(left.id), await port.getLead(right.id)];
    const confirmed = await port.updateRelation(left.id, relation.id, input);
    expect(confirmed.lead.related[0]).toMatchObject({ confirmed: true, dismissed: false, revision: 1 });
    expect(confirmed.relatedLead.related[0]).toMatchObject({ confirmed: true, revision: 1 });
    const rejected = await port.updateRelation(right.id, relation.id, { ...input, action: 'dismiss', expectedRevision: 1 });
    expect(rejected.lead.related[0]).toMatchObject({ confirmed: false, dismissed: true, revision: 2 });
    expect((await port.getLead(left.id)).related[0]).toMatchObject({ dismissed: true, revision: 2 });
    await port.updateRelation(left.id, relation.id, { ...input, expectedRevision: 2 });
    for (const prior of before) {
      const after = await port.getLead(prior.id);
      expect(after.related[0]).toMatchObject({ confirmed: true, dismissed: false, revision: 3 });
      for (const key of ['memo', 'salesStage', 'userMark', 'draft', 'bodyText', 'outcomes', 'originalUrl'] as const) expect(after[key]).toEqual(prior[key]);
    }
    port.dispose();
  });

  it('같은 선택 반복은 양쪽 활동·revision·이벤트를 늘리지 않는다', async () => {
    const { port, left, right, relation, input } = mock();
    const events = vi.fn(); const unsubscribe = port.subscribe(events, () => undefined);
    await port.updateRelation(left.id, relation.id, input);
    const before = await port.getLead(left.id); const emitted = events.mock.calls.length;
    const repeated = await port.updateRelation(right.id, relation.id, input);
    expect(repeated.relatedLead.activity).toEqual(before.activity);
    expect(repeated.relatedLead.related[0]?.revision).toBe(1);
    expect(events.mock.calls.length).toBe(emitted);
    unsubscribe(); port.dispose();
  });

  it('다른 선택의 오래된 revision과 복원 generation은 거절한다', async () => {
    const { port, left, relation, input } = mock();
    await port.updateRelation(left.id, relation.id, input);
    await expect(port.updateRelation(left.id, relation.id, { ...input, action: 'dismiss' })).rejects.toMatchObject({ code: 'relation_changed' });
    await expect(port.updateRelation(left.id, relation.id, { ...input, expectedGeneration: input.expectedGeneration + 1 })).rejects.toMatchObject({ code: 'relation_changed' });
    expect((await port.getLead(left.id)).related[0]?.confirmed).toBe(true);
    port.dispose();
  });

  it('존재하지 않는 후보와 잘못된 revision을 거절한다', async () => {
    const { port, left, relation, input } = mock();
    await expect(port.updateRelation(left.id, 'absent', input)).rejects.toMatchObject({ code: 'relation_not_found' });
    await expect(port.updateRelation(left.id, relation.id, { ...input, expectedRevision: -1 })).rejects.toMatchObject({ code: 'validation_error' });
    expect((await port.getLead(left.id)).related[0]?.revision).toBe(0);
    port.dispose();
  });
});
