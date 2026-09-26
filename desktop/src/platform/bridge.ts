/**
 * 데스크톱 셸(Tauri) 브리지. 백엔드 주소·토큰은 셸의 제한된 명령으로만 받는다.
 * 브라우저 개발 모드에서는 환경 변수(VITE_WORKLEAD_API_URL/TOKEN)를 쓴다.
 */
import type { Connection } from '@contracts/index';
import { PortError } from '../data/port';

export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export async function resolveConnection(): Promise<Connection> {
  if (isTauri()) {
    const { invoke } = await import('@tauri-apps/api/core');
    try {
      const c = await invoke<{ base_url: string; token: string }>('backend_connection');
      return { baseUrl: c.base_url, token: c.token };
    } catch (err) {
      throw new PortError({
        kind: 'connection',
        code: 'sidecar_unavailable',
        message: typeof err === 'string' ? err : '로컬 서비스를 시작하지 못했습니다',
        retryable: true,
      });
    }
  }
  const url = import.meta.env.VITE_WORKLEAD_API_URL;
  const token = import.meta.env.VITE_WORKLEAD_TOKEN;
  if (url && token) return { baseUrl: url.replace(/\/$/, ''), token };
  throw new PortError({
    kind: 'connection',
    code: 'no_connection_config',
    message: '로컬 서비스 연결 정보가 없습니다 (브라우저 개발 모드: VITE_WORKLEAD_API_URL·VITE_WORKLEAD_TOKEN 필요)',
  });
}

/** 셸 쪽 백엔드 재시작 요청 (연결 실패 화면의 "다시 시작") */
export async function restartBackend(): Promise<boolean> {
  if (!isTauri()) return false;
  const { invoke } = await import('@tauri-apps/api/core');
  try {
    await invoke('restart_backend');
    return true;
  } catch {
    return false;
  }
}
