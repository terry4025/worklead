import { formatFuzzyDate, formatFull, formatRelative } from '../../domain/format';
import { discoveryDelaySeconds, durationLabel, postAge, postAgeHours } from '../../domain/freshness';
import type { LeadSummary } from '../../domain/model';

export function LeadTiming({ lead, now, detail = false }: { lead: LeadSummary; now: Date; detail?: boolean }) {
  const delay = discoveryDelaySeconds(lead);
  const posted = lead.published.precision === 'exact' && lead.published.at
    ? formatRelative(lead.published.at, now) : formatFuzzyDate(lead.published, now);
  const hours = postAgeHours(lead, now);
  // 7일이 지나면 상대 표기가 날짜로 바뀌므로 경과 일수를 함께 보인다
  const days = hours !== null && hours >= 24 * 7 ? Math.floor(hours / 24) : null;
  return <div className={detail ? 'lead-timing lead-timing-detail' : 'lead-timing'} aria-label="게시와 발견 시각">
    <span className={`lead-posted age-${postAge(lead, now)}`} title={lead.published.at ? formatFull(lead.published.at) : undefined}>게시 {posted}{days !== null ? ` (${days}일 전)` : ''}</span>
    <span title={formatFull(lead.firstSeenAt)}>첫 발견 {formatRelative(lead.firstSeenAt, now)}</span>
    <span className={delay !== null && delay >= 3 * 3600 ? 'tone-warn' : undefined} title="원문 게시부터 이 기기의 첫 발견까지. 공개 목록 등재 지연과 앱 중단 시간이 포함될 수 있습니다.">발견 지연 {delay === null ? '계산 불가' : durationLabel(delay)}</span>
    {detail ? <>
      <span title={lead.lastCheckedAt ? formatFull(lead.lastCheckedAt) : undefined}>모집 확인 {lead.lastCheckedAt ? formatRelative(lead.lastCheckedAt, now) : '기록 없음'}</span>
      {lead.freshness?.firstListedAt ? <span title={formatFull(lead.freshness.firstListedAt)}>공개 목록 첫 관찰 {formatRelative(lead.freshness.firstListedAt, now)}</span> : null}
      {lead.freshness?.listingToDetailSeconds != null ? <span>목록 관찰→상세 확인 {durationLabel(lead.freshness.listingToDetailSeconds)}</span> : null}
    </> : null}
  </div>;
}
