import { describe, expect, it } from 'vitest';
import { DEFAULT_FILTER } from '../features/leads/FilterBar';
import { MockPort } from '../data/mock/mockPort';

const port = (scenario: 'normal' | 'offline' | 'first_run' = 'normal') => new MockPort({ scenario, latency: [0, 0], now: () => Date.parse('2026-09-26T03:00:00Z') });

describe('데모 데이터 계층', () => {
  it('대기열 건수와 목록이 일치한다', async () => {
    const p = port();
    const counts = await p.countQueues(DEFAULT_FILTER);
    expect(counts).not.toBeNull();
    const rec = await p.listLeads({ queue: 'recommended', filter: DEFAULT_FILTER, cursor: null, limit: 100 });
    expect(rec.items.length).toBe(counts?.recommended);
    expect(rec.items.every((i) => i.recommendation === 'recommended')).toBe(true);
  });

  it('제외하면 대기열이 바뀐다 (자동 판정은 그대로)', async () => {
    const p = port();
    const [first] = (await p.listLeads({ queue: 'recommended', filter: DEFAULT_FILTER, cursor: null, limit: 1 })).items;
    await p.updateLead(first!.id, { userMark: 'dismissed' });
    const d = await p.getLead(first!.id);
    expect(d.recommendation).toBe('recommended');
    const dismissed = await p.listLeads({ queue: 'dismissed', filter: DEFAULT_FILTER, cursor: null, limit: 10 });
    expect(dismissed.items.map((i) => i.id)).toContain(first!.id);
  });

  it('수동 입력 리드는 재확인하지 않는다', async () => {
    const p = port();
    await expect(p.recheckLead('L-1020')).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('권한 대기 소스는 실행할 수 없다', async () => {
    const p = port('first_run');
    await expect(p.startRun({ sourceId: 'daangn-alba', kind: 'discovery' })).rejects.toMatchObject({ code: 'policy_not_allowed' });
    expect(await p.countQueues(DEFAULT_FILTER)).toMatchObject({ all: 0 });
  });

  it('연결 실패 시나리오는 연결 오류를 낸다 (데모로 조용히 넘기지 않음)', async () => {
    await expect(port('offline').bootstrap()).rejects.toMatchObject({ kind: 'connection' });
  });

  it('won 만으로 수금이 늘지 않는다', async () => {
    const p = port();
    const before = await p.getSalesSummary();
    await p.updateLead('L-1001', { salesStage: 'won' });
    const after = await p.getSalesSummary();
    expect(after.collected).toBe(before.collected);
    expect(after.won).toBe(before.won + 1);
  });
});
