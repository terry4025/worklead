import { useRef, useState } from 'react';
import type { StreamStatus } from '../../domain/model';
import { isTauri, restartBackend } from '../../platform/bridge';
import { useToast } from '../../ui/Toasts';

/** Recovery remains reachable if the sidecar stops after bootstrap succeeded. */
export function ServiceRecovery({ mode, stream }: { mode: 'demo' | 'live'; stream: StreamStatus }) {
  const toast = useToast();
  const active = useRef(false);
  const [busy, setBusy] = useState(false);
  if (mode !== 'live' || !isTauri() || !['retrying', 'closed'].includes(stream)) return null;
  return <button type="button" className="tb-btn" disabled={busy} onClick={async () => {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    try {
      const ready = await restartBackend();
      if (!ready) toast.show({ tone: 'bad', text: '로컬 서비스를 다시 시작하지 못했습니다. 앱을 종료한 뒤 다시 실행해주세요.' });
    } finally {
      active.current = false;
      setBusy(false);
    }
  }}>
    {busy ? '서비스 연결 중…' : '서비스 다시 시작'}
  </button>;
}
