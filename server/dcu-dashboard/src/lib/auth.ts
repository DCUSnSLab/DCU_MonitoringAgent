// SSO 토큰은 서버에만 보관한다. 브라우저는 HttpOnly 세션 쿠키를 사용한다.
import { useSyncExternalStore } from 'react';

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const STORAGE_KEY = 'dcu_dashboard_session';
const CHANGE_EVENT = 'dcu-session-change';

export interface Session {
  token?: string;
  username: string;
  expiresAt?: number;
  mode?: 'local' | 'sso';
  role?: string;
  sub?: string;
}

interface AuthState {
  mode?: 'local' | 'sso';
  session?: Session | null;
  error?: string;
}

let state: AuthState = {};
let snapshot = JSON.stringify(state);
let pending: Promise<void> | undefined;

function publish(next: AuthState) {
  state = next;
  snapshot = JSON.stringify(next);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

function readLocal(): Session | null {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    return value && typeof value.token === 'string' && typeof value.username === 'string'
      && typeof value.expiresAt === 'number' && value.expiresAt > Date.now() ? value : null;
  } catch { return null; }
}

export function getSession(): Session | null {
  return state.mode === 'local' ? readLocal() : state.session ?? null;
}

export function getToken(): string | null {
  return state.mode === 'local' ? getSession()?.token ?? null : null;
}

export function saveSession(session: Session): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  publish({ mode: 'local', session });
}

export function clearSession(): void {
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* SSO는 브라우저 저장소 없이 동작 */ }
  publish({ ...state, session: null });
}

export function refreshSession(): Promise<void> {
  if (pending) return pending;
  pending = (async () => {
    try {
      let mode = state.mode;
      if (!mode) {
        const response = await fetch(`${API_BASE}/api/auth/config`, { cache: 'no-store' });
        if (!response.ok) throw new Error('인증 설정을 확인할 수 없습니다. 서버 연결을 확인하세요.');
        mode = (await response.json()).mode;
        if (mode !== 'local' && mode !== 'sso') throw new Error('인증 설정이 올바르지 않습니다.');
      }
      if (mode === 'local') {
        publish({ mode, session: readLocal() });
        return;
      }
      const response = await fetch(`${API_BASE}/api/auth/session`, {
        credentials: 'include', cache: 'no-store',
      });
      if (response.status === 401) publish({ mode, session: null });
      else if (response.ok) publish({ mode, session: await response.json() });
      else throw new Error('로그인 상태를 확인할 수 없습니다. 잠시 후 다시 시도하세요.');
    } catch (error) {
      publish({ ...state, session: state.session ?? null,
        error: error instanceof Error ? error.message : '서버에 연결할 수 없습니다.' });
    }
  })().finally(() => { pending = undefined; });
  return pending;
}

export function startSSOLogin() {
  window.location.assign(`${API_BASE}/api/auth/sso/login`);
}

export async function logout(): Promise<void> {
  if (state.mode === 'sso') {
    const response = await fetch(`${API_BASE}/api/auth/logout`, {
      method: 'POST', credentials: 'include', cache: 'no-store',
    });
    if (!response.ok) throw new Error('로그아웃에 실패했습니다. 다시 시도하세요.');
    const body = await response.json();
    clearSession();
    window.location.assign(body.logout_url);
  } else clearSession();
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener('storage', refreshSession);
  void refreshSession();
  const timer = setInterval(refreshSession, 30_000);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener('storage', refreshSession);
    clearInterval(timer);
  };
}

const getSnapshot = () => snapshot;
const getServerSnapshot = () => '{}';

export function useAuthState(): AuthState {
  return JSON.parse(useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot));
}

export function useSession(): Session | null | undefined {
  return useAuthState().session;
}
