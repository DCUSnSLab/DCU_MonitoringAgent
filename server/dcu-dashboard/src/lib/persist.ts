// 화면에서 고른 필터·정렬을 이 브라우저에 저장해 새로고침·재접속 뒤에도 유지한다 (localStorage)
//
// - 서버 렌더링과 첫 화면(하이드레이션)에서는 기본값을 쓰고, 이후 저장된 값으로 바뀐다.
// - 같은 브라우저의 다른 탭과는 실시간으로 맞추지 않는다 (각 탭은 연 시점의 값에서 따로 움직임).
// - 저장소를 쓸 수 없으면(사생활 보호 모드 등) 이 탭 안에서만 유지된다.

import { useCallback, useMemo, useSyncExternalStore } from 'react';

const PREFIX = 'dcu_dashboard_filter:';

const cache = new Map<string, string | null>(); // 이 탭에서 쓰는 값 (처음 읽을 때 저장소에서 가져옴)
const listeners = new Set<() => void>();

function readRaw(key: string): string | null {
  if (!cache.has(key)) {
    let raw: string | null = null;
    try {
      raw = localStorage.getItem(PREFIX + key);
    } catch { /* 저장소 사용 불가 */ }
    cache.set(key, raw);
  }
  return cache.get(key) ?? null;
}

function writeRaw(key: string, raw: string | null): void {
  cache.set(key, raw);
  try {
    if (raw === null) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, raw);
  } catch { /* 저장 불가 — 이 탭 안에서만 유지 */ }
  listeners.forEach((notify) => notify());
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

function parse<T>(raw: string | null, fallback: T, isValid: (v: unknown) => boolean): T {
  if (raw === null) return fallback;
  try {
    const value: unknown = JSON.parse(raw);
    return isValid(value) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

/**
 * 저장되는 상태. isValid 로 저장값을 검사해 맞지 않으면(옛 형식·손상) 기본값을 쓴다.
 * fallback(객체인 경우)과 isValid 는 컴포넌트 밖 상수로 넘길 것 (매번 새로 만들면 값이 바뀐 것으로 보임).
 */
export function usePersistentState<T>(
  key: string,
  fallback: T,
  isValid: (v: unknown) => boolean,
): [T, (next: T | ((prev: T) => T)) => void] {
  const raw = useSyncExternalStore(subscribe, () => readRaw(key), () => null);
  const value = useMemo(() => parse(raw, fallback, isValid), [raw, fallback, isValid]);

  const setValue = useCallback(
    (next: T | ((prev: T) => T)) => {
      const prev = parse(readRaw(key), fallback, isValid);
      const resolved = typeof next === 'function' ? (next as (p: T) => T)(prev) : next;
      writeRaw(key, JSON.stringify(resolved));
    },
    [key, fallback, isValid],
  );
  return [value, setValue];
}

/** 저장된 값을 지워 기본값으로 되돌린다 */
export function clearPersistentState(...keys: string[]): void {
  keys.forEach((key) => writeRaw(key, null));
}

export const isString = (v: unknown): boolean => typeof v === 'string';
export const isBoolean = (v: unknown): boolean => typeof v === 'boolean';
export const isOneOf = (options: readonly string[]) => (v: unknown): boolean =>
  typeof v === 'string' && options.includes(v);
