// 대시보드 관리자 로그인 상태 (토큰은 이 브라우저의 localStorage 에 보관)
//
// 서버(대시보드·통계 API, 실시간 연결)가 토큰을 확인하므로, 여기서는 토큰을 보관·전달하고
// 없거나 만료되면 로그인 화면으로 보내는 일만 한다.

import { useSyncExternalStore } from 'react';

const STORAGE_KEY = 'dcu_dashboard_session';
const CHANGE_EVENT = 'dcu-session-change';

export interface Session {
  token: string;
  username: string;
  expiresAt: number; // 만료 시각 (ms)
}

function parse(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    const s = JSON.parse(raw) as Session;
    return typeof s.token === 'string' && typeof s.expiresAt === 'number' ? s : null;
  } catch {
    return null;
  }
}

function readRaw(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** 지금 유효한(만료 전) 로그인 정보 */
export function getSession(): Session | null {
  const session = parse(readRaw());
  return session && session.expiresAt > Date.now() ? session : null;
}

export function getToken(): string | null {
  return getSession()?.token ?? null;
}

export function saveSession(session: Session): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch { /* 저장 불가(사생활 보호 모드 등) — 이 탭에서만 로그인 유지되지 않음 */ }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

// 신뢰 기기 토큰: 로그인에 성공한 브라우저임을 서버가 알아보는 값. 로그아웃해도 지우지 않는다
// (다른 사람이 틀린 로그인으로 잠금을 걸어도, 이 브라우저는 따로 세어 계속 로그인할 수 있음)
const DEVICE_KEY = 'dcu_dashboard_device';

export function getDeviceToken(): string | null {
  try {
    return localStorage.getItem(DEVICE_KEY);
  } catch {
    return null;
  }
}

export function saveDeviceToken(token: string): void {
  try {
    localStorage.setItem(DEVICE_KEY, token);
  } catch { /* 저장 불가 — 다음 로그인 때 신뢰 기기로 인정받지 못할 뿐 */ }
}

/** 로그아웃 (토큰이 만료·거부됐을 때도 호출 → 로그인 화면으로 이동) */
export function clearSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch { /* ignore */ }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

function subscribe(onChange: () => void): () => void {
  // 같은 탭의 로그인·로그아웃(CHANGE_EVENT)과 다른 탭의 변경(storage)을 함께 감지
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener('storage', onChange);
  // 열어 둔 채 만료 시각이 지나도 로그인 화면으로 가도록 주기적으로 다시 확인
  const timer = setInterval(onChange, 30_000);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener('storage', onChange);
    clearInterval(timer);
  };
}

// 스냅샷은 문자열(같은 값이면 같은 결과)로 돌려줘야 화면이 불필요하게 다시 그려지지 않는다
const getSnapshot = () => {
  const session = getSession();
  return session ? JSON.stringify(session) : null;
};
const getServerSnapshot = () => undefined;

/** 로그인 정보: undefined = 아직 확인 전(서버 렌더링·첫 화면), null = 로그인 안 됨 */
export function useSession(): Session | null | undefined {
  const raw = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return raw === undefined ? undefined : parse(raw);
}
