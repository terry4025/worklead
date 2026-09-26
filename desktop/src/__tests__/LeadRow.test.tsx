import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { buildDemoLeads } from '../data/mock/fixtures';
import { toSummary } from '../domain/summary';
import { LeadRow } from '../features/leads/LeadRow';

describe('목록 행', () => {
  const now = Date.parse('2026-09-26T03:00:00Z');
  const leads = buildDemoLeads(now).map(toSummary);
  const byId = (id: string) => leads.find((l) => l.id === id)!;
  const renderRow = (id: string) =>
    render(<LeadRow lead={byId(id)} queue="all" selected={false} categoryLabel={(c) => c} now={new Date(now)} onSelect={() => undefined} onOpen={() => undefined} />);

  it('예산 미기재·재택 미확인·모집 미확인을 그대로 보여준다', () => {
    renderRow('L-1009');
    expect(screen.getAllByText('예산 미기재').length).toBeGreaterThan(0);
    expect(screen.getAllByText('재택 미확인').length).toBeGreaterThan(0);
    expect(screen.getAllByText('모집 미확인').length).toBeGreaterThan(0);
    expect(screen.queryByText(/0원/)).toBeNull();
  });

  it('점수에 미확인 요소 수를 함께 보여준다', () => {
    renderRow('L-1008');
    expect(screen.getByText('미확인 2')).toBeTruthy();
    expect(screen.getByTitle(/수주 확률이 아닙니다/)).toBeTruthy();
  });

  it('접근 차단을 마감으로 표시하지 않는다', () => {
    renderRow('L-1011');
    expect(screen.getAllByText('접근 차단').length).toBeGreaterThan(0);
    expect(screen.queryByText('마감')).toBeNull();
  });
});
