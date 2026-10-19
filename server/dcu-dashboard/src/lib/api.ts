// API 타입 정의 및 클라이언트 함수

import { Session, clearSession, getDeviceToken, getToken, saveDeviceToken } from './auth';

export type UserState = 'safe' | 'warning' | 'danger' | 'unknown';

export interface AgentSummary {
  agent_id: string;
  hostname: string;
  lab_name: string | null;
  ip_address: string | null;
  current_state: UserState;
  is_online: boolean;
  last_seen_at: string | null;
  process_count: number;
  chrome_tab_count: number;
  alert_count: number;
  foreground_window: string | null;
  cpu_percent: number;
  memory_percent: number;
  agent_version?: string | null;
  os_version?: string | null;
  danger_reason?: string | null; // DANGER일 때 화면 앞에 띄운 탐지 대상 (DANGER 유지 시간 동안 함께 유지)
  danger_since?: string | null; // DANGER가 이어진 구간의 시작 시각
  danger_last_at?: string | null; // 마지막 DANGER 보고 시각
  danger_on_screen?: boolean; // 지금도 화면에 떠 있는지 (false면 내린 뒤 유지 중)
}

export interface ProcessInfo {
  pid: number;
  name: string;
  memory_mb: number;
  is_foreground: boolean;
  window_title: string | null;
}

export interface ChromeTab {
  tab_id: string;
  title: string;
  url: string;
  is_active: boolean;
}

export interface AlertInfo {
  level: string;
  code: string;
  message: string;
  process_name: string | null;
  url: string | null;
  detected_at: string | null;
}

// 에이전트가 지금 탐지 중인 항목 (대상별 1건)
export interface ActiveDetection {
  code: string; // BLOCKED_PROCESS | UNAUTHORIZED_PROCESS | BLOCKED_URL | MESSENGER_DETECTED
  target: string; // 프로세스명 / URL / 창 제목
  source: string; // process | url | title
  is_foreground: boolean; // 지금 화면에 띄워져 있는지
  on_screen_since?: string | null; // 화면 앞에 계속 떠 있기 시작한 시각 (화면 앞일 때만)
}

export interface AgentDetail extends AgentSummary {
  processes: ProcessInfo[];
  chrome_tabs: ChromeTab[];
  recent_alerts: AlertInfo[];
  active_detections?: ActiveDetection[]; // 구버전 서버 응답에는 없을 수 있음
  registered_at: string | null;
}

export interface AlertLog {
  id: number;
  agent_id: string;
  hostname: string | null;
  level: string;
  code: string;
  message: string;
  process_name: string | null;
  url: string | null;
  detected_at: string;
}

export interface DashboardSummary {
  total_agents: number;
  online_agents: number;
  safe_count: number;
  warning_count: number;
  danger_count: number;
  recent_alert_count: number;
}

// WebSocket 메시지 타입
export interface WSMessage {
  type: 'agent_update' | 'agent_online' | 'agent_offline' | 'summary_update' | 'connected' | 'heartbeat';
  data: Record<string, unknown>;
}

// ─── 통계 타입 ───
export interface BlockedSiteStatRow {
  agent_id: string;
  hostname: string | null;
  ip_address: string | null;
  lab_name: string | null;
  url_pattern: string;
  active_seconds: number;
  background_seconds: number;
  access_count: number;
  first_access_at: string | null;
  last_access_at: string | null;
}

export interface TimelineHourPoint {
  hour: number;
  count: number;
}

export interface TimelineDayPoint {
  date: string;
  count: number;
}

export interface BlockedAccessTimeline {
  hourly: TimelineHourPoint[];
  daily: TimelineDayPoint[];
  total: number;
}

export interface StatFilters {
  lab?: string;
  date_from?: string;
  date_to?: string;
  agent_id?: string;
}

export interface BlockedSiteDetailDay {
  date: string;
  active_seconds: number;
  background_seconds: number;
  access_count: number;
}

export interface BlockedSiteLogEntry {
  detected_at: string;
  level: string;
  url: string | null;
  message: string | null;
}

export interface BlockedSiteDetail {
  agent_id: string;
  hostname: string | null;
  ip_address: string | null;
  lab_name: string | null;
  url_pattern: string;
  active_seconds: number;
  background_seconds: number;
  access_count: number;
  first_access_at: string | null;
  last_access_at: string | null;
  daily: BlockedSiteDetailDay[];
  logs: BlockedSiteLogEntry[];
  log_total: number;
}

const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

async function apiFetch<T>(path: string): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    cache: 'no-store',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  // 로그인이 만료·거부됨 → 로그인 정보를 지우면 로그인 화면으로 이동
  if (res.status === 401) clearSession();
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

/** 로그인 실패 (화면에 보여 줄 메시지) */
export class LoginError extends Error {}

/** 관리자 로그인 → 성공하면 로그인 정보 반환 */
export async function login(username: string, password: string): Promise<Session> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      // 전에 로그인했던 브라우저면 신뢰 기기 토큰을 함께 보낸다 (잠금을 이 브라우저 기준으로 따로 셈)
      body: JSON.stringify({ username, password, device_token: getDeviceToken() }),
    });
  } catch {
    throw new LoginError('서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.');
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : null;
    // 아이디·비밀번호 오류(401), 잠금(429), 서버 설정 없음(503)은 서버가 보낸 안내를 그대로 보여 준다
    if (detail && [401, 429, 503].includes(res.status)) throw new LoginError(detail);
    if (res.status === 404 || res.status === 405) {
      throw new LoginError('서버가 로그인 기능을 지원하지 않습니다. 백엔드 배포 상태를 확인하세요.');
    }
    throw new LoginError(`서버 오류로 로그인하지 못했습니다 (${res.status})`);
  }
  if (typeof body.device_token === 'string') saveDeviceToken(body.device_token);
  // 만료 시각은 이 PC 시계 기준으로 계산 (PC 시계가 서버와 달라도 바로 만료로 보지 않도록)
  const expiresAt = typeof body.expires_in === 'number'
    ? Date.now() + body.expires_in * 1000
    : new Date(body.expires_at).getTime();
  return { token: body.token, username: body.username, expiresAt };
}

function statQuery(f: StatFilters): string {
  const p = new URLSearchParams();
  if (f.lab) p.set('lab', f.lab);
  if (f.date_from) p.set('date_from', f.date_from);
  if (f.date_to) p.set('date_to', f.date_to);
  if (f.agent_id) p.set('agent_id', f.agent_id);
  const q = p.toString();
  return q ? `?${q}` : '';
}

export const api = {
  getSummary: () => apiFetch<DashboardSummary>('/api/dashboard/summary'),
  getAgents:  () => apiFetch<AgentSummary[]>('/api/dashboard/agents'),
  getAgent:   (id: string) => apiFetch<AgentDetail>(`/api/dashboard/agents/${id}`),
  getAlerts:  (limit = 50) => apiFetch<AlertLog[]>(`/api/dashboard/alerts?limit=${limit}`),
  getLabs:    () => apiFetch<string[]>('/api/statistics/labs'),
  getBlockedSiteStats: (f: StatFilters = {}) =>
    apiFetch<BlockedSiteStatRow[]>(`/api/statistics/blocked-sites${statQuery(f)}`),
  getStatTimeline: (f: StatFilters = {}) =>
    apiFetch<BlockedAccessTimeline>(`/api/statistics/timeline${statQuery(f)}`),
  getBlockedSiteDetail: (agentId: string, site: string, f: StatFilters = {}) => {
    const p = new URLSearchParams({ agent_id: agentId, site });
    if (f.date_from) p.set('date_from', f.date_from);
    if (f.date_to) p.set('date_to', f.date_to);
    return apiFetch<BlockedSiteDetail>(`/api/statistics/blocked-detail?${p.toString()}`);
  },
};

export const WS_URL =
  (process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8000') + '/ws/dashboard';

// 실시간 연결에 로그인 토큰을 싣는 하위 프로토콜 (서버가 'bearer' 로 응답)
export const wsProtocols = (token: string) => ['bearer', token];
// 서버가 로그인이 필요하다며 실시간 연결을 닫을 때의 종료 코드
export const WS_CLOSE_UNAUTHORIZED = 4401;
