import type { LeadSummary, SourceInfo } from './model';

/** 관찰된 두 시각의 차이만 표시한다. 일 단위·상대 날짜를 정밀한 지연으로 바꾸지 않는다. */
export function discoveryDelaySeconds(lead: LeadSummary): number | null {
  if (lead.published.precision !== 'exact' || !lead.published.at) return null;
  const delay = lead.freshness !== undefined
    ? lead.freshness?.discoveryDelaySeconds ?? null
    : (Date.parse(lead.firstSeenAt) - Date.parse(lead.published.at)) / 1000;
  return delay !== null && Number.isFinite(delay) && delay >= 0 ? delay : null;
}

/** 게시 후 경과 구간. 게시 시각이 없으면 판단하지 않는다 (최초 발견일로 대신하지 않음). */
export type PostAge = 'fresh' | 'recent' | 'stale' | 'unknown';
export const FRESH_HOURS = 24;
export const STALE_HOURS = 72;

export function postAgeHours(lead: LeadSummary, now: Date): number | null {
  if (!lead.published.at) return null;
  const hours = (now.getTime() - Date.parse(lead.published.at)) / 3_600_000;
  return Number.isFinite(hours) ? Math.max(0, hours) : null;
}

export function postAge(lead: LeadSummary, now: Date): PostAge {
  const hours = postAgeHours(lead, now);
  if (hours === null) return 'unknown';
  return hours < FRESH_HOURS ? 'fresh' : hours >= STALE_HOURS ? 'stale' : 'recent';
}

export function durationLabel(seconds: number): string {
  if (seconds < 60) return '1분 미만';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `${hours}시간${rest ? ` ${rest}분` : ''}`;
}

export function monitoringWarning(source: SourceInfo): string | null {
  const monitor = source.monitoring;
  if (!monitor) return null;
  if (monitor.state === 'overdue') {
    const delay = monitor.overdueSeconds;
    return `${source.name} 수집 예정 시각${delay != null && delay > 0 ? `보다 ${durationLabel(delay)}` : '이'} 지났습니다. 앱 실행·절전·실행 기록을 확인하세요.`;
  }
  if (monitor.state === 'never') return `${source.name} 자동 수집은 켜져 있지만 아직 탐색 기록이 없습니다.`;
  return null;
}
