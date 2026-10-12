import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { mapDetail } from '../data/live/mapper';
import { Conditions } from '../features/detail/Conditions';

it('작업 시작 근거를 보존하고 구인 마감과 별도로 원문을 열 수 있다', () => {
  const input = JSON.parse(readFileSync(join(__dirname, '../../../contracts/fixtures/lead-details/01.json'), 'utf-8'));
  const quote = '10월 10일부터';
  input.work_period.start = { at: '2026-10-09T15:00:00Z', precision: 'day', raw: quote };
  input.evidence.work_start = [{
    id: 'work-start-evidence', quote, span: { start: 0, end: quote.length }, basis: 'explicit', confidence: 'high',
    observed_at: '2026-09-30T00:00:00Z', source_record_id: 'source-fixture', version: 'rules-2026.09.7',
    note: '작업 시작일 (구인 마감과 다름)',
  }];
  const lead = mapDetail(input);
  const onShow = vi.fn();
  render(<Conditions lead={lead} categoryLabel={(id) => id} onShowEvidence={onShow} now={new Date('2026-09-30T00:00:00Z')} />);
  const row = screen.getByText('작업 시작').closest('.cond-row') as HTMLElement;
  fireEvent.click(within(row).getByRole('button', { name: '근거 1' }));
  expect(within(row).getByText(quote)).toBeTruthy();
  fireEvent.click(within(row).getByRole('button', { name: '원문에서 보기' }));
  expect(onShow).toHaveBeenCalledWith('work-start-evidence');
  expect(lead.evidence.workStart?.[0]?.quote).toBe(quote);
  expect(lead.workPeriod.start.at).not.toBe(lead.deadline.at);
});
