import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'DCU 실습실 모니터링 대시보드',
  description: '실습실 PC 상태를 실시간으로 모니터링합니다.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
