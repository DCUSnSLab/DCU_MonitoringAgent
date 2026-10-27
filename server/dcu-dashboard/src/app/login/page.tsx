'use client';

import { FormEvent, useState, useSyncExternalStore } from 'react';
import { LoginError, login } from '@/lib/api';
import { getSession, saveSession, startSSOLogin, useAuthState, refreshSession } from '@/lib/auth';

const subscribeLocation = () => () => {};
const locationSnapshot = () => window.location.search;
const serverLocation = () => '';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const auth = useAuthState();

  const search = useSyncExternalStore(subscribeLocation, locationSnapshot, serverLocation);
  const code = new URLSearchParams(search).get('error');
  const messages: Record<string, string> = {
    access_denied: '대시보드 접근 권한이 없습니다. 관리자에게 접근 승인을 요청하세요.',
    invalid_callback: '로그인 요청이 만료되었거나 유효하지 않습니다. 다시 로그인하세요.',
    sso_failed: 'SSO 로그인에 실패했습니다. 다시 시도하세요.',
  };
  const callbackError = code ? messages[code] ?? messages.sso_failed : null;

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
        <div className="login-sub">{auth.mode === 'sso' ? 'DCU 통합계정 로그인' : '관리자 로그인'}</div>
        {auth.mode === 'local' && <>
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
        </>}
        {auth.mode === 'sso' && <>
          <p>통합계정으로 인증한 뒤, 모니터링 접근 권한을 확인합니다.</p>
          <button type="button" onClick={startSSOLogin}>DCU SSO로 로그인</button>
        </>}
        {!auth.mode && <button type="button" onClick={() => void refreshSession()}>서버 연결 다시 확인</button>}
        {(error || callbackError || auth.error) && (
          <div className="login-error" role="alert">
            {error || callbackError || auth.error}
          </div>
        )}
      </form>
    </div>
  );
}
