import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { createPort, readScenario, resolveMode, saveScenario } from '../data';
import type { ScenarioId } from '../data/mock/scenarios';
import { restartBackend } from '../platform/bridge';
import { Button } from '../ui/Button';
import { ErrorState } from '../ui/States';
import { ToastProvider } from '../ui/Toasts';
import { PortProvider, usePort } from './PortContext';
import { Workspace } from './Workspace';
import { applyDisplayPrefs, readPref, type FontScale, type ThemePref } from './prefs';

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 로컬 서비스: 같은 오류를 여러 번 재요청하지 않는다. 창 포커스로 목록을 바꾸지 않는다.
        retry: (count, err) => count < 1 && (err as { kind?: string })?.kind === 'connection',
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      mutations: { retry: false },
    },
  });
}

/** 연결 확인 → 작업 화면. 연결이 안 되면 데모로 바꾸지 않고 원인을 보여준다. */
function Gate({ onDemoReset }: { onDemoReset: () => void }) {
  const port = usePort();
  const boot = useQuery({ queryKey: ['bootstrap'], queryFn: () => port.bootstrap(), retry: 1 });
  if (boot.isPending) {
    return (
      <div className="boot" aria-busy="true">
        <p>로컬 서비스에 연결하는 중…</p>
      </div>
    );
  }
  if (boot.isError) {
    return (
      <div className="boot">
        <ErrorState
          error={boot.error}
          onRetry={() => void boot.refetch()}
          extra={
            port.mode === 'live' ? (
              <Button
                size="sm"
                onClick={async () => {
                  await restartBackend();
                  void boot.refetch();
                }}
              >
                서비스 다시 시작
              </Button>
            ) : (
              <Button size="sm" onClick={onDemoReset}>
                데모: 정상 시나리오로
              </Button>
            )
          }
        />
        <p className="field-hint">수집·분석 데이터는 사용자 데이터 폴더에 그대로 있습니다. 로그: AppData\Local\Worklead\logs</p>
      </div>
    );
  }
  return <Workspace />;
}

export function App() {
  const mode = useMemo(() => resolveMode(), []);
  const [scenario, setScenarioState] = useState<ScenarioId>(() => readScenario());
  const port = useMemo(() => createPort(mode, scenario), [mode, scenario]);
  const client = useMemo(() => makeClient(), [port]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    applyDisplayPrefs(readPref<FontScale>('fontScale', 'normal'), readPref<ThemePref>('theme', 'system'));
  }, []);

  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <PortProvider
          value={{
            port,
            mode,
            scenario: mode === 'demo' ? scenario : null,
            setScenario: (id) => {
              saveScenario(id);
              setScenarioState(id);
            },
          }}
        >
          <Gate
            onDemoReset={() => {
              saveScenario('normal');
              setScenarioState('normal');
            }}
          />
        </PortProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
