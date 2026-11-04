import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type Admin, type Role } from '../api';
import { useAdmin, useCan } from '../auth';
import { date } from '../format';
import { Badge, CopyButton, ErrorBox, Field, Loading, Modal, PageHead, useToast } from '../ui';

const ROLES: Array<{ value: Role; label: string; help: string }> = [
  { value: 'super_admin', label: 'Super admin', help: 'Everything, including team management and the platform-wide switch' },
  { value: 'billing', label: 'Billing', help: 'Subscribers, plans, terms, modules; suspend and cancel' },
  { value: 'support', label: 'Support', help: 'Read everything and view subscribers read-only' },
  { value: 'read_only', label: 'Read-only', help: 'Read subscribers, dashboard and the audit log' },
];

export function TeamPage() {
  const can = useCan();
  return (
    <>
      <PageHead title={can.superAdmin ? 'Team & sessions' : 'My sessions'} sub="Who can use this console, and where they are signed in." />
      {can.superAdmin && <Admins />}
      <Sessions />
    </>
  );
}

function Admins() {
  const me = useAdmin()!;
  const toast = useToast();
  const qc = useQueryClient();
  const admins = useQuery({ queryKey: ['admins'], queryFn: api.admins });
  const [creating, setCreating] = useState(false);
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: object }) => api.updateAdmin(id, body),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['admins'] }); await qc.invalidateQueries({ queryKey: ['sessions'] }); toast('Saved'); },
    onError: (e) => toast((e as Error).message, true),
  });

  return (
    <section className="card">
      <div className="card-head"><h2>Operators</h2><button className="btn primary small" onClick={() => setCreating(true)}>Add operator</button></div>
      <ErrorBox error={admins.error} />
      {admins.isLoading ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Operator</th><th>Role</th><th>Last sign-in</th><th>Status</th><th /></tr></thead>
            <tbody>
              {admins.data?.map((a: Admin) => (
                <tr key={a.id} className={a.is_active ? '' : 'dim-row'}>
                  <td><strong>{a.full_name}</strong>{a.id === me.id && ' (you)'}<div className="muted">{a.email}</div></td>
                  <td>
                    <select aria-label={`Role for ${a.email}`} value={a.role} disabled={a.id === me.id || update.isPending}
                      onChange={(e) => update.mutate({ id: a.id, body: { role: e.target.value } })}>
                      {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  </td>
                  <td className="nowrap">{date(a.last_login_at, true)}</td>
                  <td><Badge value={a.is_active ? 'active' : 'deactivated'} tone={a.is_active ? 'good' : 'bad'} /></td>
                  <td className="right">
                    {a.id !== me.id && (
                      <button className={`btn small ${a.is_active ? '' : 'primary'}`} disabled={update.isPending}
                        onClick={() => update.mutate({ id: a.id, body: { is_active: !a.is_active } })}>
                        {a.is_active ? 'Deactivate' : 'Reactivate'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ marginBottom: 0 }}>Changing a role or deactivating signs that operator out everywhere immediately.</p>
      {creating && <CreateAdmin onClose={() => setCreating(false)} />}
    </section>
  );
}

function CreateAdmin({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ email: '', full_name: '', role: 'support' as Role, password: '' });
  const create = useMutation({
    mutationFn: () => api.createAdmin({ ...f, email: f.email.trim(), full_name: f.full_name.trim() }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admins'] }),
  });

  if (create.data) {
    const d = create.data;
    return (
      <Modal title={`${d.admin.full_name} was added`} onClose={onClose} wide
        lead="Give these to the new operator over a secure channel. They are shown only once and cannot be retrieved later.">
        <div className="alert warn">Closing this window discards them. The operator signs in with their password and a code from the authenticator app.</div>
        <h3>Authenticator setup key</h3>
        <div className="secret-box">{d.totp_secret}</div>
        <p className="muted">Add it to an authenticator app (or paste the full link below).</p>
        <div className="secret-box">{d.provisioning_uri}</div>
        <h3 style={{ marginTop: 16 }}>Recovery codes (each works once)</h3>
        <div className="codes">{d.recovery_codes.map((c) => <div key={c}>{c}</div>)}</div>
        <div className="foot">
          <CopyButton text={`Setup key: ${d.totp_secret}\nRecovery codes:\n${d.recovery_codes.join('\n')}`} label="Copy all" />
          <button className="btn primary" onClick={onClose}>I've saved them</button>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="Add operator" onClose={onClose} lead="Creates an account that must sign in with a password and an authenticator code.">
      <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <ErrorBox error={create.error} />
        <Field label="Full name" htmlFor="ad-name"><input id="ad-name" className="input" required minLength={2} value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="Email" htmlFor="ad-email"><input id="ad-email" className="input" type="email" required autoComplete="off" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Role" hint={ROLES.find((r) => r.value === f.role)?.help} htmlFor="ad-role">
          <select id="ad-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}</select>
        </Field>
        <Field label="Initial password" hint="At least 14 characters, mixing three of: lower, upper, digits, symbols. Must not contain their email name." htmlFor="ad-pass">
          <input id="ad-pass" className="input" type="password" required minLength={14} autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </Field>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create operator'}</button>
        </div>
      </form>
    </Modal>
  );
}

function Sessions() {
  const toast = useToast();
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: api.sessions });
  const admins = useQuery({ queryKey: ['admins'], queryFn: api.admins, retry: false, enabled: useCan().superAdmin });
  const who = new Map(admins.data?.map((a) => [a.id, a.email]));
  const revoke = useMutation({
    mutationFn: api.revokeSession,
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['sessions'] }); toast('Session ended'); },
    onError: (e) => toast((e as Error).message, true),
  });
  return (
    <section className="card">
      <div className="card-head"><h2>Active sessions</h2></div>
      <ErrorBox error={sessions.error} />
      {sessions.isLoading ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Operator</th><th>Signed in</th><th>Last active</th><th>From</th><th /></tr></thead>
            <tbody>
              {sessions.data?.map((s) => (
                <tr key={s.id}>
                  <td>{who.get(s.admin_id) ?? (s.current ? 'You' : s.admin_id.slice(0, 8))}{s.current && <> <Badge value="this session" tone="info" /></>}</td>
                  <td className="nowrap">{date(s.created_at, true)}</td>
                  <td className="nowrap">{date(s.last_seen_at, true)}</td>
                  <td><span className="mono">{s.ip ?? '—'}</span><div className="muted" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.user_agent}</div></td>
                  <td className="right"><button className="btn small" disabled={revoke.isPending} onClick={() => revoke.mutate(s.id)}>{s.current ? 'Sign out' : 'End'}</button></td>
                </tr>
              ))}
              {sessions.data?.length === 0 && <tr><td colSpan={5}><div className="empty">No active sessions.</div></td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ marginBottom: 0 }}>Sessions end after an hour idle or 12 hours.</p>
    </section>
  );
}
