import { Database, HelpCircle, Settings } from 'lucide-react';
import type { RunInfo, SourceInfo, StreamStatus } from '../../domain/model';
import { SCENARIOS, type ScenarioId } from '../../data/mock/scenarios';
import { formatRelative } from '../../domain/format';
import { StatusGlyph } from '../../ui/StatusMark';

function coverageSummary(sources: SourceInfo[]): { text: string; title: string } {
  const site = sources.find((s) => s.kind === 'site' && s.coverage);
  const c = site?.coverage;
  if (!site || !c) return { text: '전국 목표 · 탐색 범위 없음', title: '자동 수집 소스가 없습니다' };
  if (c.planned === null) {
    return {
      text: '전국 목표 · 확인 범위 미확인',
      title: `${site.name}: 탐색 범위를 계획할 수 없습니다. ${c.notes.join(' ')}`,
    };
  }
  const units = c.targetUnitsCovered !== null && c.targetUnitsTotal ? ` · 시·도 ${c.targetUnitsCovered}/${c.targetUnitsTotal}` : '';
  const verified = c.regionListStatus === 'verified' ? '' : c.regionListStatus === 'not_applicable' ? ` (${c.unitLabel})` : ' (목록 미검증)';
  return {
    text: `전국 목표 · 이번 주기 ${c.completed}/${c.planned}${units}${verified}`,
    title: `탐색 목표는 전국이며, 표시 값은 계획한 탐색 작업 완료 수입니다. 전체 시장 포괄률은 알 수 없습니다.\n${c.notes.join('\n')}`,
  };
}

export function TopBar({
  mode,
  scenario,
  onScenario,
  sources,
  runs,
  stream,
  onCollection,
  onSettings,
  onHelp,
  now,
}: {
  mode: 'demo' | 'live';
  scenario: ScenarioId | null;
  onScenario: (id: ScenarioId) => void;
  sources: SourceInfo[];
  runs: RunInfo[];
  stream: StreamStatus;
  onCollection: () => void;
  onSettings: () => void;
  onHelp: () => void;
  now: Date;
}) {
  const running = runs.find((r) => r.state === 'running' || r.state === 'queued');
  const cov = coverageSummary(sources);
  const lastDone = runs.find((r) => r.kind === 'discovery' && r.finishedAt);
  const streamTone = stream === 'open' ? 'good' : stream === 'connecting' ? 'unknown' : 'warn';
  const streamText = stream === 'open' ? '실시간' : stream === 'connecting' ? '연결 중' : stream === 'retrying' ? '재연결 중' : '끊김';

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          W
        </span>
        <span className="brand-name">Worklead</span>
        {mode === 'demo' ? (
          <label className="demo-badge" title="합성 데이터입니다. 실제 수집 결과·성과와 섞이지 않습니다.">
            <span>데모 데이터</span>
            <select value={scenario ?? 'normal'} onChange={(e) => onScenario(e.target.value as ScenarioId)} aria-label="데모 시나리오">
              {SCENARIOS.map((s) => (
                <option key={s.id} value={s.id} title={s.note}>
                  시나리오: {s.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      <button type="button" className="run-summary" onClick={onCollection} title={cov.title}>
        {running ? (
          <>
            <span className="run-pulse" aria-hidden="true" />
            <span>
              {running.state === 'queued' ? '탐색 대기' : '탐색 중'} {running.progress.label ? `· ${running.progress.label}` : ''} ·{' '}
              <span className="num">
                {running.progress.done}/{running.progress.total ?? '?'}
              </span>
            </span>
            <span className="run-bar" aria-hidden="true">
              <span style={{ width: `${running.progress.total ? Math.min(100, (running.progress.done / running.progress.total) * 100) : 10}%` }} />
            </span>
          </>
        ) : (
          <>
            <span>{cov.text}</span>
            {lastDone?.finishedAt ? <span className="muted-text">· 마지막 탐색 {formatRelative(lastDone.finishedAt, now)}</span> : null}
          </>
        )}
      </button>

      <div className="topbar-actions">
        <span className="stream" title="백엔드 이벤트 연결 상태">
          <StatusGlyph tone={streamTone} />
          <span>{streamText}</span>
        </span>
        <button type="button" className="tb-btn" onClick={onCollection} title="수집 제어·수동 입력 (C)">
          <Database size={16} aria-hidden="true" />
          <span>수집</span>
        </button>
        <button type="button" className="tb-btn" onClick={onSettings} title="설정 (,)">
          <Settings size={16} aria-hidden="true" />
          <span>설정</span>
        </button>
        <button type="button" className="tb-btn tb-icon" onClick={onHelp} aria-label="단축키 도움말" title="단축키 (?)">
          <HelpCircle size={16} aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
