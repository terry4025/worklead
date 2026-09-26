/**
 * 데스크톱 알림 (Tauri 셸에서만). 브라우저 개발 모드에서는 아무것도 하지 않는다.
 * 알림은 보기용이며 자동 연락·지원을 하지 않는다. 방해 금지 시간은 백엔드가 이미 걸러서 보낸다.
 */
import { isTauri } from './bridge';

let permission: boolean | null = null;

export async function nativeNotify(title: string, body?: string): Promise<void> {
  if (!isTauri()) return;
  try {
    const n = await import('@tauri-apps/plugin-notification');
    if (permission === null) {
      permission = await n.isPermissionGranted();
      if (!permission) permission = (await n.requestPermission()) === 'granted';
    }
    if (permission) n.sendNotification(body ? { title, body } : { title });
  } catch {
    // 알림 실패는 앱 동작에 영향을 주지 않는다 (앱 안 토스트는 그대로 표시됨)
  }
}
