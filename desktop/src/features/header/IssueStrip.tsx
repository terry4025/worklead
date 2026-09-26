import { useState } from 'react';
import { policyLabel } from '../../domain/labels';
import type { RunInfo, SourceInfo, StreamStatus } from '../../domain/model';
import { formatRelative, formatTime } from '../../domain/format';
import type { Tone } from '../../domain/present';
import { StatusMark } from '../../ui/StatusMark';

export interface Issue {
  key: string;
  tone: Tone;
  text: string;
  action?: { label: string; run: () => void };
}

/** 숨기지 않아야 할 상태: 차단·부분 수집·권한 대기·AI 실패·연결 문제 */
export function deriveIssues({
  sources,
  runs,
  stream,
  mode,
  openCollection,
  now,
}: {
  sources: SourceInfo[];
  runs: RunInfo[];
  stream: StreamStatus;
  mode: 'demo' | 'live';
  openCollection: () => void;
  now: Date;
}): Issue[] {
  const out: Issue[] = [];
  const detail = { label: '자세히', run: openCollection };
  if (stream === 'retrying' || stream === 'closed') {
    out.push({ key: 'stream', tone: 'warn', text: '실시간 연결을 다시 시도하는 중 — 목록·상태가 최신이 아닐 수 있습니다' });
  }
  for (const s of sources.filter((x) => x.kind === 'site')) {
    if (s.stoppedReason) {
      out.push({ key: `stop-${s.id}`, tone: 'bad', text: `${s.name} 자동 수집 정지 — ${s.health.message ?? s.stoppedReason} 기존 리드의 모집 상태는 바꾸지 않았습니다.`, action: detail });
      continue;
    }
    if (s.policy.status !== 'allowed' && s.policy.status !== 'restricted') {
      const research = s.research.ready ? '' : ' · 사이트 조사 미완료';
      out.push({
        key: `policy-${s.id}`,
        tone: 'unknown',
        text: `${s.name} 자동 수집: ${policyLabel[s.policy.status]}${research} — 수동 입력은 사용할 수 있습니다`,
        action: { label: '수집 설정', run: openCollection },
      });
    } else if (s.health.status === 'degraded' || s.health.status === 'error') {
      out.push({ key: `health-${s.id}`, tone: 'warn', text: `${s.name}: ${s.health.message ?? '일부 오류'}`, action: detail });
    }
  }
  const last = runs.find((r) => r.kind === 'discovery' && ['succeeded', 'partial', 'failed'].includes(r.state));
  if (last && last.state === 'partial') {
    const retry = last.error?.retryAfter ? ` · ${formatTime(last.error.retryAfter)} 이후 재개` : '';
    out.push({
      key: `run-${last.id}`,
      tone: 'warn',
      text: `최근 탐색 부분 완료 — 계획 ${last.progress.total ?? '?'}개 중 ${last.progress.done}개 처리 (${last.error?.message ?? '일부 실패'})${retry}`,
      action: detail,
    });
  }
  if (last && last.state === 'failed' && !sources.some((s) => s.stoppedReason)) {
    out.push({ key: `runf-${last.id}`, tone: 'bad', text: `최근 탐색 실패 — ${last.error?.message ?? '원인 미상'} (${last.finishedAt ? formatRelative(last.finishedAt, now) : ''})`, action: detail });
  }
  if (last && last.counts.aiFailures > 0) {
    out.push({ key: `ai-${last.id}`, tone: 'warn', text: `AI 분석 실패 ${last.counts.aiFailures}건 — 규칙 기반 판정·근거로 표시 중` });
  }
  if (last && last.state === 'succeeded' && last.counts.created === 0 && last.counts.detailsFetched === 0 && mode) {
    out.push({ key: `empty-${last.id}`, tone: 'good', text: `최근 탐색 정상 완료 — 새 글 0건 (요청 ${last.counts.requests}회, 오류 없음)` });
  }
  return out;
}

export function IssueStrip({ issues }: { issues: Issue[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!issues.length) return null;
  const shown = expanded ? issues : issues.slice(0, 2);
  return (
    <div className="issues" role="region" aria-label="수집·연결 상태 알림">
      {shown.map((i) => (
        <div key={i.key} className={`issue issue-${i.tone}`}>
          <StatusMark tone={i.tone}>{i.text}</StatusMark>
          {i.action ? (
            <button type="button" className="link-btn" onClick={i.action.run}>
              {i.action.label}
            </button>
          ) : null}
        </div>
      ))}
      {issues.length > 2 ? (
        <button type="button" className="link-btn issues-more" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
          {expanded ? '접기' : `외 ${issues.length - 2}건`}
        </button>
      ) : null}
    </div>
  );
}
