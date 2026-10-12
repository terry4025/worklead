import { useCallback, useEffect, useRef, useState } from 'react';
import { isTauri } from '../../platform/bridge';
import { desktopExit, desktopHide, getDesktopSettings, updateDesktopSettings, type DesktopSettings, type DesktopSettingsPatch } from '../../platform/desktop';
import { Button } from '../../ui/Button';

type Action = 'load' | 'save' | 'hide' | 'exit';
const failure: Record<Action, string> = {
  load: '이 PC의 데스크톱 설정을 불러오지 못했습니다. 다시 시도하세요.',
  save: '데스크톱 설정 저장 결과를 확인하지 못했습니다. 설정을 다시 불러오세요.',
  hide: '트레이로 숨기지 못했습니다. 다시 시도하세요.',
  exit: '앱을 종료하지 못했습니다. 다시 시도하세요.',
};

export function DesktopSettingsPanel() {
  const native = isTauri();
  const [settings, setSettings] = useState<DesktopSettings | null>(null);
  const [pending, setPending] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const busy = useRef(false);
  const mounted = useRef(false);
  const sequence = useRef(0);

  const run = useCallback(async (action: Action, task: () => Promise<DesktopSettings | void>) => {
    // React state alone does not guard two events before the next render.
    if (busy.current) return;
    const request = ++sequence.current;
    busy.current = true;
    setPending(action);
    setError(null);
    setSaved(false);
    try {
      const result = await task();
      if (mounted.current && request === sequence.current) {
        if (result) setSettings(result);
        if (action === 'save') setSaved(true);
      }
    } catch {
      if (mounted.current && request === sequence.current) setError(failure[action]);
    } finally {
      if (request === sequence.current) {
        busy.current = false;
        if (mounted.current) setPending(null);
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (native) void run('load', getDesktopSettings);
    return () => {
      mounted.current = false;
      sequence.current += 1;
      busy.current = false;
    };
  }, [native, run]);

  if (!native) return null;
  const disabled = !settings || pending !== null;
  const save = (patch: DesktopSettingsPatch) => {
    if (disabled || (patch.close_to_tray && !settings?.tray_available)) return;
    void run('save', () => updateDesktopSettings(patch));
  };

  return (
    <fieldset aria-busy={pending !== null}>
      <legend>데스크톱 (이 PC)</legend>
      <p className="field-hint">변경 즉시 저장됩니다. 아래 저장 버튼은 내 조건·수집·알림 종류 설정에 적용됩니다.</p>
      {pending === 'load' ? <p className="muted-text" role="status">데스크톱 설정 불러오는 중…</p> : null}
      <label className="toggle">
        <input type="checkbox" checked={settings?.close_to_tray ?? false} disabled={disabled || !settings?.tray_available}
          onChange={(e) => save({ close_to_tray: e.target.checked })} />
        <span>창을 닫으면 트레이로 숨기기</span>
      </label>
      {settings && !settings.tray_available ? <p className="field-hint">트레이를 사용할 수 없어 창을 숨길 수 없습니다.</p> : null}
      <label className="toggle">
        <input type="checkbox" checked={settings?.native_notifications ?? false} disabled={disabled}
          onChange={(e) => save({ native_notifications: e.target.checked })} />
        <span>Windows 알림 사용</span>
      </label>
      <p className="field-hint">OS 알림에는 리드 제목·연락처를 넣지 않습니다. 알림 종류와 조용한 시간 설정이 적용됩니다. 다시 켠 시점 이후의 알림만 표시합니다. Windows 알림·방해 금지 설정에 따라 보이지 않을 수 있습니다.</p>
      <label className="toggle">
        <input type="checkbox" checked={settings?.autostart_enabled ?? false} disabled={disabled}
          onChange={(e) => save({ autostart_enabled: e.target.checked })} />
        <span>Windows 로그인 시 자동 시작</span>
      </label>
      {pending === 'save' ? <p className="field-hint" role="status">데스크톱 설정 저장 중…</p> : saved ? <p className="save-state save-saved" role="status">데스크톱 설정 저장됨</p> : null}
      {error ? <p className="field-error" role="alert">{error}</p> : null}
      {error ? <Button size="sm" disabled={pending !== null} onClick={() => void run('load', getDesktopSettings)}>데스크톱 설정 다시 불러오기</Button> : null}
      <p className="field-hint">트레이에 숨겨도 앱과 켜 둔 자동 수집은 계속 실행됩니다.</p>
      <div className="field-inline">
        <Button size="sm" busy={pending === 'hide'} disabled={pending !== null || !settings?.tray_available} onClick={() => void run('hide', desktopHide)}>트레이로 숨기기</Button>
        <Button size="sm" variant="ghost" busy={pending === 'exit'} disabled={pending !== null} onClick={() => void run('exit', desktopExit)}>Worklead 종료</Button>
      </div>
    </fieldset>
  );
}
