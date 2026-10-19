'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AgentDetail, AgentSummary, AlertInfo, AlertLog, DashboardSummary, WSMessage, WS_URL, api } from '@/lib/api';
import AgentCard from '@/components/AgentCard';
import AgentModal from '@/components/AgentModal';
import Header from '@/components/Header';

type SortOption = 'name-asc' | 'name-desc' | 'seat-asc' | 'seat-desc';

// 실시간 연결: 끊기면 이만큼 뒤 재연결, 이만큼 아무 메시지도 없으면 멈춘 연결로 보고 재연결 (서버 신호는 15초마다)
const WS_RECONNECT_DELAY_MS = 3_000;
const WS_SILENCE_LIMIT_MS = 45_000;

/** 숫자는 숫자로 비교하는 이름 정렬 (D2-535-2 < D2-535-10) */
const naturalCompare = (a: string, b: string) => a.localeCompare(b, 'ko', { numeric: true, sensitivity: 'base' });
const compareHostname = (a: AgentSummary, b: AgentSummary) =>
  naturalCompare(a.hostname, b.hostname) || a.agent_id.localeCompare(b.agent_id);

/** 호실 뒤 자리 번호 (D2-535-21 → 21, D2_520_04 → 4). 호실은 서버와 같이 이름의 두 번째 부분. 없으면 NaN */
function seatNumber(hostname: string): number {
  const seat = hostname.split(/[-_]/)[2]?.match(/^\d+/);
  return seat ? Number(seat[0]) : NaN;
}

/** PC 목록 정렬. 자리 번호 정렬에서 번호가 없는 PC(DESKTOP-… 등)는 방향과 관계없이 맨 뒤 */
function sortAgents(list: AgentSummary[], option: SortOption): AgentSummary[] {
  const [key, dir] = option.split('-');
  const sign = dir === 'asc' ? 1 : -1;
  return list.sort((a, b) => {
    if (key === 'seat') {
      const sa = seatNumber(a.hostname);
      const sb = seatNumber(b.hostname);
      if (Number.isNaN(sa) !== Number.isNaN(sb)) return Number.isNaN(sa) ? 1 : -1;
      if (!Number.isNaN(sa) && sa !== sb) return sign * (sa - sb);
    }
    return sign * compareHostname(a, b);
  });
}

const alertTime = (a: AlertInfo) => (a.detected_at ? new Date(a.detected_at).getTime() : 0);
const alertKey = (a: AlertInfo) => `${a.code}|${a.message}|${alertTime(a)}`;

/** 상세 모달의 최근 알림에 실시간 알림을 합친다 (상세 조회와 겹친 알림 제거, 최신순, 최대 50건) */
function mergeRecentAlerts(current: AlertInfo[], incoming: AlertInfo[]): AlertInfo[] {
  const seen = new Set(current.map(alertKey));
  const fresh = incoming.filter((a) => !seen.has(alertKey(a)));
  return [...fresh, ...current].sort((a, b) => alertTime(b) - alertTime(a)).slice(0, 50);
}

// PC 실시간 갱신 (agent_update 에는 이번 보고의 새 알림도 담김)
type AgentUpdate = AgentSummary & { alerts?: AlertInfo[] };

const isAgentMessage = (m: WSMessage) =>
  m.type === 'agent_update' || m.type === 'agent_online' || m.type === 'agent_offline';

/** PC 목록에 실시간 갱신 1건을 반영 (없던 PC면 추가) */
function applyAgentUpdate(list: AgentSummary[], updated: AgentSummary): AgentSummary[] {
  const idx = list.findIndex((a) => a.agent_id === updated.agent_id);
  if (idx < 0) return [...list, updated];
  const next = [...list];
  next[idx] = { ...next[idx], ...updated };
  return next;
}

/** 열린 상세에 같은 PC의 실시간 갱신 1건을 반영 (새 알림은 최근 알림 맨 위에 — 빨간 '화면 표시' 알림 포함) */
function applyDetailUpdate(detail: AgentDetail, updated: AgentUpdate): AgentDetail {
  const merged = { ...detail, ...updated } as AgentDetail;
  if (updated.alerts?.length) merged.recent_alerts = mergeRecentAlerts(detail.recent_alerts, updated.alerts);
  return merged;
}

/** 서버 조회가 진행되는 동안 받은 실시간 메시지 기록 */
interface LiveLog {
  active: number; // 진행 중인 조회 수 (0이면 기록하지 않음)
  messages: WSMessage[];
}

/**
 * 서버 조회 결과와 함께, 조회하는 동안 받은 실시간 메시지를 돌려준다.
 * 조회 결과는 요청 시점의 상태라서, 그사이 받은 메시지를 결과에 이어 다시 적용해야
 * 더 새로운 상태(오프라인 전환·새 알림 등)가 조회 결과에 덮이지 않는다.
 */
async function withLiveMessages<T>(log: LiveLog, request: Promise<T>): Promise<[T, WSMessage[]]> {
  const start = log.messages.length;
  log.active += 1;
  try {
    const result = await request;
    return [result, log.messages.slice(start)];
  } finally {
    log.active -= 1;
    if (log.active === 0) log.messages = [];
  }
}

export default function DashboardPage() {
  const [summary, setSummary]         = useState<DashboardSummary | null>(null);
  const [agents, setAgents]           = useState<AgentSummary[]>([]);
  const [alerts, setAlerts]           = useState<AlertLog[]>([]);
  const [selected, setSelected]       = useState<AgentDetail | null>(null);
  const [selectedLab, setSelectedLab] = useState<string>('전체');
  const [onlineOnly, setOnlineOnly]   = useState<boolean>(false);
  const [sortOption, setSortOption]   = useState<SortOption>('name-asc');
  const [wsStatus, setWsStatus]       = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const wsRef = useRef<WebSocket | null>(null);
  const liveLogRef = useRef<LiveLog>({ active: 0, messages: [] });

  /** PC 상세를 서버에서 불러와 모달에 반영 (open=false면 그 PC 모달이 열려 있을 때만) */
  const loadDetail = useCallback(async (id: string, open: boolean) => {
    const [detail, live] = await withLiveMessages(liveLogRef.current, api.getAgent(id));
    const latest = live
      .filter((m) => isAgentMessage(m) && m.data.agent_id === id)
      .reduce((d, m) => applyDetailUpdate(d, m.data as unknown as AgentUpdate), detail);
    setSelected((prev) => (open || (prev && prev.agent_id === id) ? latest : prev));
  }, []);

  // 열린 상세 모달의 PC (재연결 시 다시 불러오기용)
  const selectedIdRef = useRef<string | null>(null);
  useEffect(() => {
    selectedIdRef.current = selected?.agent_id ?? null;
  }, [selected]);

  /* ── WebSocket 연결 ── */
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let disposed = false;
    let hasConnected = false;
    let lastMessageAt = Date.now();
    let loadSeq = 0;
    let appliedSeq = 0;

    /** 요약·PC 목록·경고 이력을 서버에서 불러온다. 성공하면 true */
    async function loadAll(): Promise<boolean> {
      const seq = ++loadSeq;
      try {
        const [[s, a, al], live] = await withLiveMessages(
          liveLogRef.current,
          Promise.all([api.getSummary(), api.getAgents(), api.getAlerts(50)]),
        );
        if (disposed || seq < appliedSeq) return true; // 나중에 시작한 조회가 이미 반영됨
        appliedSeq = seq;
        const lastSummary = live.filter((m) => m.type === 'summary_update').pop();
        setSummary(lastSummary ? (lastSummary.data as unknown as DashboardSummary) : s);
        setAgents(live.filter(isAgentMessage).reduce((list, m) => applyAgentUpdate(list, m.data as unknown as AgentSummary), a));
        setAlerts(al);
        return true;
      } catch (e) {
        console.error('데이터 로드 실패:', e);
        return false;
      }
    }

    /** 열린 상세 모달을 서버 기준으로 다시 불러온다 */
    function refreshSelected() {
      const id = selectedIdRef.current;
      if (id) loadDetail(id, false).catch(() => {});
    }

    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      setWsStatus('connecting');

      ws.onopen = () => {
        setWsStatus('connected');
        lastMessageAt = Date.now();
        // 재연결이면 끊긴 동안 놓친 변경(목록·요약·이력·열린 상세)을 서버에서 다시 불러온다.
        // 첫 연결이면 처음 불러오기가 실패했을 때만 다시 불러온다 (서버 재시작 중에 화면을 연 경우 등)
        if (hasConnected) {
          loadAll();
          refreshSelected();
        } else {
          initialLoad.then((ok) => {
            if (!ok && !disposed) loadAll();
          });
        }
        hasConnected = true;
      };
      ws.onclose = () => {
        setWsStatus('disconnected');
        if (!disposed) reconnectTimer = setTimeout(connect, WS_RECONNECT_DELAY_MS);
      };
      ws.onerror = () => ws.close();

      ws.onmessage = (e) => {
        lastMessageAt = Date.now();
        try {
          const msg: WSMessage = JSON.parse(e.data);

          // 연결 확인 신호·연결 확인 메시지는 수신 시각만 기록
          if (msg.type === 'heartbeat' || msg.type === 'connected') return;

          // 서버에서 불러오는 중이면 기록해 두었다가 조회 결과에 이어 다시 적용
          const liveLog = liveLogRef.current;
          if (liveLog.active > 0) liveLog.messages.push(msg);

          // ── 요약 카드 즉시 갱신 ──
          if (msg.type === 'summary_update') {
            setSummary(msg.data as unknown as DashboardSummary);
            return;
          }

          // ── 에이전트 카드 실시간 갱신 (모달이 열린 상태라면 모달도) ──
          if (isAgentMessage(msg)) {
            const updated = msg.data as unknown as AgentUpdate;
            setAgents((prev) => applyAgentUpdate(prev, updated));
            setSelected((prev) => (prev && prev.agent_id === updated.agent_id ? applyDetailUpdate(prev, updated) : prev));
          }
        } catch { /* ignore */ }
      };
    }

    const initialLoad = loadAll();
    connect();

    // 요약 통계는 30초마다 REST로 갱신
    const pollTimer = setInterval(() => {
      api.getSummary().then(setSummary).catch(() => {});
    }, 30_000);

    // 무응답 감시: 서버는 15초마다 연결 확인 신호를 보낸다. 오래 아무 메시지도 없으면 연결이 멈춘 것으로 보고,
    // 닫히기를 기다리지 않고 그 연결을 버린 뒤 바로 새로 연결한다 (새 연결에서 전체를 다시 불러옴)
    const watchdogTimer = setInterval(() => {
      const stale = wsRef.current;
      if (!stale || stale.readyState !== WebSocket.OPEN || Date.now() - lastMessageAt < WS_SILENCE_LIMIT_MS) return;
      stale.onopen = stale.onclose = stale.onerror = stale.onmessage = null;
      stale.close();
      connect();
    }, 5_000);

    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      clearInterval(pollTimer);
      clearInterval(watchdogTimer);
      wsRef.current?.close();
    };
  }, [loadDetail]);

  /* ── 에이전트 카드 클릭 → 상세 모달 ── */
  const handleCardClick = useCallback(async (agentId: string) => {
    try {
      await loadDetail(agentId, true);
    } catch (e) {
      console.error('에이전트 상세 로드 실패:', e);
    }
  }, [loadDetail]);

  /* ── 렌더링 ── */
  /* ── 데이터 필터링 (강의실 기준) ── */
  const labs = ['전체', ...Array.from(new Set(agents.map((a) => a.lab_name || '기타'))).sort(naturalCompare)];
  // PC 이름·자리 번호순 정렬 (경고·주의 영역과 강의실별 목록 모두 같은 순서)
  const filteredAgents = sortAgents(
    agents
      .filter((a) => selectedLab === '전체' || (a.lab_name || '기타') === selectedLab)
      .filter((a) => !onlineOnly || a.is_online),
    sortOption,
  );

  const filteredAgentIds = new Set(filteredAgents.map((a) => a.agent_id));
  const filteredAlerts = selectedLab === '전체'
    ? alerts
    : alerts.filter((al) => filteredAgentIds.has(al.agent_id));

  const dangerAgents  = filteredAgents.filter((a) => a.is_online && a.current_state === 'danger');
  const warningAgents = filteredAgents.filter((a) => a.is_online && a.current_state === 'warning');

  return (
    <div className="dashboard-layout">
      {/* ── 헤더 ── */}
      <Header>
        <div className={`ws-status ${wsStatus}`}>
          <span className="dot" />
          {wsStatus === 'connected' ? '실시간 연결됨' : wsStatus === 'connecting' ? '연결 중…' : '연결 끊김'}
        </div>
      </Header>

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

        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            cursor: 'pointer',
            color: onlineOnly ? 'var(--safe)' : 'var(--text-secondary)',
            fontSize: '14px',
            fontWeight: 500,
            userSelect: 'none',
          }}
        >
          <input
            type="checkbox"
            checked={onlineOnly}
            onChange={(e) => setOnlineOnly(e.target.checked)}
            style={{ width: 16, height: 16, cursor: 'pointer', accentColor: 'var(--safe)' }}
          />
          🟢 온라인만 보기
        </label>

        <select
          value={sortOption}
          onChange={(e) => setSortOption(e.target.value as SortOption)}
          aria-label="PC 정렬 순서"
          style={{
            padding: '8px 12px',
            borderRadius: '6px',
            border: '1px solid var(--border)',
            backgroundColor: 'var(--bg-elevated)',
            color: 'var(--text-primary)',
            fontSize: '14px',
            cursor: 'pointer',
            fontWeight: 500,
            outline: 'none',
          }}
        >
          <option value="name-asc">PC 이름 ▲ 오름차순</option>
          <option value="name-desc">PC 이름 ▼ 내림차순</option>
          <option value="seat-asc">자리 번호 ▲ 오름차순</option>
          <option value="seat-desc">자리 번호 ▼ 내림차순</option>
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
        ).sort(([a], [b]) => naturalCompare(a, b)).map(([labName, labAgents]) => {
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
