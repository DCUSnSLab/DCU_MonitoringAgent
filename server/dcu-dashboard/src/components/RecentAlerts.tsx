'use client';

import { useState } from 'react';
import { AlertInfo } from '@/lib/api';

interface Props {
  alerts: AlertInfo[];
}

const MAX_SHOWN = 15;

type Category = 'screen' | 'program' | 'site' | 'messenger' | 'other';

const CATEGORY_OPTIONS: { value: Category; label: string }[] = [
  { value: 'screen', label: '화면 표시' },
  { value: 'program', label: '차단 프로그램' },
  { value: 'site', label: '차단 사이트(웹)' },
  { value: 'messenger', label: '메신저' },
  { value: 'other', label: '기타' },
];

// 화면 표시 알림(ON_SCREEN)의 메시지 앞부분 = 원래 탐지 분류 (서버 on_screen_alert 의 라벨)
const ON_SCREEN_PREFIX: [string, Category][] = [
  ['메신저', 'messenger'],
  ['차단 사이트', 'site'],
  ['차단 프로그램', 'program'],
  ['허용되지 않은 프로그램', 'program'],
];

/** 알림이 속한 큰 분류들. 화면 표시 알림은 '화면 표시'와 원래 분류(메신저 등) 양쪽에 속한다. */
function categoriesOf(a: AlertInfo): Category[] {
  switch (a.code) {
    case 'ON_SCREEN': {
      const origin = ON_SCREEN_PREFIX.find(([prefix]) => a.message.startsWith(prefix))?.[1];
      return origin ? ['screen', origin] : ['screen'];
    }
    case 'BLOCKED_PROCESS':
    case 'UNAUTHORIZED_PROCESS':
      return ['program'];
    case 'BLOCKED_URL':
      return ['site'];
    case 'MESSENGER_DETECTED':
      return ['messenger'];
    default:
      return ['other'];
  }
}

export default function RecentAlerts({ alerts }: Props) {
  const [category, setCategory] = useState<Category | 'all'>('all');

  const counts = new Map<Category, number>();
  for (const a of alerts) {
    for (const c of categoriesOf(a)) counts.set(c, (counts.get(c) ?? 0) + 1);
  }
  // '기타'는 해당 알림이 있을 때만 선택지로 보인다
  const options = CATEGORY_OPTIONS.filter((o) => o.value !== 'other' || counts.has('other'));
  const visible = category === 'all' ? alerts : alerts.filter((a) => categoriesOf(a).includes(category));

  return (
    <>
      <div className="modal-section-title" style={{ color: 'var(--warning)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ flex: 1 }}>
          최근 알림 ({category === 'all' ? `${alerts.length}건` : `${visible.length} / 전체 ${alerts.length}건`})
        </span>
        <select
          className="alert-category-select"
          value={category}
          onChange={(e) => setCategory(e.target.value as Category | 'all')}
          aria-label="최근 알림 분류"
        >
          <option value="all">전체 ({alerts.length})</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label} ({counts.get(o.value) ?? 0})
            </option>
          ))}
        </select>
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
            이 분류의 최근 알림이 없습니다.
          </div>
        )}
      </div>
    </>
  );
}
