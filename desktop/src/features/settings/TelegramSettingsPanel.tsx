import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { usePortCtx } from '../../app/PortContext';
import { PortError } from '../../data/port';
import type { TelegramStatus } from '../../domain/model';
import { Button } from '../../ui/Button';

export const TELEGRAM_STATUS_KEY = ['telegram-status'] as const;

function label(status: TelegramStatus): string {
  if (status.enabled) return '연결됨 · 새 제작 의뢰를 휴대폰으로 보냅니다';
  if (status.keyConfigured && !status.chatConnected) return '토큰 저장됨 · 봇에게 메시지를 보낸 뒤 연결하세요';
  if (status.keyConfigured === null) return '토큰 저장 상태 확인 불가';
  return '토큰 미설정';
}

export function TelegramSettingsPanel({ disabled = false }: { disabled?: boolean }) {
  const { port, mode } = usePortCtx();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: TELEGRAM_STATUS_KEY, queryFn: () => port.getTelegramStatus(), retry: false, refetchOnWindowFocus: false });
  const input = useRef<HTMLInputElement>(null);
  const [hasInput, setHasInput] = useState(false);
  const [pending, setPending] = useState<'save' | 'connect' | 'delete' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const allowed = mode !== 'demo' && !disabled && !pending && status.data?.keySupported !== false;

  const act = async (kind: 'save' | 'connect' | 'delete') => {
    if (!allowed) return;
    const token = input.current?.value ?? '';
    // The token is used for this request only and cleared immediately.
    if (input.current) input.current.value = '';
    setHasInput(false);
    setPending(kind); setError(null); setNotice(null);
    try {
      const next = kind === 'save' ? await port.saveTelegramToken(token) : kind === 'connect' ? await port.connectTelegram() : await port.deleteTelegram();
      qc.setQueryData(TELEGRAM_STATUS_KEY, next);
      setNotice(kind === 'connect' ? '테스트 메시지를 보냈습니다. 휴대폰에서 확인하세요.' : kind === 'save' ? '토큰을 저장했습니다. 이제 봇에게 메시지를 보낸 뒤 연결하세요.' : '텔레그램 알림을 해제했습니다.');
    } catch (err) {
      setError(err instanceof PortError && err.message ? err.message : '텔레그램 작업을 완료하지 못했습니다. 다시 시도하세요.');
      void qc.invalidateQueries({ queryKey: TELEGRAM_STATUS_KEY });
    } finally { setPending(null); }
  };

  return <fieldset aria-busy={pending !== null}>
    <legend>휴대폰 알림 (텔레그램)</legend>
    <p className="field-hint">새 제작 의뢰가 잡히면 1분 안에 내 텔레그램으로 제목·보수·원문 링크를 보냅니다. 위 알림 종류와 조용한 시간 설정을 그대로 따릅니다.</p>
    <ol className="field-hint">
      <li>텔레그램에서 @BotFather에게 /newbot을 보내 봇을 만들고 토큰을 복사합니다.</li>
      <li>아래에 토큰을 저장합니다.</li>
      <li>휴대폰에서 만든 봇을 열어 아무 메시지나 보낸 뒤 연결을 누릅니다.</li>
    </ol>
    <label className="field"><span className="field-label">봇 토큰</span><input ref={input} type="password" autoComplete="off" spellCheck={false} maxLength={200} disabled={!allowed} onChange={() => setHasInput(!!input.current?.value)} /></label>
    <div className="field-inline">
      <Button size="sm" busy={pending === 'save'} disabled={!allowed || !hasInput} onClick={() => void act('save')}>토큰 저장</Button>
      <Button size="sm" busy={pending === 'connect'} disabled={!allowed || status.data?.keyConfigured !== true} onClick={() => void act('connect')}>연결 · 테스트 메시지</Button>
      <Button size="sm" variant="ghost" busy={pending === 'delete'} disabled={!allowed || status.data?.keyConfigured !== true} onClick={() => void act('delete')}>해제</Button>
    </div>
    <p className="field-hint">토큰은 이 PC의 Windows 보안 저장소에만 보관하며 설정·백업·내보내기에 포함하지 않습니다. 앱이 켜져 있을 때만 보냅니다.</p>
    {mode === 'demo' ? <p className="field-hint">데모에서는 토큰을 저장하거나 메시지를 보내지 않습니다.</p>
      : status.isError ? <p className="field-error" role="alert">텔레그램 상태를 불러오지 못했습니다.</p>
      : status.data ? <p className="field-hint" role="status">현재 상태: {label(status.data)}</p> : null}
    {notice ? <p className="field-hint" role="status">{notice}</p> : null}
    {error ? <p className="field-error" role="alert">{error}</p> : null}
  </fieldset>;
}
