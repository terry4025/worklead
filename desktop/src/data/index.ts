/**
 * 데이터 계층 선택. 데모와 실제 데이터를 섞지 않는다.
 * - Tauri 안: live (연결 실패 시 데모로 바꾸지 않고 오류를 보여준다)
 * - 브라우저: VITE_DATA_MODE 또는 ?mode= 로 지정, 기본 demo
 */
import { isTauri, resolveConnection } from '../platform/bridge';
import { LivePort } from './live/livePort';
import { MockPort } from './mock/mockPort';
import { SCENARIOS, type ScenarioId } from './mock/scenarios';
import type { WorkleadPort } from './port';

export type DataMode = 'demo' | 'live';

const SCENARIO_KEY = 'worklead.demoScenario';

export function resolveMode(): DataMode {
  if (isTauri()) return 'live';
  const q = new URLSearchParams(window.location.search).get('mode');
  if (q === 'live' || q === 'demo') return q;
  return import.meta.env.VITE_DATA_MODE === 'live' ? 'live' : 'demo';
}

export function readScenario(): ScenarioId {
  const q = new URLSearchParams(window.location.search).get('scenario');
  const valid = (v: string | null): v is ScenarioId => !!v && SCENARIOS.some((s) => s.id === v);
  if (valid(q)) return q;
  try {
    const stored = window.localStorage.getItem(SCENARIO_KEY);
    if (valid(stored)) return stored;
  } catch {
    // 저장소 접근 불가 — 기본값
  }
  return 'normal';
}

export function saveScenario(id: ScenarioId): void {
  try {
    window.localStorage.setItem(SCENARIO_KEY, id);
  } catch {
    // 무시
  }
}

export function createPort(mode: DataMode, scenario: ScenarioId): WorkleadPort {
  if (mode === 'live') return new LivePort(resolveConnection);
  return new MockPort({ scenario });
}
