import type { CurrencySales, MoneyTotal, Outcome, OutcomeKind } from './model';
import { todayKst } from './format';

const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

/** 원금 각각은 안전 정수일 때만 사용하며, 합계는 Number로 되돌리지 않는다. */
function total(outcomes: Outcome[], kind: OutcomeKind): MoneyTotal {
  const records = outcomes.filter((o) => o.kind === kind);
  let known = 0n;
  let missing = 0;
  for (const record of records) {
    if (record.amount === null || !Number.isSafeInteger(record.amount)) missing += 1;
    else known += BigInt(record.amount);
  }
  return { amount: missing ? null : known.toString(), knownAmount: known.toString(), records: records.length, missingAmounts: missing };
}

/** 저장된 통화 코드는 그대로 보존한다. 서로 다른 통화를 환산·합산하지 않는다. */
export function groupOutcomes(outcomes: Outcome[]): CurrencySales[] {
  const currencies = new Map<string, Outcome[]>();
  for (const outcome of outcomes) {
    const records = currencies.get(outcome.currency) ?? [];
    records.push(outcome);
    currencies.set(outcome.currency, records);
  }
  return Array.from(currencies, ([currency, records]) => ({
    currency,
    contract: total(records, 'contract_confirmed'),
    collected: total(records, 'payment_received'),
    refunded: total(records, 'refund'),
    directCost: total(records, 'direct_cost'),
  })).sort((a, b) => a.currency.localeCompare(b.currency));
}

export function salesPeriod(now: Date, periodDays = 30): { start: string; end: string } {
  const end = todayKst(now);
  const start = new Date(end + 'T00:00:00Z');
  start.setUTCDate(start.getUTCDate() - (periodDays - 1));
  return { start: start.toISOString().slice(0, 10), end };
}

/** 구형 KRW 단일 합계 계약은 안전 범위와 미기록 여부가 확인된 값만 유지한다. */
export function legacyMoneyAmount(total: MoneyTotal): string | null {
  if (total.amount === null) return null;
  const amount = BigInt(total.amount);
  return amount >= -MAX_SAFE && amount <= MAX_SAFE ? total.amount : null;
}

export function legacyScalar(value: number | null): string | null {
  return value !== null && Number.isSafeInteger(value) ? String(value) : null;
}
