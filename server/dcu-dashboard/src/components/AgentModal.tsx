'use client';

import { AgentDetail } from '@/lib/api';
import StatusBadge from './StatusBadge';

interface Props {
  agent: AgentDetail;
  onClose: () => void;
}

export default function AgentModal({ agent, onClose }: Props) {
  const state = agent.is_online ? agent.current_state : 'unknown';

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {agent.hostname}
            <StatusBadge state={state} size="sm" />
          </div>
          <button className="modal-close" onClick={onClose} aria-label="닫기">×</button>
        </div>

        <div className="modal-body">
          {/* 기본 정보 */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
            {[
              ['IP 주소', agent.ip_address ?? '—'],
              ['실습실', agent.lab_name ?? '—'],
              ['OS', agent.ip_address ?? '—'],
              ['에이전트 버전', '—'],
              ['CPU', `${agent.cpu_percent.toFixed(1)}%`],
              ['메모리', `${agent.memory_percent.toFixed(1)}%`],
            ].map(([label, value]) => (
              <div key={label}>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }} className="mono">{value}</div>
              </div>
            ))}
          </div>

          {/* 활성 탭 */}
          {agent.chrome_tabs.length > 0 && (
            <>
              <div className="modal-section-title">크롬 탭 ({agent.chrome_tabs.length}개)</div>
              <div className="tab-list">
                {agent.chrome_tabs.map((tab) => (
                  <div key={tab.tab_id} className={`tab-item ${tab.is_active ? 'active' : ''}`}>
                    <div className="tab-title">{tab.title || '(제목 없음)'}</div>
                    <div className="tab-url">{tab.url}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* 최근 알림 */}
          {agent.recent_alerts.length > 0 && (
            <>
              <div className="modal-section-title" style={{ color: 'var(--warning)' }}>
                최근 알림 ({agent.recent_alerts.length}건)
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {agent.recent_alerts.slice(0, 15).map((a, i) => (
                  <div key={i} style={{
                    background: 'var(--bg-elevated)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '8px 12px',
                    borderLeft: `2px solid ${a.level === 'warning' ? 'var(--warning)' : 'var(--danger)'}`,
                    fontSize: 12,
                  }}>
                    <div style={{ color: 'var(--text-primary)', marginBottom: 2 }}>{a.message}</div>
                    {a.detected_at && (
                      <div style={{ color: 'var(--text-muted)' }}>
                        {new Date(a.detected_at).toLocaleString('ko-KR')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* 프로세스 목록 */}
          {agent.processes.length > 0 && (
            <>
              <div className="modal-section-title">실행 중인 프로세스 ({agent.processes.length}개)</div>
              <div className="process-list">
                {agent.processes.map((p) => (
                  <div key={p.pid} className={`process-item ${p.is_foreground ? 'foreground' : ''}`}>
                    <span className="process-name">{p.name}</span>
                    <span className="process-mem">{p.memory_mb.toFixed(0)} MB</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
