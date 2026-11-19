import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { buildDemoLeads } from '../data/mock/fixtures';
import { postAge } from '../domain/freshness';
import type { LeadSummary } from '../domain/model';
import { toSummary } from '../domain/summary';
import { LeadTiming } from '../features/leads/LeadTiming';

afterEach(cleanup);
const now = new Date('2026-11-07T12:00:00Z');
const lead = (at: string | null): LeadSummary => ({
  ...toSummary(buildDemoLeads(now.getTime())[0]!),
  published: { at, precision: at ? 'exact' : 'unknown', raw: null },
  firstSeenAt: '2026-11-07T11:00:00Z',
});

describe('post age', () => {
  it('buckets by hours since posting and never guesses from first-seen time', () => {
    expect(postAge(lead('2026-11-07T00:00:01Z'), now)).toBe('fresh');
    expect(postAge(lead('2026-11-06T12:00:00Z'), now)).toBe('recent');
    expect(postAge(lead('2026-11-04T12:00:00Z'), now)).toBe('stale');
    expect(postAge(lead(null), now)).toBe('unknown');
  });

  it('adds elapsed days once the relative label turns into a date', () => {
    render(<LeadTiming lead={lead('2026-09-30T03:00:00Z')} now={now} />);
    expect(screen.getByText(/게시 .*\(38일 전\)/).className).toContain('age-stale');
  });

  it('marks posts within a day as fresh', () => {
    render(<LeadTiming lead={lead('2026-11-07T09:00:00Z')} now={now} />);
    expect(screen.getByText('게시 3시간 전').className).toContain('age-fresh');
  });
});
