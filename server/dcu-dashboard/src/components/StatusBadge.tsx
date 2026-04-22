'use client';

import { UserState } from '@/lib/api';

interface Props {
  state: UserState;
  size?: 'sm' | 'md';
}

const LABELS: Record<UserState, string> = {
  safe: 'SAFE',
  warning: 'WARNING',
  danger: 'DANGER',
  unknown: 'OFFLINE',
};

export default function StatusBadge({ state, size = 'md' }: Props) {
  return (
    <span
      className={`badge ${state}`}
      style={size === 'sm' ? { fontSize: '10px', padding: '2px 8px' } : undefined}
    >
      {LABELS[state] ?? 'UNKNOWN'}
    </span>
  );
}
