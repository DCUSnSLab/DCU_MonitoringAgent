import { API_BASE, clearSession, refreshSession } from './auth';

export type DashboardRole = 'pending' | 'viewer' | 'admin';
export interface DashboardUser {
  sub: string;
  username: string;
  name: string;
  email: string;
  role: DashboardRole;
  enabled: boolean;
}
export interface UserList {
  users: DashboardUser[];
  total: number;
  current_sub: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/api/admin${path}`, {
    ...init, credentials: 'include', cache: 'no-store',
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) clearSession();
  if (response.status === 403) void refreshSession();
  if (!response.ok) {
    throw new Error(typeof body.detail === 'string' ? body.detail : '사용자 권한을 처리할 수 없습니다.');
  }
  return body;
}

export function getDashboardUsers(q: string, offset: number, signal?: AbortSignal): Promise<UserList> {
  return request(`/users?${new URLSearchParams({ q, offset: String(offset), limit: '50' })}`, { signal });
}

export function updateDashboardUser(sub: string, role: DashboardRole, enabled: boolean): Promise<DashboardUser> {
  return request(`/users/${encodeURIComponent(sub)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, enabled }),
  });
}
