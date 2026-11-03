import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, isApproval, newKey, type BillingTerm, type ChangePreview, type Effective, type ModuleState, type OrgDetail, type Plan } from '../api';
import { useCan } from '../auth';
import { date, label, localToIso, money, relativeDays } from '../format';
import { InvoiceTable } from './invoices';
import { Badge, CopyButton, ErrorBox, Field, Loading, Modal, PageHead, ReasonDialog, Segmented, Switch, useToast } from '../ui';

type Dialog = null | 'suspend' | 'reinstate' | 'cancel' | 'activate' | 'extend' | 'change' | 'impersonate' | 'profile'
  | { module: ModuleState; enable: boolean };

export function SubscriberDetailPage() {
  const { id = '' } = useParams();
  const can = useCan();
  const [dialog, setDialog] = useState<Dialog>(null);
  const { data: org, error, isLoading } = useQuery({ queryKey: ['organization', id], queryFn: () => api.organization(id) });
  if (isLoading) return <Loading />;
  if (error || !org) return <><Link to="/subscribers">← Subscribers</Link><ErrorBox error={error} /></>;

  const sub = org.subscription;
  const suspended = org.status === 'suspended';
  const canActivate = !!sub && (sub.status === 'trialing' || sub.status === 'expired' || sub.status === 'cancelled' || sub.status === 'paused');
  const close = () => setDialog(null);

  return (
    <>
      <p style={{ margin: '0 0 8px' }}><Link to="/subscribers">← Subscribers</Link></p>
      <PageHead title={org.name} sub={<span className="actions"><code>{org.slug}</code><Badge value={org.status} /><Badge value={org.access} /></span>}>
        {can.manageBilling && !suspended && canActivate && <button className="btn primary" onClick={() => setDialog('activate')}>Start paid period</button>}
        {can.manageBilling && !suspended && <button className="btn danger" onClick={() => setDialog('suspend')}>Suspend</button>}
        {can.manageBilling && suspended && <button className="btn primary" onClick={() => setDialog('reinstate')}>Reinstate</button>}
        {can.impersonate && <button className="btn" onClick={() => setDialog('impersonate')}>View as owner</button>}
      </PageHead>

      {suspended && (
        <div className="alert error" role="status">
          Suspended {date(org.suspended_at, true)}: {org.suspension_reason}. All sessions were revoked and nothing works until it is reinstated.
        </div>
      )}
      {org.access === 'read_only' && !suspended && (
        <div className="alert warn" role="status">This subscription is not active, so the subscriber can read data but not change it.</div>
      )}

      <div className="grid cols-2">
        <section className="card">
          <div className="card-head">
            <h2>Business</h2>
            {can.manageBilling && <button className="btn small" onClick={() => setDialog('profile')}>Edit</button>}
          </div>
          <dl className="kv">
            <dt>Legal name</dt><dd>{org.legal_name ?? '—'}</dd>
            <dt>Billing email</dt><dd>{org.billing_email ?? '—'}</dd>
            <dt>Country</dt><dd>{org.country ?? '—'}</dd>
            <dt>Customer since</dt><dd>{date(org.created_at)}</dd>
            <dt>Branches</dt><dd>{org.branches}{sub && effectiveLimit(sub.max_branches_override) ? ` of ${sub.max_branches_override}` : ''}</dd>
            <dt>Active staff</dt><dd>{org.active_staff}{sub && effectiveLimit(sub.max_staff_override) ? ` of ${sub.max_staff_override}` : ''}</dd>
          </dl>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Subscription</h2>
            <span className="actions">
              {can.manageBilling && sub && <button className="btn small" onClick={() => setDialog('change')}>Change plan / term</button>}
              {can.manageBilling && sub?.current_period_end && <button className="btn small" onClick={() => setDialog('extend')}>Extend</button>}
            </span>
          </div>
          {!sub ? <div className="empty">No subscription on record.</div> : (
            <dl className="kv">
              <dt>Plan</dt><dd>{sub.plan_code}</dd>
              <dt>Billing term</dt><dd><strong>{label(sub.billing_term)}</strong> · {money(sub.price_amount, sub.currency)} / {sub.billing_term === 'monthly' ? 'month' : 'year'}</dd>
              <dt>Status</dt><dd><Badge value={sub.effective_status ?? sub.status} />{sub.cancel_at_period_end && <> <Badge value="cancels at period end" tone="warn" /></>}</dd>
              <dt>Current period</dt><dd>{date(sub.current_period_start)} → {date(sub.current_period_end)} <span className="muted">{relativeDays(sub.current_period_end)}</span></dd>
              {sub.trial_ends_at && <><dt>Trial ends</dt><dd>{date(sub.trial_ends_at)} <span className="muted">{relativeDays(sub.trial_ends_at)}</span></dd></>}
              <dt>Auto-renew</dt><dd>{sub.auto_renew ? 'On' : 'Off'}</dd>
              {Number(sub.credit_balance) > 0 && <><dt>Account credit</dt><dd>{money(sub.credit_balance, sub.currency)} <span className="muted">applied to the next invoice</span></dd></>}
              {sub.pending_billing_term && (
                <><dt>Scheduled</dt><dd>Switches to <strong>{label(sub.pending_billing_term)}</strong> at {money(sub.pending_price_amount, sub.currency)} on renewal</dd></>
              )}
            </dl>
          )}
          {can.manageBilling && sub && !suspended && sub.status !== 'cancelled' && (
            <p style={{ marginBottom: 0 }}><button className="btn small ghost" onClick={() => setDialog('cancel')}>Cancel subscription…</button></p>
          )}
        </section>
      </div>

      <ModulesCard org={org} blocked={org.access === 'blocked'} canEdit={can.manageBilling} onToggle={(m, enable) => setDialog({ module: m, enable })} />

      <InvoicesCard org={org} canIssue={can.manageBilling && !!sub?.current_period_end && sub.status !== 'trialing'} />

      <section className="card">
        <div className="card-head"><h2>History</h2></div>
        {org.recent_events.length === 0 ? <div className="empty">Nothing yet.</div> : (
          <ul className="timeline">
            {org.recent_events.map((e, i) => (
              <li key={i}>
                <span className="muted nowrap">{date(e.at, true)}</span>
                <span><strong>{label(e.type)}</strong>{describeEvent(e.data) && <span className="muted"> — {describeEvent(e.data)}</span>}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {dialog === 'suspend' && <LifecycleDialog org={org} kind="suspend" onClose={close} />}
      {dialog === 'reinstate' && <LifecycleDialog org={org} kind="reinstate" onClose={close} />}
      {dialog === 'cancel' && <CancelDialog org={org} onClose={close} />}
      {dialog === 'activate' && <ActivateDialog org={org} onClose={close} />}
      {dialog === 'extend' && sub && <ExtendDialog org={org} onClose={close} />}
      {dialog === 'change' && sub && <ChangeDialog org={org} onClose={close} />}
      {dialog === 'impersonate' && <ImpersonateDialog org={org} onClose={close} />}
      {dialog === 'profile' && <ProfileDialog org={org} onClose={close} />}
      {dialog && typeof dialog === 'object' && <ModuleDialog org={org} module={dialog.module} enable={dialog.enable} onClose={close} />}
    </>
  );
}

function InvoicesCard({ org, canIssue }: { org: OrgDetail; canIssue: boolean }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const qc = useQueryClient();
  const invoices = useQuery({ queryKey: ['invoices', 'org', org.id], queryFn: () => api.orgInvoices(org.id) });
  const issue = useMutation({
    mutationFn: () => api.issueRenewal(org.id),
    onSuccess: async (inv) => { await refresh(); await qc.invalidateQueries({ queryKey: ['invoices'] }); toast(`Renewal invoice ${inv.number} ready`); },
    onError: (e) => toast((e as Error).message, true),
  });
  return (
    <section className="card">
      <div className="card-head">
        <h2>Invoices</h2>
        {canIssue && <button className="btn small" disabled={issue.isPending} onClick={() => issue.mutate()}>Issue renewal invoice</button>}
      </div>
      <ErrorBox error={invoices.error} />
      {invoices.isLoading ? <Loading /> : <InvoiceTable invoices={invoices.data ?? []} />}
    </section>
  );
}

const effectiveLimit = (v: number | null) => v !== null;

function describeEvent(d: Record<string, unknown>): string {
  const parts: string[] = [];
  if (d.reason) parts.push(String(d.reason));
  if (d.module) parts.push(`${d.module}: ${d.enabled ? 'on' : 'off'}`);
  if (d.to && typeof d.to === 'string' && d.to.length < 20) parts.push(`→ ${d.to}`);
  if (d.days) parts.push(`+${d.days} days`);
  if (d.amount_due) parts.push(`due ${d.amount_due}`);
  return parts.join(' · ');
}

function useRefresh(id: string) {
  const qc = useQueryClient();
  return () => Promise.all([
    qc.invalidateQueries({ queryKey: ['organization', id] }),
    qc.invalidateQueries({ queryKey: ['organizations'] }),
    qc.invalidateQueries({ queryKey: ['dashboard'] }),
  ]);
}

// ------------------------------------------------------------------------------------------------ modules

const SOURCE: Record<string, { text: string; tone: string }> = {
  core: { text: 'Always on', tone: '' },
  plan: { text: 'From plan', tone: 'info' },
  override: { text: 'Forced on', tone: 'good' },
  off: { text: 'Off', tone: '' },
  platform_disabled: { text: 'Disabled platform-wide', tone: 'bad' },
  dependency_missing: { text: 'Missing a required module', tone: 'warn' },
};

function ModulesCard({ org, blocked, canEdit, onToggle }: { org: OrgDetail; blocked: boolean; canEdit: boolean; onToggle: (m: ModuleState, enable: boolean) => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const clear = useMutation({
    mutationFn: (key: string) => api.clearModule(org.id, key, 'Override removed'),
    onSuccess: async () => { await refresh(); toast('Override removed'); },
    onError: (e) => toast((e as Error).message, true),
  });
  return (
    <section className="card">
      <div className="card-head"><h2>Modules</h2></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Module</th><th>State</th><th>Source</th><th>Override</th><th className="right">Enabled</th></tr></thead>
          <tbody>
            {org.modules.map((m) => {
              const src = SOURCE[m.source] ?? { text: label(m.source), tone: '' };
              const locked = m.is_core || m.source === 'platform_disabled' || blocked;
              return (
                <tr key={m.key}>
                  <td><strong>{m.name}</strong><div className="muted mono">{m.key}</div></td>
                  <td>{blocked ? <Badge value="suspended" tone="bad" /> : <Badge value={m.enabled ? 'enabled' : 'disabled'} tone={m.enabled ? 'good' : 'bad'} />}</td>
                  <td>{blocked ? <span className="muted">Organization suspended</span> : <Badge value={src.text} tone={src.tone} />}</td>
                  <td>
                    {m.override ? (
                      <>
                        <div>{m.override.enabled ? 'On' : 'Off'}{m.override.expires_at ? ` until ${date(m.override.expires_at)}` : ''}{!m.override.active && ' (expired)'}</div>
                        <div className="muted">{m.override.reason}</div>
                        {canEdit && <button className="btn small ghost" onClick={() => clear.mutate(m.key)} disabled={clear.isPending}>Remove</button>}
                      </>
                    ) : <span className="muted">—</span>}
                  </td>
                  <td className="right">
                    <Switch label={`${m.name} enabled`} checked={m.enabled && !blocked} disabled={!canEdit || locked} onChange={(v) => onToggle(m, v)} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ModuleDialog({ org, module: m, enable, onClose }: { org: OrgDetail; module: ModuleState; enable: boolean; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const [expires, setExpires] = useState('');
  const run = useMutation({
    mutationFn: (reason: string) => api.setModule(org.id, m.key, { enabled: enable, reason, expires_at: localToIso(expires) }),
    onSuccess: async () => { await refresh(); toast(`${m.name} ${enable ? 'enabled' : 'disabled'}`); onClose(); },
  });
  return (
    <ReasonDialog title={`${enable ? 'Enable' : 'Disable'} ${m.name}`} danger={!enable} confirm={enable ? 'Enable module' : 'Disable module'}
      lead={`Takes effect for ${org.name} immediately${enable ? '' : ' — connected apps will lose access to it'}.`}
      busy={run.isPending} error={run.error} onSubmit={(r) => run.mutate(r)} onClose={onClose}>
      <Field label="Until (optional)" hint="Leave empty for no expiry. After this time the plan decides again." htmlFor="mod-exp">
        <input id="mod-exp" className="input" type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} />
      </Field>
    </ReasonDialog>
  );
}

// ------------------------------------------------------------------------------------------------ lifecycle

function LifecycleDialog({ org, kind, onClose }: { org: OrgDetail; kind: 'suspend' | 'reinstate'; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const run = useMutation({
    mutationFn: (reason: string) => (kind === 'suspend' ? api.suspend(org.id, reason) : api.reinstate(org.id, reason)),
    onSuccess: async () => { await refresh(); toast(kind === 'suspend' ? `${org.name} suspended` : `${org.name} reinstated`); onClose(); },
  });
  return kind === 'suspend' ? (
    <ReasonDialog title={`Suspend ${org.name}?`} danger confirm="Suspend now" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}
      lead="Every staff session is revoked and open connections are closed right away. Nothing works until you reinstate." />
  ) : (
    <ReasonDialog title={`Reinstate ${org.name}?`} confirm="Reinstate" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}
      lead="Staff can sign in again. They will need to log in, because their sessions were revoked." />
  );
}

function CancelDialog({ org, onClose }: { org: OrgDetail; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const [atEnd, setAtEnd] = useState(true);
  const end = org.subscription?.current_period_end;
  const run = useMutation({
    mutationFn: (reason: string) => api.cancel(org.id, reason, atEnd),
    onSuccess: async (r) => {
      await refresh();
      toast(isApproval(r) ? 'Sent for approval: a second super admin must approve the immediate cancellation' : 'Subscription cancelled');
      onClose();
    },
  });
  return (
    <ReasonDialog title="Cancel subscription" danger confirm="Cancel subscription" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}>
      <Segmented name="When" value={atEnd ? 'end' : 'now'} onChange={(v) => setAtEnd(v === 'end')}
        options={[{ value: 'end', label: 'At period end' }, { value: 'now', label: 'Immediately' }]} />
      <p className="muted">
        {atEnd ? `Access continues until ${date(end)}, then becomes read-only. Auto-renew is switched off.` : 'The subscriber becomes read-only right now. Data stays viewable.'}
      </p>
    </ReasonDialog>
  );
}

function ActivateDialog({ org, onClose }: { org: OrgDetail; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const key = useRef(newKey());
  const sub = org.subscription!;
  const run = useMutation({
    mutationFn: (reason: string) => api.activate(org.id, reason, key.current),
    onSuccess: async (r) => { await refresh(); toast(`Paid period started. Invoice ${r.invoice_number}: ${money(r.amount_due, r.currency)} due`); onClose(); },
  });
  return (
    <ReasonDialog title="Start a paid period" confirm="Start now" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}
      lead={`Starts a ${sub.billing_term} period today at ${money(sub.price_amount, sub.currency)}. Record how the payment was received in the reason.`} />
  );
}

function ExtendDialog({ org, onClose }: { org: OrgDetail; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const key = useRef(newKey());
  const [days, setDays] = useState('7');
  const run = useMutation({
    mutationFn: (reason: string) => api.extend(org.id, { days: Number(days), reason, version: org.subscription!.version }, key.current),
    onSuccess: async () => { await refresh(); toast(`Extended by ${days} days`); onClose(); },
  });
  return (
    <ReasonDialog title="Extend current period" confirm="Extend" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}
      lead={`Adds days to the period ending ${date(org.subscription!.current_period_end)}. This also restores access if the subscription had lapsed.`}>
      <Field label="Days to add" htmlFor="ext-days">
        <input id="ext-days" className="input" type="number" min={1} max={366} value={days} onChange={(e) => setDays(e.target.value)} required />
      </Field>
    </ReasonDialog>
  );
}

function ProfileDialog({ org, onClose }: { org: OrgDetail; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const [f, setF] = useState({ name: org.name, legal_name: org.legal_name ?? '', billing_email: org.billing_email ?? '', country: org.country ?? '' });
  const run = useMutation({
    mutationFn: (reason: string) => api.updateOrganization(org.id, {
      name: f.name.trim(), legal_name: f.legal_name.trim() || null, billing_email: f.billing_email.trim() || null,
      country: f.country.trim().toUpperCase() || null, reason,
    }),
    onSuccess: async () => { await refresh(); toast('Saved'); onClose(); },
  });
  return (
    <ReasonDialog title="Edit business details" confirm="Save" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}>
      <Field label="Name" htmlFor="p-name"><input id="p-name" className="input" required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="Legal name" htmlFor="p-legal"><input id="p-legal" className="input" value={f.legal_name} onChange={(e) => setF({ ...f, legal_name: e.target.value })} /></Field>
      <div className="row">
        <Field label="Billing email" htmlFor="p-mail"><input id="p-mail" className="input" type="email" value={f.billing_email} onChange={(e) => setF({ ...f, billing_email: e.target.value })} /></Field>
        <Field label="Country" htmlFor="p-cty"><input id="p-cty" className="input" maxLength={2} pattern="[A-Za-z]{2}" value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })} /></Field>
      </div>
    </ReasonDialog>
  );
}

// ------------------------------------------------------------------------------------------------ plan / term change

function ChangeDialog({ org, onClose }: { org: OrgDetail; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh(org.id);
  const key = useRef(newKey());
  const sub = org.subscription!;
  const plans = useQuery({ queryKey: ['plans'], queryFn: api.plans });
  const [planId, setPlanId] = useState(sub.plan_id);
  const [term, setTerm] = useState<BillingTerm>(sub.billing_term);
  const [price, setPrice] = useState('');
  const [effective, setEffective] = useState<Effective>('now');
  const [reason, setReason] = useState('');

  const planChanged = planId !== sub.plan_id;
  const changed = planChanged || term !== sub.billing_term || price !== '';
  const body = { plan_id: planChanged ? planId : undefined, billing_term: term !== sub.billing_term ? term : undefined,
    price_amount: price !== '' ? price : undefined, effective: planChanged ? 'now' : effective };

  const preview = useQuery({
    queryKey: ['preview', org.id, body],
    queryFn: () => api.previewChange(org.id, body),
    enabled: changed,
    retry: false,
  });
  const apply = useMutation({
    mutationFn: () => api.updateSubscription(org.id, { ...body, reason: reason.trim(), version: sub.version }, key.current),
    onSuccess: async (r) => {
      await refresh();
      toast(r.invoice_number ? `Updated. Invoice ${r.invoice_number} for ${money(r.change?.amount_due_now, sub.currency)}` : 'Subscription updated');
      onClose();
    },
  });
  const plan = plans.data?.find((p) => p.id === planId);
  const listPrice = term === 'monthly' ? plan?.price_monthly : plan?.price_yearly;

  return (
    <Modal title="Change plan or billing term" lead={`${org.name} is on ${sub.plan_code}, billed ${sub.billing_term}.`} onClose={onClose} wide>
      <form onSubmit={(e) => { e.preventDefault(); apply.mutate(); }}>
        <ErrorBox error={apply.error} />
        <div className="row">
          <div className="field">
            <span className="label">Billing term</span>
            <Segmented name="Billing term" value={term} onChange={setTerm} options={[{ value: 'monthly', label: 'Monthly' }, { value: 'yearly', label: 'Yearly' }]} />
          </div>
          <Field label="Plan" htmlFor="ch-plan">
            <select id="ch-plan" value={planId} onChange={(e) => setPlanId(e.target.value)}>
              {(plans.data ?? []).filter((p: Plan) => p.is_active || p.id === sub.plan_id).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Custom price (optional)" htmlFor="ch-price"
          hint={listPrice ? `List price for ${term}: ${money(listPrice, plan?.currency)}. Leave empty to use it.` : 'No list price for this term — enter one.'}>
          <input id="ch-price" className="input" type="number" min={0} step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} />
        </Field>
        <div className="field">
          <span className="label">When</span>
          <Segmented name="When" value={planChanged ? 'now' : effective} onChange={(v) => !planChanged && setEffective(v)}
            options={[{ value: 'now', label: 'Now (prorated)' }, { value: 'next_renewal', label: 'At next renewal' }]} />
          {planChanged && <span className="hint">Plan changes always apply immediately.</span>}
        </div>

        {changed && <PreviewPanel preview={preview.data} loading={preview.isFetching} error={preview.error} currency={sub.currency} />}

        <Field label="Reason" hint="Recorded in the audit log and the subscriber's history." htmlFor="ch-reason">
          <textarea id="ch-reason" value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={500} />
        </Field>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!changed || reason.trim().length < 3 || apply.isPending || preview.isError}>
            {apply.isPending ? 'Applying…' : 'Apply change'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function PreviewPanel({ preview, loading, error, currency }: { preview?: ChangePreview; loading: boolean; error: unknown; currency: string }) {
  if (error) return <ErrorBox error={error} />;
  if (!preview) return <div className="alert info"><span className="spinner" /> Calculating…</div>;
  const now = preview.effective === 'now';
  return (
    <div className="alert info" style={{ opacity: loading ? 0.6 : 1 }} aria-live="polite">
      <strong>{label(preview.current_term)} → {label(preview.new_term)}</strong>
      <dl className="kv" style={{ marginTop: 8, gridTemplateColumns: '210px 1fr' }}>
        <dt>New price</dt><dd>{money(preview.new_price, currency)} / {preview.new_term === 'monthly' ? 'month' : 'year'}</dd>
        {now ? (
          <>
            <dt>Credit for unused time</dt><dd>{money(preview.credit_for_unused_time, currency)}</dd>
            <dt>Amount due now</dt><dd><strong>{money(preview.amount_due_now, currency)}</strong></dd>
            {Number(preview.account_credit_remaining) > 0 && <><dt>Credit left over</dt><dd>{money(preview.account_credit_remaining, currency)}</dd></>}
            <dt>New period</dt><dd>{date(preview.new_period_start)} → {date(preview.new_period_end)}</dd>
          </>
        ) : (
          <>
            <dt>Takes effect</dt><dd>{date(preview.effective_at)} (next renewal)</dd>
            <dt>Charged then</dt><dd>{money(preview.new_price, currency)}. Nothing changes today.</dd>
          </>
        )}
      </dl>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ impersonation

function ImpersonateDialog({ org, onClose }: { org: OrgDetail; onClose: () => void }) {
  const [minutes, setMinutes] = useState('15');
  const run = useMutation({ mutationFn: (reason: string) => api.impersonate(org.id, { reason, minutes: Number(minutes) }) });

  if (run.data) {
    return (
      <Modal title="Read-only access token" onClose={onClose}
        lead={`Signed in as ${run.data.username} for ${Math.round(run.data.expires_in / 60)} minutes. It can read but never change anything.`}>
        <div className="alert warn">Treat this like a password. It is shown once and is recorded in the audit log.</div>
        <div className="secret-box" aria-label="Access token">{run.data.access_token}</div>
        <div className="foot"><CopyButton text={run.data.access_token} label="Copy token" /><button className="btn primary" onClick={onClose}>Done</button></div>
      </Modal>
    );
  }
  return (
    <ReasonDialog title="View as the owner" confirm="Get read-only token" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}
      lead="For support: see exactly what the subscriber sees. Every use is audited. Include the ticket number in the reason.">
      <Field label="Minutes" hint="Up to 30" htmlFor="imp-min">
        <input id="imp-min" className="input" type="number" min={1} max={30} value={minutes} onChange={(e) => setMinutes(e.target.value)} required />
      </Field>
    </ReasonDialog>
  );
}
