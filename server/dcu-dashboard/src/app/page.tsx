'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AgentDetail, AgentSummary, AlertLog, DashboardSummary, WSMessage, WS_URL, api } from '@/lib/api';
import AgentCard from '@/components/AgentCard';
import AgentModal from '@/components/AgentModal';

export default function DashboardPage() {
  const [summary, setSummary]         = useState<DashboardSummary | null>(null);
  const [agents, setAgents]           = useState<AgentSummary[]>([]);
  const [alerts, setAlerts]           = useState<AlertLog[]>([]);
  const [selected, setSelected]       = useState<AgentDetail | null>(null);
  const [selectedLab, setSelectedLab] = useState<string>('전체');
  const [wsStatus, setWsStatus]       = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const wsRef = useRef<WebSocket | null>(null);

  /* ── 초기 데이터 로드 ── */
  const loadInitial = useCallback(async () => {
    try {
      const [s, a, al] = await Promise.all([api.getSummary(), api.getAgents(), api.getAlerts(50)]);
      setSummary(s);
      setAgents(a);
      setAlerts(al);
    } catch (e) {
      console.error('초기 데이터 로드 실패:', e);
    }
  }, []);

  /* ── WebSocket 연결 ── */
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      setWsStatus('connecting');

      ws.onopen  = () => setWsStatus('connected');
      ws.onclose = () => {
        setWsStatus('disconnected');
        reconnectTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();

      ws.onmessage = (e) => {
        try {
          const msg: WSMessage = JSON.parse(e.data);

          // ── 요약 카드 즉시 갱신 ──
          if (msg.type === 'summary_update') {
            setSummary(msg.data as unknown as DashboardSummary);
            return;
          }

          // ── 에이전트 카드 실시간 갱신 ──
          if (msg.type === 'agent_update' || msg.type === 'agent_online' || msg.type === 'agent_offline') {
            const updated = msg.data as unknown as AgentSummary;
            setAgents((prev) => {
              const idx = prev.findIndex((a) => a.agent_id === updated.agent_id);
              if (idx >= 0) {
                const next = [...prev];
                next[idx] = { ...next[idx], ...updated };
                return next;
              }
              return [...prev, updated];
            });
            // 모달이 열린 상태라면 실시간 업데이트
            setSelected((prev) => {
              if (prev && prev.agent_id === updated.agent_id) {
                return { ...prev, ...updated } as AgentDetail;
              }
              return prev;
            });
          }
        } catch { /* ignore */ }
      };
    }

    loadInitial();
    connect();

    // 요약 통계는 30초마다 REST로 갱신
    const pollTimer = setInterval(() => {
      api.getSummary().then(setSummary).catch(() => {});
    }, 30_000);

    return () => {
      clearTimeout(reconnectTimer);
      clearInterval(pollTimer);
      wsRef.current?.close();
    };
  }, [loadInitial]);

  /* ── 에이전트 카드 클릭 → 상세 모달 ── */
  const handleCardClick = useCallback(async (agentId: string) => {
    try {
      const detail = await api.getAgent(agentId);
      setSelected(detail);
    } catch (e) {
      console.error('에이전트 상세 로드 실패:', e);
    }
  }, []);

  /* ── 렌더링 ── */
  /* ── 데이터 필터링 (강의실 기준) ── */
  const labs = ['전체', ...Array.from(new Set(agents.map((a) => a.lab_name || '기타'))).sort()];
  const filteredAgents = selectedLab === '전체'
    ? agents
    : agents.filter((a) => (a.lab_name || '기타') === selectedLab);

  const filteredAgentIds = new Set(filteredAgents.map((a) => a.agent_id));
  const filteredAlerts = selectedLab === '전체'
    ? alerts
    : alerts.filter((al) => filteredAgentIds.has(al.agent_id));

  const dangerAgents  = filteredAgents.filter((a) => a.is_online && a.current_state === 'danger');
  const warningAgents = filteredAgents.filter((a) => a.is_online && a.current_state === 'warning');

  return (
    <div className="dashboard-layout">
      {/* ── 헤더 ── */}
      <header className="header">
        <div className="header-logo">
          <span className="logo-dot" />
          DCU 실습실 모니터링
        </div>
        <div className={`ws-status ${wsStatus}`}>
          <span className="dot" />
          {wsStatus === 'connected' ? '실시간 연결됨' : wsStatus === 'connecting' ? '연결 중…' : '연결 끊김'}
        </div>
      </header>

      {/* ── 필터 옵션 ── */}
      <div style={{ padding: '0 2rem', marginTop: '1.5rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '1rem', color: 'var(--text-secondary)' }}>강의실 필터:</h3>
        <select 
          value={selectedLab} 
          onChange={(e) => setSelectedLab(e.target.value)}
          style={{
            padding: '8px 16px',
            borderRadius: '6px',
            border: '1px solid var(--border)',
            backgroundColor: 'var(--bg-elevated)',
            color: 'var(--text-primary)',
            fontSize: '15px',
            cursor: 'pointer',
            minWidth: '120px',
            fontWeight: 500,
            outline: 'none'
          }}
        >
          {labs.map(lab => (
            <option key={lab} value={lab}>{lab === '전체' ? '🌐 전체 보기' : `🏢 ${lab}호`}</option>
          ))}
        </select>
      </div>

      {/* ── 본문 ── */}
      <main className="main-content">

        {/* 요약 통계 카드 */}
        {summary && (
          <div className="summary-grid">
            <div className="summary-card">
              <span className="label">전체 PC</span>
              <span className="value">{summary.total_agents}</span>
            </div>
            <div className="summary-card">
              <span className="label">온라인</span>
              <span className="value" style={{ color: 'var(--accent-light)' }}>{summary.online_agents}</span>
            </div>
            <div className="summary-card safe">
              <span className="label">🟢 정상</span>
              <span className="value">{summary.safe_count}</span>
            </div>
            <div className="summary-card warning">
              <span className="label">🟡 주의</span>
              <span className="value">{summary.warning_count}</span>
            </div>
            <div className="summary-card danger">
              <span className="label">🔴 경고</span>
              <span className="value">{summary.danger_count}</span>
            </div>
            <div className="summary-card">
              <span className="label">최근 1h 알림</span>
              <span className="value" style={{ color: summary.recent_alert_count > 0 ? 'var(--warning)' : 'var(--text-primary)' }}>
                {summary.recent_alert_count}
              </span>
            </div>
          </div>
        )}

        {/* 경고 중인 PC */}
        {dangerAgents.length > 0 && (
          <>
            <p className="section-title" style={{ color: 'var(--danger)' }}>🔴 경고 — 즉시 확인 필요</p>
            <div className="agent-grid">
              {dangerAgents.map((a) => (
                <AgentCard key={a.agent_id} agent={a} onClick={() => handleCardClick(a.agent_id)} />
              ))}
            </div>
          </>
        )}

        {/* 주의 중인 PC */}
        {warningAgents.length > 0 && (
          <>
            <p className="section-title" style={{ color: 'var(--warning)' }}>🟡 주의</p>
            <div className="agent-grid">
              {warningAgents.map((a) => (
                <AgentCard key={a.agent_id} agent={a} onClick={() => handleCardClick(a.agent_id)} />
              ))}
            </div>
          </>
        )}

        {/* 강의실별 PC 목록 (그룹핑) */}
        {Object.entries(
          filteredAgents.reduce((acc, agent) => {
            const lab = agent.lab_name || '기타';
            if (!acc[lab]) acc[lab] = [];
            acc[lab].push(agent);
            return acc;
          }, {} as Record<string, AgentSummary[]>)
        ).sort().map(([labName, labAgents]) => {
          // 해당 강의실 통계
          const onlineCount = labAgents.filter(a => a.is_online).length;
          return (
            <div key={labName} style={{ marginBottom: '2rem' }}>
              <p className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>🏢 {labName}호 <span style={{ fontSize: '12px', color: 'var(--text-muted)', marginLeft: '8px' }}>({onlineCount} / {labAgents.length} 온라인)</span></span>
              </p>
              <div className="agent-grid">
                {labAgents.map((a) => (
                  <AgentCard key={a.agent_id} agent={a} onClick={() => handleCardClick(a.agent_id)} />
                ))}
              </div>
            </div>
          );
        })}

        {filteredAgents.length === 0 && (
          <div style={{ textAlign: 'center', padding: '80px 0', color: 'var(--text-muted)' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>📡</div>
            <div style={{ fontSize: 16, marginBottom: 8 }}>{selectedLab === '전체' ? '연결된 에이전트가 없습니다' : '해당 강의실에 연결된 PC가 없습니다'}</div>
            <div style={{ fontSize: 13 }}>실습실 PC에서 에이전트를 실행하면 자동으로 표시됩니다.</div>
          </div>
        )}

        {/* 최근 경고 이력 */}
        {filteredAlerts.length > 0 && (
          <div className="alerts-section">
            <p className="section-title">최근 경고 이력</p>
            <table className="alert-table">
              <thead>
                <tr>
                  <th>시각</th>
                  <th>PC</th>
                  <th>유형</th>
                  <th>내용</th>
                </tr>
              </thead>
              <tbody>
                {filteredAlerts.slice(0, 30).map((a) => (
                  <tr key={a.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => handleCardClick(a.agent_id)}
                  >
                    <td className="mono" style={{ whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>
                      {new Date(a.detected_at).toLocaleString('ko-KR')}
                    </td>
                    <td style={{ fontWeight: 500 }}>{a.hostname ?? a.agent_id}</td>
                    <td>
                      <span style={{
                        color: a.level === 'warning' ? 'var(--warning)' : 'var(--danger)',
                        fontWeight: 600, fontSize: 11,
                      }}>
                        {a.code}
                      </span>
                    </td>
                    <td style={{ maxWidth: 400 }} className="truncate">{a.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

      </main>

      {/* 상세 모달 */}
      {selected && (
        <AgentModal agent={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
