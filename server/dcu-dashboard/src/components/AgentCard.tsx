'use client';

import { AgentSummary } from '@/lib/api';
import StatusBadge from './StatusBadge';

interface Props {
  agent: AgentSummary;
  onClick: () => void;
}

function timeAgo(iso: string | null): string {
  if (!iso) return '알 수 없음';
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 5)  return '방금';
  if (diff < 60) return `${diff}초 전`;
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  return `${Math.floor(diff / 3600)}시간 전`;
}

export default function AgentCard({ agent, onClick }: Props) {
  const state = agent.is_online ? agent.current_state : 'unknown';

  return (
    <div
      className={`agent-card ${state} ${!agent.is_online ? 'agent-offline' : ''}`}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
    >
      <div className="agent-card-header">
        <div>
          <div className="agent-hostname">{agent.hostname}</div>
          <div className="agent-ip mono">
            {agent.ip_address ?? '—'}
            {agent.lab_name && <span style={{ marginLeft: 6, color: 'var(--text-muted)' }}>• {agent.lab_name}</span>}
          </div>
        </div>
        <StatusBadge state={state} size="sm" />
      </div>

      {agent.foreground_window && agent.is_online && (
        <div className="agent-foreground">
          <span className="fg-label">활성창</span>
          {agent.foreground_window}
        </div>
      )}

      <div className="agent-card-body">
        <div className="agent-stat">
          <span className="stat-label">프로세스</span>
          <span className="stat-value">{agent.process_count.toLocaleString()}개</span>
        </div>
        <div className="agent-stat">
          <span className="stat-label">크롬 탭</span>
          <span className="stat-value">{agent.chrome_tab_count}개</span>
        </div>
        <div className="agent-stat">
          <span className="stat-label">CPU</span>
          <span className="stat-value">{agent.cpu_percent.toFixed(1)}%</span>
        </div>
        <div className="agent-stat">
          <span className="stat-label">메모리</span>
          <span className="stat-value">{agent.memory_percent.toFixed(1)}%</span>
        </div>
      </div>

      {agent.alert_count > 0 && (
        <div style={{
          paddingLeft: 12,
          marginBottom: 8,
          fontSize: 12,
          color: state === 'danger' ? 'var(--danger)' : 'var(--warning)',
        }}>
          ⚠ 알림 {agent.alert_count}건
        </div>
      )}

      <div className="agent-last-seen">
        {agent.is_online ? `마지막 보고: ${timeAgo(agent.last_seen_at)}` : '오프라인'}
      </div>
    </div>
  );
}
