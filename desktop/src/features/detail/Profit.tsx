import { formatKrw } from '../../domain/format';
import { scenarioLabel } from '../../domain/labels';
import type { Profitability } from '../../domain/model';

const money = (v: number | null) => (v === null ? '—' : formatKrw(v));

export function Profit({ p }: { p: Profitability | null }) {
  if (!p) return <p className="muted-text">수익성 분석 결과가 없습니다.</p>;
  if (p.status === 'not_calculated') {
    return (
      <p className="muted-text">
        계산하지 않음 — {p.reason ?? '근거가 부족합니다.'}
        {p.revenueType === 'recurring' ? ' (반복 매출)' : ''}
      </p>
    );
  }
  const hypo = p.status === 'hypothesis';
  const rows: { label: string; get: (i: number) => string; strong?: boolean; negative?: (i: number) => boolean }[] = hypo
    ? [
        { label: '제안 견적 가설', get: (i) => money(p.scenarios[i]?.contractValue ?? null), strong: true },
        { label: '예상 투입 시간', get: (i) => (p.scenarios[i]?.hours != null ? `${p.scenarios[i]?.hours}시간` : '—') },
        { label: '예상 기여액', get: () => '계산 안 함' },
      ]
    : [
        { label: '계약 대가', get: (i) => money(p.scenarios[i]?.contractValue ?? null) },
        { label: '직접 비용', get: (i) => money(p.scenarios[i]?.directCost ?? null) },
        { label: '총 투입 시간', get: (i) => (p.scenarios[i]?.hours != null ? `${p.scenarios[i]?.hours}시간` : '—') },
        { label: '예상 기여액', get: (i) => money(p.scenarios[i]?.contribution ?? null), strong: true },
        { label: '시간당 실효 수익', get: (i) => money(p.scenarios[i]?.effectiveHourly ?? null) },
        {
          label: '목표 시간가치 반영 잔여액',
          get: (i) => money(p.scenarios[i]?.residualAfterTarget ?? null),
          negative: (i) => (p.scenarios[i]?.residualAfterTarget ?? 0) < 0,
        },
      ];
  return (
    <div className="profit">
      <p className="profit-basis">
        {hypo ? <strong>가설</strong> : null} {p.basis}
      </p>
      {p.reason ? <p className="muted-text">{p.reason}</p> : null}
      <table className="profit-table">
        <caption className="sr-only">수익성 시나리오</caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">항목</span>
            </th>
            {p.scenarios.map((s) => (
              <th key={s.key} scope="col">
                {scenarioLabel[s.key]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className={r.strong ? 'is-strong' : undefined}>
              <th scope="row">{r.label}</th>
              {p.scenarios.map((s, i) => (
                <td key={s.key} className={`num${r.negative?.(i) ? ' is-negative' : ''}`}>
                  {r.get(i)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="assumptions">
        {p.assumptions.map((a) => (
          <li key={a}>{a}</li>
        ))}
        <li>추정치입니다. 세후 순이익·확정 수익이 아니며 수주 확률을 반영하지 않습니다.</li>
      </ul>
    </div>
  );
}
