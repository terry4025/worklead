import { createContext, useContext, type ReactNode } from 'react';
import type { DataMode } from '../data';
import type { ScenarioId } from '../data/mock/scenarios';
import type { WorkleadPort } from '../data/port';

interface PortCtx {
  port: WorkleadPort;
  mode: DataMode;
  scenario: ScenarioId | null;
  setScenario: (id: ScenarioId) => void;
}

const Ctx = createContext<PortCtx | null>(null);

export function PortProvider({ value, children }: { value: PortCtx; children: ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePortCtx(): PortCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error('PortProvider 가 없습니다');
  return v;
}

export function usePort(): WorkleadPort {
  return usePortCtx().port;
}
