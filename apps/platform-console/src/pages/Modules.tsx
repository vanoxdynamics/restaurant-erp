import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, isApproval, type GlobalModule } from '../api';
import { useCan } from '../auth';
import { Badge, ErrorBox, Field, Loading, PageHead, ReasonDialog, Segmented, Switch, useToast } from '../ui';

export function ModulesPage() {
  const can = useCan();
  const modules = useQuery({ queryKey: ['modules'], queryFn: api.modules });
  const [switching, setSwitching] = useState<{ module: GlobalModule; enable: boolean } | null>(null);
  if (modules.isLoading) return <Loading />;

  return (
    <>
      <PageHead title="Modules" sub="Switch a module off for every subscriber at once, or apply a change to many subscribers in bulk." />
      <ErrorBox error={modules.error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Module</th><th>Needs</th><th>Subscribers with access</th><th>Platform-wide</th><th className="right">Enabled</th></tr></thead>
          <tbody>
            {modules.data?.map((m) => (
              <tr key={m.key}>
                <td><strong>{m.name}</strong><div className="muted">{m.description}</div></td>
                <td>{m.depends_on.length ? m.depends_on.map((d) => <Badge key={d} value={d} tone="info" />) : <span className="muted">—</span>}</td>
                <td>{m.organizations_with_access}</td>
                <td>{m.is_core ? <Badge value="always on" /> : m.globally_enabled ? <Badge value="on" tone="good" /> : <><Badge value="off" tone="bad" /><div className="muted">{m.reason}</div></>}</td>
                <td className="right">
                  <Switch label={`${m.name} platform-wide`} checked={m.globally_enabled} disabled={!can.superAdmin || m.is_core} onChange={(v) => setSwitching({ module: m, enable: v })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!can.superAdmin && <p className="muted">Only super admins can use the platform-wide switch.</p>}
      {can.manageBilling && modules.data && <BulkCard modules={modules.data.filter((m) => !m.is_core)} />}
      {switching && <KillSwitchDialog {...switching} onClose={() => setSwitching(null)} />}
    </>
  );
}

function KillSwitchDialog({ module: m, enable, onClose }: { module: GlobalModule; enable: boolean; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const run = useMutation({
    mutationFn: (reason: string) => api.switchModule(m.key, { enabled: enable, reason }),
    onSuccess: async (r) => {
      await qc.invalidateQueries();
      toast(isApproval(r) ? 'Sent for approval: a second super admin must approve it before it takes effect' : `${m.name} ${enable ? 'switched on' : 'switched off'} platform-wide`);
      onClose();
    },
  });
  return (
    <ReasonDialog title={`${enable ? 'Switch on' : 'Switch off'} ${m.name} for everyone?`} danger={!enable} confirm={enable ? 'Switch on' : 'Switch off for everyone'}
      lead={enable ? 'Subscribers whose plan or override includes it get it back immediately.'
        : `${m.organizations_with_access} subscriber${m.organizations_with_access === 1 ? '' : 's'} lose${m.organizations_with_access === 1 ? 's' : ''} access right now, whatever their plan or overrides say.`}
      busy={run.isPending} error={run.error} onSubmit={(r) => run.mutate(r)} onClose={onClose} />
  );
}

function BulkCard({ modules }: { modules: GlobalModule[] }) {
  const toast = useToast();
  const qc = useQueryClient();
  const plans = useQuery({ queryKey: ['plans'], queryFn: api.plans });
  const orgs = useQuery({ queryKey: ['organizations', 'names'], queryFn: () => api.organizations({ limit: 200 }) });
  const [moduleKey, setModuleKey] = useState(modules[0]?.key ?? '');
  const [enabled, setEnabled] = useState<'on' | 'off'>('on');
  const [planId, setPlanId] = useState('');
  const [reason, setReason] = useState('');
  const [confirm, setConfirm] = useState(false);

  const body = { module_key: moduleKey, enabled: enabled === 'on', reason: reason.trim(), plan_id: planId };
  const dry = useMutation({
    mutationFn: async () => {
      const r = await api.bulkModules({ ...body, dry_run: true });
      if (isApproval(r)) throw new Error('Unexpected approval request for a preview');
      return r; // a preview never needs approval
    },
  });
  const apply = useMutation({
    mutationFn: () => api.bulkModules({ ...body, dry_run: false }),
    onSuccess: async (r) => {
      await qc.invalidateQueries();
      toast(isApproval(r) ? 'Sent for approval: a second super admin must approve it before anything changes' : `Updated ${r.changed.length} subscriber${r.changed.length === 1 ? '' : 's'}`);
      dry.reset();
      setConfirm(false);
    },
  });
  const names = new Map(orgs.data?.items.map((o) => [o.id, o.name]));
  const ready = !!moduleKey && !!planId && reason.trim().length >= 3;

  if (modules.length === 0) return null;
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div className="card-head"><h2>Bulk change</h2></div>
      <p className="muted" style={{ marginTop: 0 }}>Preview first. Nothing changes until you apply.</p>
      <ErrorBox error={dry.error ?? apply.error} />
      <div className="row">
        <Field label="Module" htmlFor="bk-mod">
          <select id="bk-mod" value={moduleKey} onChange={(e) => { setModuleKey(e.target.value); dry.reset(); }}>{modules.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}</select>
        </Field>
        <div className="field"><span className="label">Set to</span>
          <Segmented name="Set to" value={enabled} onChange={(v) => { setEnabled(v); dry.reset(); }} options={[{ value: 'on', label: 'On' }, { value: 'off', label: 'Off' }]} /></div>
        <Field label="Everyone on plan" htmlFor="bk-plan">
          <select id="bk-plan" value={planId} onChange={(e) => { setPlanId(e.target.value); dry.reset(); }}>
            <option value="">Choose a plan…</option>
            {plans.data?.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.subscribers})</option>)}
          </select>
        </Field>
      </div>
      <Field label="Reason" htmlFor="bk-reason"><input id="bk-reason" className="input" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} /></Field>
      <button className="btn" disabled={!ready || dry.isPending} onClick={() => dry.mutate()}>{dry.isPending ? 'Checking…' : 'Preview changes'}</button>

      {dry.data && (
        <div style={{ marginTop: 14 }}>
          <div className="alert info" role="status">
            <strong>{dry.data.changed.length}</strong> would change · {dry.data.unchanged} already {enabled === 'on' ? 'have' : 'lack'} it · {dry.data.blocked.length} blocked by dependencies
          </div>
          {dry.data.changed.length > 0 && (
            <ul>{dry.data.changed.map((c) => (
              <li key={c.organization_id}><Link to={`/subscribers/${c.organization_id}`}>{names.get(c.organization_id) ?? c.organization_id}</Link>{' '}
                <span className="diff-no">{String(c.before)}</span> → <span className="diff-yes">{String(c.after)}</span></li>
            ))}</ul>
          )}
          {dry.data.changed.length > 0 && !confirm && <button className="btn primary" onClick={() => setConfirm(true)}>Apply to {dry.data.changed.length}</button>}
          {confirm && (
            <div className="alert warn">
              This will change {dry.data.changed.length} subscriber{dry.data.changed.length === 1 ? '' : 's'} immediately.{' '}
              <button className="btn small danger" disabled={apply.isPending} onClick={() => apply.mutate()}>{apply.isPending ? 'Applying…' : 'Yes, apply now'}</button>{' '}
              <button className="btn small" onClick={() => setConfirm(false)}>Back</button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
