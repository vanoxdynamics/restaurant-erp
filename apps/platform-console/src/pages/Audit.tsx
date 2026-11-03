import { useInfiniteQuery, useMutation } from '@tanstack/react-query';
import { Fragment, useState } from 'react';
import { api, type AuditEntry } from '../api';
import { useCan } from '../auth';
import { date } from '../format';
import { ErrorBox, Loading, PageHead } from '../ui';

const ACTIONS = [
  'auth.login', 'auth.login_failed', 'auth.2fa_failed', 'auth.refresh_reuse_detected', 'auth.step_up', 'admin.create', 'admin.update',
  'org.create', 'org.suspend', 'org.reinstate', 'org.impersonate', 'org.module_override', 'subscription.update', 'subscription.extend',
  'subscription.cancel', 'subscription.activate', 'platform.module_switch', 'platform.module_bulk', 'plan.create', 'plan.update',
];
const PAGE = 50;

export function AuditPage() {
  const can = useCan();
  const [action, setAction] = useState('');
  const [open, setOpen] = useState<number | null>(null);
  const log = useInfiniteQuery({
    queryKey: ['audit', action],
    queryFn: ({ pageParam }) => api.audit({ limit: PAGE, before_seq: pageParam, action: action || undefined }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1]!.seq : undefined),
  });
  const verify = useMutation({ mutationFn: api.verifyAudit });
  const rows = log.data?.pages.flat() ?? [];

  return (
    <>
      <PageHead title="Audit log" sub="Every platform action, newest first. Entries are chained by hash and cannot be edited or deleted.">
        {can.superAdmin && <button className="btn" disabled={verify.isPending} onClick={() => verify.mutate()}>{verify.isPending ? 'Verifying…' : 'Verify integrity'}</button>}
      </PageHead>

      {verify.data && (verify.data.ok
        ? <div className="alert good" role="status">Chain intact: {verify.data.checked} entries verified. Head hash <code>{verify.data.head_hash.slice(0, 16)}…</code> — export it somewhere the app cannot write to detect truncation.</div>
        : <div className="alert error" role="alert">Tampering detected at entry #{verify.data.first_bad_seq} after {verify.data.checked} good entries.</div>)}
      <ErrorBox error={verify.error ?? log.error} />

      <div className="filters">
        <select aria-label="Action" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">All actions</option>
          {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>

      {log.isLoading ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>#</th><th>When</th><th>Action</th><th>Target</th><th>Reason</th><th>IP</th></tr></thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={6}><div className="empty">No entries.</div></td></tr>}
              {rows.map((e) => (
                <Fragment key={e.seq}>
                  <tr className="link" onClick={() => setOpen(open === e.seq ? null : e.seq)} aria-expanded={open === e.seq}>
                    <td className="mono muted">{e.seq}</td>
                    <td className="nowrap">{date(e.created_at, true)}</td>
                    <td><code>{e.action}</code></td>
                    <td>{e.target_type ? `${e.target_type} ` : ''}<span className="muted mono">{e.target_id?.slice(0, 8)}</span></td>
                    <td>{e.reason ?? <span className="muted">—</span>}</td>
                    <td className="mono">{e.ip ?? '—'}</td>
                  </tr>
                  {open === e.seq && <tr><td colSpan={6}><Detail entry={e} /></td></tr>}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {log.hasNextPage && <p><button className="btn" disabled={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>{log.isFetchingNextPage ? 'Loading…' : 'Load older'}</button></p>}
    </>
  );
}

function Detail({ entry }: { entry: AuditEntry }) {
  return (
    <div className="grid cols-2">
      <div><h3>Before</h3><pre className="secret-box">{entry.before ? JSON.stringify(entry.before, null, 2) : '—'}</pre></div>
      <div><h3>After</h3><pre className="secret-box">{entry.after ? JSON.stringify(entry.after, null, 2) : '—'}</pre></div>
      <div><h3>Operator</h3><code>{entry.admin_id ?? 'unknown (failed sign-in)'}</code></div>
      <div><h3>Hash</h3><code style={{ overflowWrap: 'anywhere' }}>{entry.hash}</code></div>
    </div>
  );
}
