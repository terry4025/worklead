/** PC shell preferences are saved by Tauri, independently of backend settings. */
import { invoke } from '@tauri-apps/api/core';
import { isTauri } from './bridge';

export interface DesktopSettings {
  close_to_tray: boolean;
  native_notifications: boolean;
  autostart_enabled: boolean;
  tray_available: boolean;
}

export type DesktopSettingsPatch = Partial<Omit<DesktopSettings, 'tray_available'>>;

async function invokeDesktop<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!isTauri()) throw new Error('데스크톱 앱에서만 사용할 수 있습니다.');
  return invoke<T>(command, args);
}

export function getDesktopSettings(): Promise<DesktopSettings> {
  return invokeDesktop<DesktopSettings>('get_desktop_settings');
}

export function updateDesktopSettings(patch: DesktopSettingsPatch): Promise<DesktopSettings> {
  return invokeDesktop<DesktopSettings>('update_desktop_settings', { patch });
}

export function desktopHide(): Promise<void> {
  return invokeDesktop<void>('desktop_hide');
}

export function desktopExit(): Promise<void> {
  return invokeDesktop<void>('desktop_exit');
}
