'use client';

import { useState } from 'react';
import { AlertInfo } from '@/lib/api';

interface Props {
  alerts: AlertInfo[];
}

const HIDDEN_STORAGE_KEY = 'dcu.recentAlerts.hidden';
const MAX_SHOWN = 15;

/**
 * 알림 → 필터 키.
 * 화면 표시(빨강)는 프로그램과 상관없이 따로 묶는다 — 카카오톡처럼 늘 켜 두는 프로그램의 실행 알림을 숨겨도
 * 그 프로그램을 화면에 띄운 순간은 계속 보이게 하기 위함.
 */
function filterKey(a: AlertInfo): string {
  if (a.code === 'ON_SCREEN') return 'screen';
  if (a.process_name) return `proc:${a.process_name.toLowerCase()}`;
  if (a.code === 'BLOCKED_URL' || a.code === 'MESSENGER_DETECTED') return 'site';
  return 'other';
}

const FIXED_LABELS: Record<string, string> = { screen: '화면 표시', site: '사이트', other: '기타' };

/** 숨긴 필터 (이 브라우저에만 저장, 다른 PC 상세·새로고침에도 유지). 저장소를 못 쓰면 빈 값. */
function loadHidden(): Set<string> {
  try {
    const raw = window.localStorage.getItem(HIDDEN_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : []);
  } catch {
    return new Set();
  }
}

function saveHidden(hidden: Set<string>) {
  try {
    window.localStorage.setItem(HIDDEN_STORAGE_KEY, JSON.stringify([...hidden]));
  } catch {
    /* 저장 불가(시크릿 창 등) — 이번 화면에서만 적용 */
  }
}

export default function RecentAlerts({ alerts }: Props) {
  const [hidden, setHidden] = useState<Set<string>>(loadHidden);

  // 필터 칩: 화면 표시 → 사이트 → 프로그램(알림 많은 순) → 기타
  const chips = new Map<string, { label: string; count: number }>();
  for (const a of alerts) {
    const key = filterKey(a);
    const chip = chips.get(key) ?? { label: FIXED_LABELS[key] ?? a.process_name ?? key, count: 0 };
    chip.count += 1;
    chips.set(key, chip);
  }
  const rank = (key: string) => (key === 'screen' ? 0 : key === 'site' ? 1 : key === 'other' ? 3 : 2);
  const chipList = [...chips.entries()].sort(
    ([ka, a], [kb, b]) => rank(ka) - rank(kb) || b.count - a.count || a.label.localeCompare(b.label),
  );

  const visible = alerts.filter((a) => !hidden.has(filterKey(a)));
  const hiddenCount = alerts.length - visible.length;

  const update = (next: Set<string>) => {
    setHidden(next);
    saveHidden(next);
  };
  const toggle = (key: string) => {
    const next = new Set(hidden);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    update(next);
  };

  return (
    <>
      <div className="modal-section-title" style={{ color: 'var(--warning)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span>
          최근 알림 ({hiddenCount > 0 ? `표시 ${visible.length} / 전체 ${alerts.length}건` : `${alerts.length}건`})
        </span>
        {hiddenCount > 0 && (
          <button type="button" className="alert-filter-reset" onClick={() => update(new Set())}>
            모두 보기
          </button>
        )}
      </div>

      <div className="alert-filter" role="group" aria-label="최근 알림 필터 (눌러서 숨기기·보이기)">
        {chipList.map(([key, chip]) => {
          const isHidden = hidden.has(key);
          return (
            <button
              key={key}
              type="button"
              className={`alert-filter-chip ${key === 'screen' ? 'screen' : ''} ${isHidden ? 'hidden' : ''}`}
              aria-pressed={!isHidden}
              title={isHidden ? '눌러서 다시 보이기' : '눌러서 숨기기'}
              onClick={() => toggle(key)}
            >
              {chip.label} <span className="count">{chip.count}</span>
            </button>
          );
        })}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {visible.slice(0, MAX_SHOWN).map((a, i) => (
          <div key={`${a.code}|${a.message}|${a.detected_at}|${i}`} style={{
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
        {visible.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '8px 0' }}>
            필터로 모든 알림이 숨겨져 있습니다.
          </div>
        )}
      </div>
    </>
  );
}
