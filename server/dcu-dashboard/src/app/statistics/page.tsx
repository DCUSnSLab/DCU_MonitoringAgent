'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import Header from '@/components/Header';
import {
  api,
  BlockedAccessTimeline,
  BlockedSiteStatRow,
  StatFilters,
} from '@/lib/api';

/* ── 초 → 사람이 읽는 시간 ── */
function fmtDuration(sec: number): string {
  if (!sec || sec <= 0) return '0초';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const parts: string[] = [];
  if (h) parts.push(`${h}시간`);
  if (m) parts.push(`${m}분`);
  if (s && !h) parts.push(`${s}초`);
  return parts.join(' ') || '0초';
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('ko-KR');
}

/* ── 오늘 기준 기본 날짜(YYYY-MM-DD) ── */
function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

const CHART_GRID = 'rgba(255,255,255,0.06)';
const CHART_AXIS = '#8899bb';

export default function StatisticsPage() {
  const [labs, setLabs] = useState<string[]>([]);
  const [rows, setRows] = useState<BlockedSiteStatRow[]>([]);
  const [timeline, setTimeline] = useState<BlockedAccessTimeline | null>(null);
  const [loading, setLoading] = useState(false);

  /* 필터 상태 */
  const [lab, setLab] = useState<string>('');
  const today = isoDate(new Date());
  const monthAgo = isoDate(new Date(Date.now() - 29 * 86400_000));
  const [dateFrom, setDateFrom] = useState<string>(monthAgo);
  const [dateTo, setDateTo] = useState<string>(today);
  const [agentQuery, setAgentQuery] = useState<string>('');

  /* 강의실 목록 로드 (1회) */
  useEffect(() => {
    api.getLabs().then(setLabs).catch(() => {});
  }, []);

  /* 통계 로드 */
  const load = useCallback(async () => {
    setLoading(true);
    const filters: StatFilters = {
      lab: lab || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    };
    try {
      const [r, t] = await Promise.all([
        api.getBlockedSiteStats(filters),
        api.getStatTimeline(filters),
      ]);
      setRows(r);
      setTimeline(t);
    } catch (e) {
      console.error('통계 로드 실패:', e);
    } finally {
      setLoading(false);
    }
  }, [lab, dateFrom, dateTo]);

  useEffect(() => {
    load();
  }, [load]);

  /* 에이전트 검색은 클라이언트에서 필터링 (hostname/ip/agent_id) */
  const filteredRows = useMemo(() => {
    const q = agentQuery.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        (r.hostname ?? '').toLowerCase().includes(q) ||
        (r.ip_address ?? '').toLowerCase().includes(q) ||
        r.agent_id.toLowerCase().includes(q),
    );
  }, [rows, agentQuery]);

  /* ── 차트 데이터 가공 ── */
  // 사이트별 활성 vs 백그라운드 시간 (분 단위 집계)
  const siteChart = useMemo(() => {
    const acc: Record<string, { active: number; background: number }> = {};
    for (const r of filteredRows) {
      if (!acc[r.url_pattern]) acc[r.url_pattern] = { active: 0, background: 0 };
      acc[r.url_pattern].active += r.active_seconds;
      acc[r.url_pattern].background += r.background_seconds;
    }
    return Object.entries(acc)
      .map(([url, v]) => ({
        url,
        active: +(v.active / 60).toFixed(1),
        background: +(v.background / 60).toFixed(1),
      }))
      .sort((a, b) => b.active + b.background - (a.active + a.background));
  }, [filteredRows]);

  const hourly = timeline?.hourly ?? [];
  const daily = timeline?.daily ?? [];

  /* ── 요약 합계 ── */
  const totals = useMemo(() => {
    let active = 0;
    let background = 0;
    let access = 0;
    const agents = new Set<string>();
    for (const r of filteredRows) {
      active += r.active_seconds;
      background += r.background_seconds;
      access += r.access_count;
      agents.add(r.agent_id);
    }
    return { active, background, access, agentCount: agents.size };
  }, [filteredRows]);

  return (
    <div className="dashboard-layout">
      <Header>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          차단 사이트 접근 통계
        </span>
      </Header>

      <main className="main-content">
        {/* ── 필터 바 ── */}
        <div className="stat-filter-bar">
          <div className="stat-filter-field">
            <label>강의실</label>
            <select value={lab} onChange={(e) => setLab(e.target.value)}>
              <option value="">전체</option>
              {labs.map((l) => (
                <option key={l} value={l}>
                  {l}호
                </option>
              ))}
            </select>
          </div>
          <div className="stat-filter-field">
            <label>시작일</label>
            <input
              type="date"
              value={dateFrom}
              max={dateTo}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </div>
          <div className="stat-filter-field">
            <label>종료일</label>
            <input
              type="date"
              value={dateTo}
              min={dateFrom}
              max={today}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </div>
          <div className="stat-filter-field" style={{ flex: 1, minWidth: 180 }}>
            <label>에이전트 검색 (이름 · IP)</label>
            <input
              type="text"
              placeholder="호스트명 또는 IP"
              value={agentQuery}
              onChange={(e) => setAgentQuery(e.target.value)}
            />
          </div>
          <button className="stat-refresh-btn" onClick={load} disabled={loading}>
            {loading ? '불러오는 중…' : '새로고침'}
          </button>
        </div>

        {/* ── 요약 카드 ── */}
        <div className="summary-grid">
          <div className="summary-card">
            <span className="label">접근 에이전트</span>
            <span className="value">{totals.agentCount}</span>
          </div>
          <div className="summary-card">
            <span className="label">총 방문 횟수</span>
            <span className="value" style={{ color: 'var(--accent-light)' }}>
              {totals.access}
            </span>
          </div>
          <div className="summary-card danger">
            <span className="label">🔴 활성(직접 본) 시간</span>
            <span className="value" style={{ fontSize: 20 }}>
              {fmtDuration(totals.active)}
            </span>
          </div>
          <div className="summary-card warning">
            <span className="label">🟡 백그라운드 시간</span>
            <span className="value" style={{ fontSize: 20 }}>
              {fmtDuration(totals.background)}
            </span>
          </div>
        </div>

        {/* ── 차트 그리드 ── */}
        <div className="stat-chart-grid">
          {/* 사이트별 활성 vs 백그라운드 */}
          <div className="stat-chart-card">
            <p className="section-title">사이트별 활성 / 백그라운드 시간 (분)</p>
            {siteChart.length === 0 ? (
              <div className="stat-empty">데이터 없음</div>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={siteChart} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                  <CartesianGrid stroke={CHART_GRID} vertical={false} />
                  <XAxis dataKey="url" tick={{ fill: CHART_AXIS, fontSize: 11 }} />
                  <YAxis tick={{ fill: CHART_AXIS, fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{
                      background: '#1a2235',
                      border: '1px solid rgba(255,255,255,0.15)',
                      borderRadius: 8,
                      color: '#f0f4ff',
                    }}
                    formatter={(v) => [`${v}분`, '']}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, color: CHART_AXIS }} />
                  <Bar dataKey="active" name="활성" fill="#ef4444" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="background" name="백그라운드" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* 시간대별 접근 분포 */}
          <div className="stat-chart-card">
            <p className="section-title">시간대(0~23시)별 접근 횟수</p>
            {hourly.length === 0 ? (
              <div className="stat-empty">데이터 없음</div>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={hourly} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                  <CartesianGrid stroke={CHART_GRID} vertical={false} />
                  <XAxis dataKey="hour" tick={{ fill: CHART_AXIS, fontSize: 11 }} />
                  <YAxis tick={{ fill: CHART_AXIS, fontSize: 11 }} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      background: '#1a2235',
                      border: '1px solid rgba(255,255,255,0.15)',
                      borderRadius: 8,
                      color: '#f0f4ff',
                    }}
                    formatter={(v) => [`${v}회`, '접근']}
                    labelFormatter={(h) => `${h}시`}
                  />
                  <Bar dataKey="count" name="접근" radius={[4, 4, 0, 0]}>
                    {hourly.map((h) => (
                      <Cell key={h.hour} fill={h.count > 0 ? '#3b82f6' : '#243450'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* 일자별 추세 */}
          <div className="stat-chart-card" style={{ gridColumn: '1 / -1' }}>
            <p className="section-title">일자별 접근 추세</p>
            {daily.length === 0 ? (
              <div className="stat-empty">데이터 없음</div>
            ) : (
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={daily} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                  <CartesianGrid stroke={CHART_GRID} vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: CHART_AXIS, fontSize: 11 }} />
                  <YAxis tick={{ fill: CHART_AXIS, fontSize: 11 }} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      background: '#1a2235',
                      border: '1px solid rgba(255,255,255,0.15)',
                      borderRadius: 8,
                      color: '#f0f4ff',
                    }}
                    formatter={(v) => [`${v}회`, '접근']}
                  />
                  <Line
                    type="monotone"
                    dataKey="count"
                    name="접근"
                    stroke="#60a5fa"
                    strokeWidth={2}
                    dot={{ r: 3, fill: '#60a5fa' }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* ── 상세 테이블 ── */}
        <p className="section-title" style={{ marginTop: 8 }}>
          에이전트 × 차단 사이트 상세 ({filteredRows.length}건)
        </p>
        <table className="alert-table">
          <thead>
            <tr>
              <th>컴퓨터 이름</th>
              <th>IP 주소</th>
              <th>강의실</th>
              <th>차단 사이트</th>
              <th>활성 시간</th>
              <th>백그라운드 시간</th>
              <th>방문</th>
              <th>최초 접속</th>
              <th>최근 접속</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 ? (
              <tr>
                <td colSpan={9} style={{ textAlign: 'center', padding: '32px 0', color: 'var(--text-muted)' }}>
                  {loading ? '불러오는 중…' : '해당 조건의 차단 사이트 접근 기록이 없습니다.'}
                </td>
              </tr>
            ) : (
              filteredRows.map((r) => (
                <tr key={`${r.agent_id}-${r.url_pattern}`}>
                  <td style={{ fontWeight: 500, color: 'var(--text-primary)' }}>
                    {r.hostname ?? r.agent_id}
                  </td>
                  <td className="mono" style={{ color: 'var(--text-muted)' }}>
                    {r.ip_address ?? '-'}
                  </td>
                  <td>{r.lab_name ? `${r.lab_name}호` : '-'}</td>
                  <td className="mono" style={{ color: 'var(--danger)' }}>{r.url_pattern}</td>
                  <td style={{ color: 'var(--danger)', fontWeight: 500 }}>
                    {fmtDuration(r.active_seconds)}
                  </td>
                  <td style={{ color: 'var(--warning)' }}>{fmtDuration(r.background_seconds)}</td>
                  <td style={{ textAlign: 'center' }}>{r.access_count}</td>
                  <td className="mono" style={{ whiteSpace: 'nowrap', color: 'var(--text-muted)', fontSize: 11 }}>
                    {fmtDateTime(r.first_access_at)}
                  </td>
                  <td className="mono" style={{ whiteSpace: 'nowrap', color: 'var(--text-muted)', fontSize: 11 }}>
                    {fmtDateTime(r.last_access_at)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </main>
    </div>
  );
}
