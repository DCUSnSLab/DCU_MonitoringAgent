'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { clearSession, useSession } from '@/lib/auth';

const TABS = [
  { href: '/', label: '대시보드' },
  { href: '/statistics', label: '통계' },
];

export default function Header({ children }: { children?: React.ReactNode }) {
  const pathname = usePathname();
  const session = useSession();

  return (
    <header className="header">
      <div className="header-left">
        <div className="header-logo">
          <span className="logo-dot" />
          DCU 실습실 모니터링
        </div>
        <nav className="header-nav">
          {TABS.map((tab) => {
            const active = tab.href === '/' ? pathname === '/' : pathname.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={`nav-tab ${active ? 'active' : ''}`}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <div className="header-right">
        {children}
        {session && (
          <div className="header-user">
            <span>{session.username}</span>
            {/* 로그인 정보를 지우면 로그인 화면으로 이동 (AuthGate) */}
            <button type="button" className="logout-btn" onClick={clearSession}>
              로그아웃
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
