/** 이 PC 에만 저장하는 화면 설정 (글자 크기, 테마, 분할 폭, 마지막 보기·필터). 실패해도 기본값으로 동작. */
import { useCallback, useEffect, useState } from 'react';

export function readPref<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(`worklead.${key}`);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function writePref<T>(key: string, value: T): void {
  try {
    window.localStorage.setItem(`worklead.${key}`, JSON.stringify(value));
  } catch {
    // 저장 불가 — 무시
  }
}

export function usePref<T>(key: string, fallback: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => readPref(key, fallback));
  const set = useCallback(
    (v: T) => {
      setValue(v);
      writePref(key, v);
    },
    [key],
  );
  return [value, set];
}

export type FontScale = 'normal' | 'large' | 'xlarge';
export type ThemePref = 'system' | 'light' | 'dark';

const SCALE: Record<FontScale, string> = { normal: '100%', large: '106.25%', xlarge: '112.5%' };

export function applyDisplayPrefs(scale: FontScale, theme: ThemePref): void {
  const root = document.documentElement;
  root.style.fontSize = SCALE[scale];
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
}

export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : true));
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [query]);
  return match;
}
