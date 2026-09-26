/**
 * 백엔드가 실제 엔진으로 생성한 contracts/fixtures 를 화면 모델로 변환해 본다.
 * 계약이 바뀌어 매퍼가 깨지면 여기서 드러난다.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { mapBootstrap, mapDetail, mapEvent, mapRun, mapSales, mapSettings, mapSource, mapSummary } from '../data/live/mapper';
import { formatPay } from '../domain/format';
import { recruitDisplay, remoteClass } from '../domain/present';

const FIX = join(__dirname, '../../../contracts/fixtures');
const read = (p: string) => JSON.parse(readFileSync(join(FIX, p), 'utf-8'));

describe('계약 fixture → 화면 모델', () => {
  it('bootstrap·settings·metrics', () => {
    const b = mapBootstrap(read('bootstrap.json'));
    expect(b.queues.map((q) => q.id)).toContain('needs_review');
    expect(b.categories.length).toBeGreaterThan(3);
    const s = mapSettings(read('settings.json'));
    expect(s.recheck.ttlHours).toBeGreaterThan(0);
    expect(s.ai.keyConfigured).toBe(false);
    const m = mapSales(read('metrics.json'));
    expect(m.collected).toBeLessThanOrEqual(m.contractAmount ?? Infinity);
  });

  it('모든 리드 상세를 변환하고 표시 규칙을 적용할 수 있다', () => {
    const files = readdirSync(join(FIX, 'lead-details'));
    expect(files.length).toBeGreaterThan(10);
    for (const f of files) {
      const d = mapDetail(read(`lead-details/${f}`));
      expect(d.id).toBeTruthy();
      expect(formatPay(d.pay).text).not.toBe('0원');
      remoteClass(d);
      recruitDisplay(d);
      for (const list of Object.values(d.evidence)) {
        for (const e of list ?? []) {
          if (e.span && d.bodyText) expect(d.bodyText.slice(e.span.start, e.span.end)).toBe(e.quote);
        }
      }
    }
  });

  it('목록·소스·실행 시나리오', () => {
    const page = read('leads.all.json');
    expect(page.items.map(mapSummary).length).toBe(page.items.length);
    for (const sc of ['normal', 'partial', 'blocked', 'parse_failure']) {
      const sources = read(`scenarios/${sc}/sources.json`).items.map(mapSource);
      const site = sources.find((s: { kind: string }) => s.kind === 'site');
      expect(site.coverage.marketCoverage).toBe('unknown');
      expect(site.coverage.targetLabel).toBe('전국');
      const runs = read(`scenarios/${sc}/runs.json`).items.map(mapRun);
      expect(runs.length).toBeGreaterThan(0);
    }
    const blocked = read('scenarios/blocked/sources.json').items.map(mapSource).find((s: { kind: string }) => s.kind === 'site');
    expect(blocked.stoppedReason).toBeTruthy();
    expect(blocked.health.status).toBe('blocked');
    const first = read('sources.first_run.json').items.map(mapSource).find((s: { id: string }) => s.id === 'daangn-alba');
    expect(first.policy.status).toBe('permission_pending');
    expect(first.research.ready).toBe(false);
  });

  it('이벤트', () => {
    const events = read('events.sample.json');
    const mapped = events.map(mapEvent).filter(Boolean);
    expect(mapped.length).toBe(events.length);
  });
});
