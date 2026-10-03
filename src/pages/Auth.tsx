import React, { useState } from 'react';
import { get, post } from '../api/client';
import { useApi } from '../hooks/useApi';
import { useAuth } from '../context/AppContext';
import { Async, Badge, Button, ErrorBox, Field, Page, Panel, Table, Td, fmtDateTime, inputClass } from '../components/ui';

export const Login: React.FC = () => {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await login(username, password); } catch (x) { setErr(x as Error); } finally { setBusy(false); }
  };
  return (
    <div className="min-h-screen flex items-center justify-center bg-[#F7F7F5]">
      <form onSubmit={submit} className="w-[340px] bg-white border border-[#D9DCD8] rounded-[4px] p-5 space-y-3">
        <div>
          <div className="font-bold text-[15px] uppercase tracking-wider">CausalOps</div>
          <div className="text-[12px] text-[#5E6561]">Sign in to continue</div>
        </div>
        <Field label="Username"><input autoFocus className={`${inputClass} w-full`} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></Field>
        <Field label="Password"><input type="password" className={`${inputClass} w-full`} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
        <ErrorBox error={err} />
        <Button variant="primary" className="w-full" disabled={busy || !username || !password}>{busy ? 'Signing in…' : 'Sign in'}</Button>
      </form>
    </div>
  );
};

export const ChangePassword: React.FC<{ forced?: boolean }> = ({ forced }) => {
  const { refreshUser, logout } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [err, setErr] = useState<Error | null>(null);
  const [done, setDone] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (next !== confirm) { setErr(new Error('The new passwords do not match')); return; }
    try {
      await post('/auth/password', { currentPassword: current, newPassword: next });
      setDone(true);
      await refreshUser();
    } catch (x) { setErr(x as Error); }
  };
  return (
    <div className={forced ? 'min-h-screen flex items-center justify-center bg-[#F7F7F5]' : ''}>
      <form onSubmit={submit} className="w-[360px] bg-white border border-[#D9DCD8] rounded-[4px] p-5 space-y-3">
        <div className="font-semibold text-[14px]">{forced ? 'Choose a new password' : 'Change password'}</div>
        {forced && <div className="text-[12px] text-[#5E6561]">This account uses a bootstrap password; it must be changed before continuing.</div>}
        <Field label="Current password"><input type="password" className={`${inputClass} w-full`} value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
        <Field label="New password" hint="at least 12 characters"><input type="password" className={`${inputClass} w-full`} value={next} onChange={(e) => setNext(e.target.value)} /></Field>
        <Field label="Repeat new password"><input type="password" className={`${inputClass} w-full`} value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
        <ErrorBox error={err} />
        {done && <div className="text-[12px] text-[#2F7D5C]">Password changed.</div>}
        <div className="flex gap-2"><Button variant="primary" disabled={!current || next.length < 12}>Change password</Button>
          {forced && <Button type="button" onClick={logout}>Sign out</Button>}</div>
      </form>
    </div>
  );
};

interface UserRow { username: string; role: string; enabled: boolean; mustChangePassword: boolean; createdAt: string; lastLoginAt: string | null }

export const Users: React.FC = () => {
  const users = useApi(() => get<UserRow[]>('/users'), []);
  const [username, setUsername] = useState('');
  const [role, setRole] = useState('VIEWER');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState<Error | null>(null);
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); } catch (e) { setErr(e as Error); } users.reload(); };
  return (
    <Page title="Users" subtitle="VIEWER reads everything; OPERATOR also approves, rejects and rolls back remediation and injects faults; ADMIN also manages environments, autonomy and users.">
      <Panel title="Add user">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
          <Field label="Username"><input className={`${inputClass} w-full`} value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
          <Field label="Role"><select className={`${inputClass} w-full`} value={role} onChange={(e) => setRole(e.target.value)}>
            {['VIEWER', 'OPERATOR', 'ADMIN'].map((r) => <option key={r}>{r}</option>)}</select></Field>
          <Field label="Initial password" hint="the user must change it at first sign-in"><input type="password" className={`${inputClass} w-full`} value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <Button variant="primary" disabled={!username || password.length < 12}
                  onClick={() => run(async () => { await post('/users', { username, role, password }); setUsername(''); setPassword(''); })}>Add</Button>
        </div>
      </Panel>
      <ErrorBox error={err} />
      <Panel dense>
        <Async state={users}>
          {(list) => (
            <Table head={['User', 'Role', 'State', 'Created', 'Last sign-in', '']}>
              {list.map((u) => (
                <tr key={u.username}>
                  <Td className="font-semibold">{u.username}</Td><Td><Badge tone="info">{u.role}</Badge></Td>
                  <Td>{u.enabled ? (u.mustChangePassword ? <Badge tone="warn">must change password</Badge> : <Badge tone="good">active</Badge>) : <Badge tone="muted">disabled</Badge>}</Td>
                  <Td mono>{fmtDateTime(u.createdAt)}</Td><Td mono>{fmtDateTime(u.lastLoginAt)}</Td>
                  <Td><Button onClick={() => run(() => post(`/users/${encodeURIComponent(u.username)}/${u.enabled ? 'disable' : 'enable'}`))}>{u.enabled ? 'Disable' : 'Enable'}</Button></Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
      <Panel title="Your password"><ChangePassword /></Panel>
    </Page>
  );
};
