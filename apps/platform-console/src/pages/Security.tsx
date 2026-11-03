import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type Alert, type Approval } from '../api';
import { useAdmin, useCan } from '../auth';
import { date, label, relativeDays } from '../format';
import { Badge, ErrorBox, Loading, Modal, PageHead, ReasonDialog, Segmented, useToast } from '../ui';

type Tab = 'approvals' | 'alerts';

export function SecurityPage() {
  const [tab, setTab] = useState<Tab>('approvals');
  const summary = useQuery({ queryKey: ['security-summary'], queryFn: api.alertSummary, refetchInterval: 60_000 });
  const pending = summary.data?.pending_approvals ?? 0;
  const open = Object.values(summary.data?.open_alerts ?? {}).reduce((a, b) => a + b, 0);
  return (
    <>
      <PageHead title="Approvals & alerts" sub="Dangerous actions wait here for a second super admin; security events land here as alerts.">
        <Segmented name="Section" value={tab} onChange={setTab}
          options={[{ value: 'approvals', label: `Approvals${pending ? ` (${pending})` : ''}` }, { value: 'alerts', label: `Alerts${open ? ` (${open})` : ''}` }]} />
      </PageHead>
      {tab === 'approvals' ? <Approvals /> : <Alerts />}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ approvals

const STATUS_TONE: Record<Approval['status'], string> = { pending: 'warn', executed: 'good', rejected: 'bad', cancelled: '', expired: '', failed: 'bad' };

function Approvals() {
  const me = useAdmin()!;
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const [view, setView] = useState<'pending' | 'all'>('pending');
  const [deciding, setDeciding] = useState<{ approval: Approval; kind: 'approve' | 'reject' } | null>(null);
  const list = useQuery({ queryKey: ['approvals', view], queryFn: () => api.approvals(view === 'pending' ? 'pending' : undefined) });
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ['approvals'] }), qc.invalidateQueries({ queryKey: ['security-summary'] }), qc.invalidateQueries()]);
  const withdraw = useMutation({
    mutationFn: api.cancelApproval,
    onSuccess: async () => { await refresh(); toast('Request withdrawn'); },
    onError: (e) => toast((e as Error).message, true),
  });

  return (
    <>
      <div className="filters">
        <Segmented name="Show" value={view} onChange={setView} options={[{ value: 'pending', label: 'Waiting' }, { value: 'all', label: 'History' }]} />
      </div>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.data?.length === 0 ? <div className="card"><div className="empty">{view === 'pending' ? 'Nothing is waiting for approval.' : 'No requests yet.'}</div></div> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Request</th><th>Asked by</th><th>When</th><th>Status</th><th /></tr></thead>
            <tbody>
              {list.data?.map((a) => {
                const mine = a.requested_by === me.email;
                return (
                  <tr key={a.id}>
                    <td>
                      <strong>{a.summary}</strong>
                      <div className="muted">{a.reason}</div>
                      {a.status === 'failed' && <div className="diff-no">Could not be carried out: {String(a.result?.error ?? '')}</div>}
                      {a.decision_note && <div className="muted">“{a.decision_note}” — {a.decided_by}</div>}
                    </td>
                    <td>{a.requested_by}{mine && ' (you)'}</td>
                    <td className="nowrap">{date(a.requested_at, true)}{a.status === 'pending' && <div className="muted">expires {relativeDays(a.expires_at)}</div>}</td>
                    <td><Badge value={a.status} tone={STATUS_TONE[a.status]} /></td>
                    <td className="right nowrap">
                      {a.status === 'pending' && can.superAdmin && (
                        <span className="actions" style={{ justifyContent: 'flex-end' }}>
                          {mine ? (
                            <button className="btn small" disabled={withdraw.isPending} onClick={() => withdraw.mutate(a.id)}>Withdraw</button>
                          ) : (
                            <>
                              <button className="btn small primary" onClick={() => setDeciding({ approval: a, kind: 'approve' })}>Approve…</button>
                              <button className="btn small" onClick={() => setDeciding({ approval: a, kind: 'reject' })}>Reject…</button>
                            </>
                          )}
                        </span>
                      )}
                      {a.status === 'pending' && mine && <div className="muted">needs another super admin</div>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {deciding?.kind === 'approve' && <ApproveDialog approval={deciding.approval} onDone={async () => { setDeciding(null); await refresh(); }} onClose={() => setDeciding(null)} />}
      {deciding?.kind === 'reject' && <RejectDialog approval={deciding.approval} onDone={async () => { setDeciding(null); await refresh(); }} onClose={() => setDeciding(null)} />}
    </>
  );
}

function ApproveDialog({ approval, onDone, onClose }: { approval: Approval; onDone: () => void; onClose: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState('');
  const run = useMutation({
    mutationFn: () => api.approve(approval.id, note.trim() || undefined),
    onSuccess: () => { toast('Approved and carried out'); onDone(); },
  });
  return (
    <Modal title="Approve and run this?" onClose={onClose} lead="It runs immediately, exactly as requested, with your name on the audit entry.">
      <form onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        <ErrorBox error={run.error} />
        <div className="alert warn"><strong>{approval.summary}</strong><br />Reason given: {approval.reason}</div>
        <div className="field"><label htmlFor="ap-note">Note (optional)</label>
          <input id="ap-note" className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} /></div>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn danger" disabled={run.isPending}>{run.isPending ? 'Running…' : 'Approve and run'}</button>
        </div>
      </form>
    </Modal>
  );
}

function RejectDialog({ approval, onDone, onClose }: { approval: Approval; onDone: () => void; onClose: () => void }) {
  const toast = useToast();
  const run = useMutation({
    mutationFn: (note: string) => api.rejectApproval(approval.id, note),
    onSuccess: () => { toast('Request rejected'); onDone(); },
  });
  return (
    <ReasonDialog title="Reject this request?" confirm="Reject" danger busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}
      lead={approval.summary} />
  );
}

// ------------------------------------------------------------------------------------------------ alerts

const SEVERITY_TONE: Record<Alert['severity'], string> = { info: 'info', warning: 'warn', critical: 'bad' };

function Alerts() {
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const [openOnly, setOpenOnly] = useState<'open' | 'all'>('open');
  const list = useQuery({ queryKey: ['alerts', openOnly], queryFn: () => api.alerts({ open_only: openOnly === 'open', limit: 100 }) });
  const ack = useMutation({
    mutationFn: api.acknowledgeAlert,
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['alerts'] }); await qc.invalidateQueries({ queryKey: ['security-summary'] }); },
    onError: (e) => toast((e as Error).message, true),
  });
  return (
    <>
      <div className="filters">
        <Segmented name="Show" value={openOnly} onChange={setOpenOnly} options={[{ value: 'open', label: 'Unacknowledged' }, { value: 'all', label: 'All' }]} />
      </div>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.data?.length === 0 ? <div className="card"><div className="empty">No alerts.</div></div> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Severity</th><th>What happened</th><th>When</th><th /></tr></thead>
            <tbody>
              {list.data?.map((a) => (
                <tr key={a.id} className={a.acknowledged_at ? 'dim-row' : ''}>
                  <td><Badge value={a.severity} tone={SEVERITY_TONE[a.severity]} /></td>
                  <td>
                    {a.message}
                    <div className="muted mono">{a.kind}{a.notified_at ? ' · sent to webhook' : ''}</div>
                  </td>
                  <td className="nowrap">{date(a.created_at, true)}<div className="muted">{relativeDays(a.created_at)}</div></td>
                  <td className="right">
                    {a.acknowledged_at ? <span className="muted">{label('acknowledged')}</span>
                      : can.superAdmin && <button className="btn small" disabled={ack.isPending} onClick={() => ack.mutate(a.id)}>Acknowledge</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
