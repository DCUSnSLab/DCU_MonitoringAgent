'use client';

import { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useSession } from '@/lib/auth';

const LOGIN_PATH = '/login';

/**
 * 로그인하지 않았으면 로그인 화면으로, 로그인했는데 로그인 화면이면 대시보드로 보낸다.
 * 이동하기 전에는 아무것도 그리지 않는다 (로그인 없이 대시보드가 잠깐 보이거나 데이터를 부르지 않도록).
 * 실제 접근 제어는 서버가 한다 — 토큰이 없으면 대시보드·통계 API와 실시간 연결을 거부.
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const session = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const onLoginPage = pathname === LOGIN_PATH;
  const loggedIn = !!session;

  useEffect(() => {
    if (session === undefined) return; // 아직 확인 전
    if (!loggedIn && !onLoginPage) router.replace(LOGIN_PATH);
    else if (loggedIn && onLoginPage) router.replace('/');
  }, [session, loggedIn, onLoginPage, router]);

  if (session === undefined || loggedIn === onLoginPage) return null;
  return <>{children}</>;
}
