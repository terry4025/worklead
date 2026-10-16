import { formatDateTime, formatRelative } from '../../domain/format';
import { durationLabel, monitoringWarning } from '../../domain/freshness';
import type { SourceInfo } from '../../domain/model';

export function SourceMonitoring({ source, now = new Date() }: { source: SourceInfo; now?: Date }) {
  const monitor = source.monitoring;
  if (!monitor) return null;
  const warning = monitoringWarning(source);
  const gap = monitor.previousGapSeconds;
  const longGap = gap != null && monitor.intervalMinutes != null && monitor.intervalMinutes > 0 && gap >= monitor.intervalMinutes * 120;
  return <div className="coverage" aria-label={`${source.name} 수집 일정`}>
    <div className="cov-row"><span className="cov-label">수집 상태</span><span>{{ disabled: '자동 수집 꺼짐', stopped: '수집 정지', never: '첫 탐색 대기', running: '탐색 진행 중', waiting: '다음 탐색 대기', overdue: '탐색 일정 지연' }[monitor.state]}</span></div>
    <div className="cov-row"><span className="cov-label">최근 시작</span><span>{monitor.lastStartedAt ? formatRelative(monitor.lastStartedAt, now) : '기록 없음'}</span></div>
    <div className="cov-row"><span className="cov-label">최근 종료</span><span>{monitor.lastFinishedAt ? formatDateTime(monitor.lastFinishedAt, now) : '기록 없음'}</span></div>
    {monitor.nextDueAt ? <div className="cov-row"><span className="cov-label">다음 예정</span><span>{formatDateTime(monitor.nextDueAt, now)}</span></div> : null}
    {gap != null ? <div className="cov-row"><span className="cov-label">직전 수집 공백</span><span>{durationLabel(gap)}</span></div> : null}
    {longGap ? <p className="field-error" role="status">직전 탐색 시작까지 수집 공백이 {durationLabel(gap!)} 있었습니다. 앱 실행·절전·실행 기록을 확인하세요.</p> : null}
    {warning ? <p className="field-error" role="status">{warning}</p> : null}
    <p className="field-hint">앱이 실행 중일 때만 수집합니다. 주기를 줄여도 사이트 목록 노출 지연·요청 예산·재시도 대기는 남습니다.</p>
  </div>;
}
