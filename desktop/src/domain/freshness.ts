import type { LeadSummary, SourceInfo } from './model';

/** 관찰된 두 시각의 차이만 표시한다. 일 단위·상대 날짜를 정밀한 지연으로 바꾸지 않는다. */
export function discoveryDelaySeconds(lead: LeadSummary): number | null {
  if (lead.published.precision !== 'exact' || !lead.published.at) return null;
  const delay = lead.freshness !== undefined
    ? lead.freshness?.discoveryDelaySeconds ?? null
    : (Date.parse(lead.firstSeenAt) - Date.parse(lead.published.at)) / 1000;
  return delay !== null && Number.isFinite(delay) && delay >= 0 ? delay : null;
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
