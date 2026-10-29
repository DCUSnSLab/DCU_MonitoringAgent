'use client';

import { FormEvent, useEffect, useState } from 'react';
import Header from '@/components/Header';
import { refreshSession, useSession } from '@/lib/auth';
import { DashboardRole, DashboardUser, UserList, getDashboardUsers, updateDashboardUser } from '@/lib/admin';

const ROLES: Record<DashboardRole, string> = { pending: '접근 없음', viewer: '조회 사용자', admin: '관리자' };

function UserRow({ user, self, disabled, onSave }: {
  user: DashboardUser; self: boolean; disabled: boolean;
  onSave: (user: DashboardUser, role: DashboardRole, enabled: boolean) => void;
}) {
  const [role, setRole] = useState(user.role);
  const [enabled, setEnabled] = useState(user.enabled);
  const changed = role !== user.role || enabled !== user.enabled;
  return (
    <tr>
      <td>
        <strong>{user.name || '이름 미등록'} {self && <span className="admin-self">본인</span>}</strong>
        <div>{user.username === user.sub ? '첫 로그인 전' : user.username}</div>
        <div className="admin-sub mono">{user.sub}</div>
      </td>
      <td>{user.email || '—'}</td>
      <td><span className={`access-status ${!user.enabled ? 'disabled' : user.role}`}>
        {!user.enabled ? '접근 중지' : user.role === 'pending' ? '승인 대기' : ROLES[user.role]}
      </span></td>
      <td>
        <select aria-label={`${user.username} 권한`} value={role} disabled={disabled}
          onChange={(event) => setRole(event.target.value as DashboardRole)}>
          {Object.entries(ROLES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </td>
      <td><label className="access-enabled">
        <input type="checkbox" checked={enabled} disabled={disabled}
          onChange={(event) => setEnabled(event.target.checked)} aria-label={`${user.username} 계정 활성`} /> 활성
      </label></td>
      <td><button type="button" disabled={disabled || !changed} onClick={() => onSave(user, role, enabled)}>저장</button></td>
    </tr>
  );
}

export default function AdminPage() {
  const session = useSession();
  const isAdmin = session?.role === 'admin';
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<UserList | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (!isAdmin) return;
    const controller = new AbortController();
    getDashboardUsers(search, offset, controller.signal).then((result) => {
      setData(result); setError(''); setLoading(false);
    }).catch((err) => {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : '사용자 목록을 불러오지 못했습니다.');
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [isAdmin, search, offset, reload]);

  function refresh() {
    setLoading(true); setReload((value) => value + 1);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true); setOffset(0); setSearch(query.trim()); setReload((value) => value + 1);
  }

  async function save(user: DashboardUser, role: DashboardRole, enabled: boolean) {
    setSaving(true); setError(''); setNotice('');
    try {
      await updateDashboardUser(user.sub, role, enabled);
      setNotice(`${user.name || user.username}님의 권한을 변경했습니다.`);
      await refreshSession();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : '권한 변경에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dashboard-layout">
      <Header />
      <main className="main-content access-management">
        <h1>사용자 관리</h1>
        {!isAdmin ? <p role="alert">관리자만 이 페이지에 접근할 수 있습니다.</p> : <>
          <p className="access-description">통합계정으로 로그인한 사용자의 모니터링 접근 권한을 관리합니다.</p>
          <div className="access-help">
            <span><strong>관리자</strong> 대시보드·통계 조회 및 사용자 권한 관리</span>
            <span><strong>조회 사용자</strong> 대시보드·통계 조회</span>
            <span><strong>접근 없음 / 접근 중지</strong> 로그인 후 접근 거부</span>
          </div>
          <form className="access-toolbar" onSubmit={submit}>
            <input aria-label="사용자 검색" placeholder="이름, 아이디, 이메일 또는 UUID 검색" value={query}
              onChange={(event) => setQuery(event.target.value)} />
            <button type="submit" disabled={saving}>검색</button>
            <button type="button" onClick={refresh} disabled={saving}>새로고침</button>
            <span>{data ? `총 ${data.total}명` : ''}</span>
          </form>
          {error && <p className="access-error" role="alert">{error}</p>}
          {notice && <p className="access-notice" role="status">{notice}</p>}
          <div className="access-table" aria-busy={loading || saving}>
            <table>
              <thead><tr><th>사용자</th><th>이메일</th><th>현재 상태</th><th>권한</th><th>계정</th><th>변경</th></tr></thead>
              <tbody>
                {loading ? <tr><td colSpan={6}>사용자 목록을 불러오는 중…</td></tr>
                  : data?.users.length ? data.users.map((user) => <UserRow
                    key={`${user.sub}:${user.role}:${user.enabled}`} user={user} self={user.sub === data.current_sub}
                    disabled={saving} onSave={save} />)
                    : <tr><td colSpan={6}>등록된 사용자가 없습니다. 사용자가 SSO로 로그인하면 승인 대기로 표시됩니다.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="access-pagination">
            <button type="button" disabled={loading || saving || offset === 0} onClick={() => {
              setLoading(true); setOffset(Math.max(0, offset - 50));
            }}>이전</button>
            <span>{Math.floor(offset / 50) + 1}페이지</span>
            <button type="button" disabled={loading || saving || offset + 50 >= (data?.total ?? 0)} onClick={() => {
              setLoading(true); setOffset(offset + 50);
            }}>다음</button>
          </div>
          <p className="access-description">접근 권한을 회수하면 기존 세션도 종료됩니다. 마지막 관리자의 권한은 회수할 수 없습니다.</p>
        </>}
      </main>
    </div>
  );
}
