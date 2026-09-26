/**
 * 외부 링크 열기. 검증된 http/https 만 시스템 브라우저로 연다.
 * javascript:, file:, data:, 사용자 정의 스킴은 거부한다.
 */
import { isTauri } from './bridge';

export function safeExternalUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  // 제어 문자·공백이 섞인 주소는 거부 (스킴 우회 방지)
  if (/[\u0000-\u001f\u007f\s]/.test(trimmed)) return null;
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (!url.hostname || url.username || url.password) return null;
  return url.toString();
}

export type OpenResult = 'opened' | 'copied' | 'rejected';

export async function openExternal(raw: string | null | undefined): Promise<OpenResult> {
  const url = safeExternalUrl(raw);
  if (!url) return 'rejected';
  if (isTauri()) {
    try {
      const { openUrl } = await import('@tauri-apps/plugin-opener');
      await openUrl(url);
      return 'opened';
    } catch {
      // 셸이 열 수 없으면 주소를 복사해 사용자가 직접 열도록 한다
    }
  } else {
    const w = window.open(url, '_blank', 'noopener,noreferrer');
    if (w) return 'opened';
  }
  try {
    await navigator.clipboard.writeText(url);
    return 'copied';
  } catch {
    return 'rejected';
  }
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
