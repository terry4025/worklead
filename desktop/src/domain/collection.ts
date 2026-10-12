import type { RunInfo, SourceInfo } from './model';

export function emptyCollectionMessage(site: SourceInfo | undefined, runs: RunInfo[]): string {
  if (!site) return '직접 확보한 글을 수동 입력하거나 수집 상태를 확인하세요.';
  if (!site.research.ready || !['allowed', 'restricted'].includes(site.policy.status)) {
    return `${site.name} 자동 수집은 권한 확인·사이트 조사 전이라 실행하지 않습니다. 직접 확보한 글은 수동 입력으로 분석할 수 있습니다.`;
  }
  if (site.stoppedReason) return '수집이 정지되어 있습니다. 수집 화면에서 원인을 확인하세요.';
  const last = runs.find((r) => r.sourceId === site.id && r.kind === 'discovery');
  if (!last) return '아직 탐색 결과가 없습니다. 수집 화면에서 지금 탐색을 실행하거나 자동 수집 일정을 확인하세요.';
  if (['queued', 'running', 'paused'].includes(last.state)) return '탐색이 대기·진행 중이거나 일시정지되어 있습니다. 수집 화면에서 진행 상황을 확인하세요.';
  if (last.state !== 'succeeded') return '최근 탐색이 끝까지 완료되지 않았습니다. 수집 화면에서 실행 결과를 확인하세요.';
  return '최근 탐색은 완료됐지만 저장된 리드가 없습니다. 수집 범위와 제외 판정을 확인하세요.';
}
