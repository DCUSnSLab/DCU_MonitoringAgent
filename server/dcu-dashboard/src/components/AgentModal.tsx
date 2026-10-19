'use client';

import { ActiveDetection, AgentDetail } from '@/lib/api';
import { DangerDuration, OnScreenElapsed } from './OnScreenTime';
import RecentAlerts from './RecentAlerts';
import StatusBadge from './StatusBadge';

interface Props {
  agent: AgentDetail;
  onClose: () => void;
}

// 현재 탐지 항목을 알림 코드별로 묶어 보여줄 그룹
const DETECTION_GROUPS: { label: string; codes: string[] }[] = [
  { label: '차단 프로그램', codes: ['BLOCKED_PROCESS', 'UNAUTHORIZED_PROCESS'] },
  { label: '차단 사이트', codes: ['BLOCKED_URL'] },
  { label: '메신저', codes: ['MESSENGER_DETECTED'] },
];

const SOURCE_LABEL: Record<string, string> = { process: '프로그램', url: 'URL', title: '창 제목' };

/** 그룹별로 나누고, 화면에 띄워진 항목을 먼저 정렬한다. 정의되지 않은 코드는 '기타'. */
function groupDetections(items: ActiveDetection[]) {
  const known = new Set(DETECTION_GROUPS.flatMap((g) => g.codes));
  const sorted = [...items].sort(
    (a, b) => Number(b.is_foreground) - Number(a.is_foreground) || a.target.localeCompare(b.target),
  );
  return [
    ...DETECTION_GROUPS.map((g) => ({ label: g.label, items: sorted.filter((d) => g.codes.includes(d.code)) })),
    { label: '기타', items: sorted.filter((d) => !known.has(d.code)) },
  ].filter((g) => g.items.length > 0);
}

export default function AgentModal({ agent, onClose }: Props) {
  const state = agent.is_online ? agent.current_state : 'unknown';
  const detections = agent.is_online ? agent.active_detections ?? [] : [];

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
          {/* DANGER 이유 */}
          {state === 'danger' && agent.danger_reason && (
            <div style={{
              background: 'var(--bg-elevated)',
              borderRadius: 'var(--radius-sm)',
              borderLeft: '2px solid var(--danger)',
              padding: '8px 12px',
              marginBottom: 16,
              fontSize: 13,
              color: 'var(--danger)',
              fontWeight: 600,
              overflowWrap: 'anywhere',
            }}>
              화면 표시: {agent.danger_reason}
              {agent.danger_since && (
                <div style={{ fontSize: 11, fontWeight: 500, marginTop: 2, opacity: 0.85 }}>
                  <DangerDuration agent={agent} />
                </div>
              )}
            </div>
          )}

          {/* 기본 정보 */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
            {[
              ['IP 주소', agent.ip_address ?? '—'],
              ['실습실', agent.lab_name ?? '—'],
              ['OS', agent.os_version ?? '—'],
              ['에이전트 버전', agent.agent_version ?? '—'],
              ['CPU', `${agent.cpu_percent.toFixed(1)}%`],
              ['메모리', `${agent.memory_percent.toFixed(1)}%`],
            ].map(([label, value]) => (
              <div key={label}>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }} className="mono">{value}</div>
              </div>
            ))}
          </div>

          {/* 현재 탐지 중 (지금 켜져 있는 차단 프로그램·사이트·메신저) */}
          <div className="modal-section-title">현재 탐지 중 ({detections.length}건)</div>
          {detections.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 20 }}>
              {agent.is_online ? '현재 탐지된 차단 프로그램·사이트가 없습니다.' : '오프라인 — 현재 탐지 정보가 없습니다.'}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 20 }}>
              {groupDetections(detections).map((group) => (
                <div key={group.label}>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>
                    {group.label} ({group.items.length})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {group.items.map((d) => (
                      <div key={`${d.code}:${d.target}`} style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        background: 'var(--bg-elevated)',
                        borderRadius: 'var(--radius-sm)',
                        padding: '6px 10px',
                        borderLeft: `2px solid ${d.is_foreground ? 'var(--danger)' : 'var(--warning)'}`,
                        fontSize: 12,
                      }}>
                        <span title={d.target} style={{
                          flex: 1,
                          minWidth: 0,
                          color: 'var(--text-primary)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          {d.target}
                        </span>
                        <span style={{ fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>
                          {SOURCE_LABEL[d.source] ?? d.source}
                        </span>
                        {d.is_foreground && (
                          <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--danger)', flexShrink: 0 }}>
                            화면 표시 중{d.on_screen_since && <> · <OnScreenElapsed since={d.on_screen_since} /></>}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

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

          {/* 최근 알림 (프로그램·종류별 필터) */}
          {agent.recent_alerts.length > 0 && <RecentAlerts alerts={agent.recent_alerts} />}

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
