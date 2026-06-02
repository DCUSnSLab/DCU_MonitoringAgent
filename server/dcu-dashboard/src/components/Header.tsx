'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const TABS = [
  { href: '/', label: '대시보드' },
  { href: '/statistics', label: '통계' },
];

export default function Header({ children }: { children?: React.ReactNode }) {
  const pathname = usePathname();

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
      <div className="header-right">{children}</div>
    </header>
  );
}
