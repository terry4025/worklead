import { describe, expect, it } from 'vitest';
import { formatFuzzyDate, formatKrw, formatPay } from '../domain/format';
import type { Pay } from '../domain/model';

const pay = (p: Partial<Pay>): Pay => ({ raw: null, currency: 'KRW', min: null, max: null, unit: 'unknown', negotiable: false, ...p });

describe('금액 표기', () => {
  it('10만원 미만은 원 단위, 이상은 만원 단위', () => {
    expect(formatKrw(12_000)).toBe('12,000원');
    expect(formatKrw(1_500_000)).toBe('150만원');
    expect(formatKrw(1_255_000)).toBe('125.5만원');
    expect(formatKrw(150_000_000)).toBe('1억 5,000만원');
  });

  it('모르는 금액은 절대 0원이 아니다', () => {
    expect(formatPay(pay({})).text).toBe('예산 미기재');
    expect(formatPay(pay({ unit: 'negotiable', negotiable: true })).text).toBe('금액 협의');
    expect(formatPay(pay({ raw: '추후 협의 예정' })).text).toBe('금액 해석 불가');
    for (const p of [pay({}), pay({ unit: 'negotiable' }), pay({ raw: 'x' })]) {
      expect(formatPay(p).text).not.toMatch(/0원/);
      expect(formatPay(p).missing).toBe(true);
    }
  });

  it('시급·월급·건당은 단위를 그대로 보여준다', () => {
    expect(formatPay(pay({ min: 12_000, max: 12_000, unit: 'hour' })).text).toBe('시급 12,000원');
    expect(formatPay(pay({ min: 300_000, max: 300_000, unit: 'month' })).text).toBe('월 30만원');
    expect(formatPay(pay({ min: 500_000, max: 800_000, unit: 'project', negotiable: true })).text).toBe('건당 50~80만원 · 협의 가능');
    expect(formatPay(pay({ min: 42_000_000, max: 42_000_000, unit: 'unknown' })).text).toBe('4,200만원 (단위 미확인)');
    expect(formatPay(pay({ min: 300_000, max: null, unit: 'project' })).text).toBe('건당 30만원 이상');
  });

  it('부정확한 날짜를 정확한 시각처럼 쓰지 않는다', () => {
    const now = new Date('2026-09-26T03:00:00Z');
    expect(formatFuzzyDate({ at: '2026-09-23T03:00:00Z', precision: 'approximate', raw: '3일 전' }, now)).toBe('9월 23일경 (원문 "3일 전")');
    expect(formatFuzzyDate({ at: null, precision: 'unknown', raw: null }, now)).toBe('미확인');
    expect(formatFuzzyDate({ at: '2026-10-02T15:00:00Z', precision: 'day', raw: '10월 3일까지' }, now)).toBe('10월 3일');
  });
});
