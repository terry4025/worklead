import { durationLabel } from '../../domain/freshness';
import type { SalesSummary, SourceInfo } from '../../domain/model';

export function FreshnessMetrics({ metrics, sources }: { metrics: SalesSummary['freshness']; sources: SourceInfo[] }) {
  if (!metrics?.length) return null;
  return <details className="freshness-metrics">
    <summary>최근 7일 발견 지연 보기</summary>
    <div className="freshness-metrics-grid">
      {metrics.map((m) => <div key={m.sourceId}>
        <strong>{sources.find((s) => s.id === m.sourceId)?.name ?? m.sourceId}</strong>
        <p>중앙값 {m.medianDiscoverySeconds === null ? '계산 불가' : durationLabel(m.medianDiscoverySeconds)} · p95 {m.p95DiscoverySeconds === null ? '계산 불가' : durationLabel(m.p95DiscoverySeconds)}</p>
        <p>계산 표본 {m.samples}/{m.records}건 · 15분 이내 {m.within15Minutes}건 · 계산 불가 {m.unavailableSamples}건</p>
        <p className="field-hint">{m.note}</p>
      </div>)}
    </div>
  </details>;
}
