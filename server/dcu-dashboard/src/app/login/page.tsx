'use client';

import { FormEvent, useState } from 'react';
import { LoginError, login } from '@/lib/api';
import { getSession, saveSession } from '@/lib/auth';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      // 저장하면 로그인 상태가 바뀌어 대시보드로 이동한다 (AuthGate)
      saveSession(await login(username.trim(), password));
      if (!getSession()) {
        setError('브라우저 저장소를 쓸 수 없어 로그인 상태를 유지할 수 없습니다. 사생활 보호 모드인지 확인하세요.');
      }
    } catch (err) {
      setError(err instanceof LoginError ? err.message : '로그인에 실패했습니다.');
      setPassword('');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-box" onSubmit={handleSubmit}>
        <div className="login-brand">
          <span className="logo-dot" />
          DCU 실습실 모니터링
        </div>
        <div className="login-sub">관리자 로그인</div>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="아이디"
          autoComplete="username"
          autoFocus
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="비밀번호"
          autoComplete="current-password"
          required
        />
        <button type="submit" disabled={submitting}>
          {submitting ? '로그인 중…' : '로그인'}
        </button>
        {error && (
          <div className="login-error" role="alert">
            {error}
          </div>
        )}
      </form>
    </div>
  );
}
