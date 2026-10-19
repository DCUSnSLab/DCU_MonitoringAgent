'use client';

import { useEffect, useState } from 'react';
import { AgentSummary } from '@/lib/api';

/** 1초마다 갱신되는 현재 시각. 이 컴포넌트들(DANGER 카드·열린 상세 모달)에서만 시계가 돌게 한다. */
function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** 경과 시간 표시: 45초 / 2분 05초 / 1시간 3분 */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}분 ${String(s % 60).padStart(2, '0')}초`;
  return `${Math.floor(m / 60)}시간 ${m % 60}분`;
}

const toMs = (iso?: string | null) => (iso ? Date.parse(iso) : NaN);

/** 현재 탐지 항목이 화면 앞에 떠 있은 시간 (예: "2분 05초") */
export function OnScreenElapsed({ since }: { since?: string | null }) {
  const now = useNow();
  const start = toMs(since);
  if (Number.isNaN(start)) return null;
  return <>{formatElapsed((now - start) / 1000)}</>;
}

/**
 * DANGER 구간의 지속 시간.
 * - 아직 화면에 떠 있으면: "2분 10초째"
 * - 내린 뒤 DANGER 유지 중이면: "2분 10초 동안 표시 · 30초 전 종료" (compact: "2분 10초 동안")
 */
export function DangerDuration({ agent, compact = false }: { agent: AgentSummary; compact?: boolean }) {
  const now = useNow();
  const since = toMs(agent.danger_since);
  const last = toMs(agent.danger_last_at);
  if (Number.isNaN(since) || Number.isNaN(last)) return null;

  if (agent.danger_on_screen) {
    return <>{formatElapsed((now - since) / 1000)}째</>;
  }
  const shown = (last - since) / 1000;
  const duration = shown < 1 ? '잠깐' : `${formatElapsed(shown)} 동안`;
  if (compact) return <>{duration} 표시</>;
  return <>{duration} 표시 · {formatElapsed((now - last) / 1000)} 전 종료</>;
}
