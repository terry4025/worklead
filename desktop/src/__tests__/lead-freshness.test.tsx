import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { buildDemoLeads } from '../data/mock/fixtures';
import { MockPort } from '../data/mock/mockPort';
import { discoveryDelaySeconds, durationLabel } from '../domain/freshness';
import type { LeadSummary, SourceInfo } from '../domain/model';
import { inQueue } from '../domain/queues';
import { toSummary } from '../domain/summary';
import { LeadTiming } from '../features/leads/LeadTiming';
import { LeadRow } from '../features/leads/LeadRow';
import { SourceMonitoring } from '../features/collection/SourceMonitoring';
import { deriveIssues, IssueStrip } from '../features/header/IssueStrip';
import { DEFAULT_FILTER } from '../features/leads/FilterBar';
import { FreshnessMetrics } from '../features/leads/FreshnessMetrics';

afterEach(cleanup);
const now = new Date('2026-10-16T06:00:00Z');
function candidate(patch: Partial<LeadSummary> = {}): LeadSummary {
  return {
    ...toSummary(buildDemoLeads(now.getTime())[0]!),
    published: { at: '2026-10-16T05:00:00Z', precision: 'exact', raw: null },
    firstSeenAt: '2026-10-16T05:55:00Z', lastCheckedAt: '2026-10-16T05:55:00Z',
    recommendation: 'needs_review', userMark: null, salesStage: 'new',
    sourceStatus: 'open', accessStatus: 'accessible', recheckDue: false,
    demandIntent: { value: 'buyer_project', basis: 'explicit' },
    engagementType: { value: 'project', basis: 'explicit' },
    workMode: { value: 'unknown', basis: null },
    reasons: [{ tone: 'caution', text: '재택 가능 여부 확인 필요' }], ...patch,
  };
}

describe('지금 검토 대기열', () => {
  it('확인 필요 제작 의뢰를 포함하고 재택 미확인 주의를 유지한다', () => {
    const lead = candidate();
    expect(inQueue(lead, 'fresh', now)).toBe(true);
    render(<LeadRow lead={lead} queue="fresh" now={now} selected={false} categoryLabel={(c) => c} onOpen={() => undefined} onSelect={() => undefined} />);
    expect(screen.getByText('재택 가능 여부 확인 필요')).toBeTruthy();
    expect(screen.getByText('게시 1시간 전')).toBeTruthy();
    expect(screen.getByText('첫 발견 5분 전')).toBeTruthy();
    expect(screen.getByText('발견 지연 55분')).toBeTruthy();
    expect(screen.queryByText('목록 조건에서 벗어남')).toBeNull();
  });

  it.each([
    ['마감', { sourceStatus: 'closed' }],
    ['접근 차단', { accessStatus: 'blocked' }],
    ['오래된 모집 확인', { recheckDue: true }],
    ['게시 시각 없음', { published: { at: null, precision: 'unknown', raw: null } }],
    ['일 단위 게시 시각', { published: { at: '2026-10-16T00:00:00Z', precision: 'day', raw: null } }],
    ['25시간 전 게시', { published: { at: '2026-10-15T05:00:00Z', precision: 'exact', raw: null } }],
    ['미래 게시', { published: { at: '2026-10-16T07:00:00Z', precision: 'exact', raw: null } }],
    ['사용자 제외', { userMark: 'dismissed' }],
    ['이미 연락함', { salesStage: 'contacted' }],
    ['일반 고용', { engagementType: { value: 'part_time', basis: 'explicit' } }],
  ] as [string, Partial<LeadSummary>][])('%s 건은 전체 검토와 구분한다', (_name, patch) => {
    expect(inQueue(candidate(patch), 'fresh', now)).toBe(false);
  });

  it('데모 목록과 집계도 같은 시간 기준과 정렬을 적용한다', async () => {
    const port = new MockPort({ scenario: 'normal', now: () => now.getTime(), latency: [0, 0] });
    const filter = { ...DEFAULT_FILTER, sort: 'published' as const };
    const result = await port.listLeads({ queue: 'fresh', filter, cursor: null, limit: 100 });
    const counts = await port.countQueues(filter);
    expect(counts?.fresh).toBe(result.items.length);
    for (const lead of result.items) expect(inQueue(lead, 'fresh', now)).toBe(true);
  });
});

describe('발견 지연과 모집 확인 시각', () => {
  it('모집 확인을 갱신해도 첫 발견 지연은 바뀌지 않는다', () => {
    const lead = candidate({ lastCheckedAt: now.toISOString() });
    expect(discoveryDelaySeconds(lead)).toBe(3300);
    render(<LeadTiming lead={lead} now={now} detail />);
    expect(screen.getByText('발견 지연 55분')).toBeTruthy();
    expect(screen.getByText('모집 확인 방금')).toBeTruthy();
  });
  it('부정확한 게시일과 음수 차이는 정밀한 지연으로 표시하지 않는다', () => {
    expect(discoveryDelaySeconds(candidate({ published: { at: '2026-10-15T00:00:00Z', precision: 'approximate', raw: '1일 전' } }))).toBeNull();
    expect(discoveryDelaySeconds(candidate({ firstSeenAt: '2026-10-16T04:00:00Z' }))).toBeNull();
    expect(durationLabel(75357)).toBe('20시간 55분');
    expect(discoveryDelaySeconds(candidate({ freshness: { discoveryDelaySeconds: null, firstListedAt: null, listingToDetailSeconds: null } }))).toBeNull();
    expect(discoveryDelaySeconds(candidate({ freshness: null }))).toBeNull();
  });
  it('목록 첫 관찰이 실제 있는 경우에만 목록 지연을 따로 표시한다', () => {
    const lead = candidate({ freshness: { discoveryDelaySeconds: 3300, firstListedAt: '2026-10-16T05:53:00Z', listingToDetailSeconds: 120 } });
    render(<LeadTiming lead={lead} now={now} detail />);
    expect(screen.getByText('목록 관찰→상세 확인 2분')).toBeTruthy();
    expect(screen.getByText('공개 목록 첫 관찰 7분 전')).toBeTruthy();
  });
});

it('지연된 수집 일정을 원인 단정 없이 상단과 소스에 표시한다', async () => {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const source: SourceInfo = { ...(await port.listSources())[0]!, monitoring: {
    state: 'overdue', lastStartedAt: '2026-10-15T16:00:00Z', lastFinishedAt: '2026-10-15T16:10:00Z',
    nextDueAt: '2026-10-15T19:10:00Z', overdueSeconds: 39000, intervalMinutes: 180,
  } };
  const issues = deriveIssues({ sources: [source], runs: [], stream: 'open', mode: 'live', openCollection: () => undefined, now });
  expect(issues.some((issue) => issue.text.includes('10시간 50분') && issue.text.includes('앱 실행·절전·실행 기록'))).toBe(true);
  render(<SourceMonitoring source={source} now={now} />);
  expect(screen.getByText('탐색 일정 지연')).toBeTruthy();
  expect(screen.getByText(/주기를 줄여도 사이트 목록 노출 지연/)).toBeTruthy();
});

it('발견 지연 표본 부족을 0분으로 표시하지 않고 백필 한계를 함께 보여준다', () => {
  render(<FreshnessMetrics sources={[]} metrics={[{ sourceId: 'daangn-alba', periodDays: 7, records: 3, samples: 0, unavailableSamples: 3, medianDiscoverySeconds: null, p95DiscoverySeconds: null, within15Minutes: 0, within1Hour: 0, note: '백필이 포함되어 시장 전체의 수집 속도와 다릅니다.' }]} />);
  expect(screen.getByText('중앙값 계산 불가 · p95 계산 불가')).toBeTruthy();
  expect(screen.getByText('계산 표본 0/3건 · 15분 이내 0건 · 계산 불가 3건')).toBeTruthy();
  expect(screen.getByText(/백필이 포함/)).toBeTruthy();
});

it('탐색 재개 후에도 긴 직전 공백을 표시하고 정상 주기의 공백은 경고하지 않는다', async () => {
  const port = new MockPort({ scenario: 'normal', latency: [0, 0] });
  const source: SourceInfo = { ...(await port.listSources())[0]!, monitoring: {
    state: 'running', lastStartedAt: now.toISOString(), lastFinishedAt: null, nextDueAt: null,
    overdueSeconds: 0, intervalMinutes: 30, previousGapSeconds: 14 * 3600,
  } };
  const view = render(<SourceMonitoring source={source} now={now} />);
  expect(screen.getByText('탐색 진행 중')).toBeTruthy();
  expect(screen.getByText('직전 수집 공백')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('수집 공백이 14시간');
  source.monitoring = { ...source.monitoring!, previousGapSeconds: 30 * 60 };
  view.rerender(<SourceMonitoring source={source} now={now} />);
  expect(screen.getByText('30분')).toBeTruthy();
  expect(screen.queryByRole('status')).toBeNull();
  source.monitoring = { ...source.monitoring!, previousGapSeconds: null };
  view.rerender(<SourceMonitoring source={source} now={now} />);
  expect(screen.queryByText('직전 수집 공백')).toBeNull();
});

it('재시작에 받은 야간 요약은 다른 경고가 많아도 날짜와 과거 발견 건수로 계속 표시한다', () => {
  const notices = [{ id: 'summary-1', kind: 'new_review_lead' as const, title: '조용한 시간에 발견한 확인 필요 제작 의뢰 3건', createdAt: '2026-10-16T00:00:00Z' }];
  const issues = deriveIssues({ sources: [], runs: [], notices, stream: 'open', mode: 'live', openCollection: () => undefined, now });
  const other = [1, 2, 3].map((id) => ({ key: `error-${id}`, tone: 'warn' as const, text: `수집 경고 ${id}` }));
  const view = render(<IssueStrip issues={[...issues, ...other]} />);
  expect(screen.getByText(/야간 요약 · 10월 16일 09:00 · 조용한 시간에 발견한 확인 필요 제작 의뢰 3건/)).toBeTruthy();
  expect(screen.queryByText('수집 경고 3')).toBeNull();
  view.rerender(<IssueStrip issues={[...issues, ...other]} />);
  expect(screen.getByText(/야간 요약/)).toBeTruthy();
  expect(screen.queryByText(/현재 지원 가능/)).toBeNull();
});
