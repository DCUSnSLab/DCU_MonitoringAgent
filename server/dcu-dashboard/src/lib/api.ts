// API 타입 정의 및 클라이언트 함수

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

export interface AgentDetail extends AgentSummary {
  processes: ProcessInfo[];
  chrome_tabs: ChromeTab[];
  recent_alerts: AlertInfo[];
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
  type: 'agent_update' | 'agent_online' | 'agent_offline' | 'summary_update' | 'connected';
  data: Record<string, unknown>;
}

const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const api = {
  getSummary: () => apiFetch<DashboardSummary>('/api/dashboard/summary'),
  getAgents:  () => apiFetch<AgentSummary[]>('/api/dashboard/agents'),
  getAgent:   (id: string) => apiFetch<AgentDetail>(`/api/dashboard/agents/${id}`),
  getAlerts:  (limit = 50) => apiFetch<AlertLog[]>(`/api/dashboard/alerts?limit=${limit}`),
};

export const WS_URL =
  (process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8000') + '/ws/dashboard';
