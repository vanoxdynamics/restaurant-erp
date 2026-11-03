import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type GlobalModule, type Plan } from '../api';
import { useCan } from '../auth';
import { money } from '../format';
import { Badge, ErrorBox, Field, Loading, Modal, PageHead, useToast } from '../ui';

export function PlansPage() {
  const can = useCan();
  const plans = useQuery({ queryKey: ['plans'], queryFn: api.plans });
  const modules = useQuery({ queryKey: ['modules'], queryFn: api.modules });
  const [editing, setEditing] = useState<Plan | 'new' | null>(null);
  if (plans.isLoading || modules.isLoading) return <Loading />;

  return (
    <>
      <PageHead title="Plans" sub="Bundles of modules, limits and prices that subscribers are placed on.">
        {can.manageBilling && <button className="btn primary" onClick={() => setEditing('new')}>New plan</button>}
      </PageHead>
      <ErrorBox error={plans.error ?? modules.error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Plan</th><th>Monthly</th><th>Yearly</th><th>Limits</th><th>Modules</th><th>Subscribers</th><th /></tr></thead>
          <tbody>
            {plans.data?.length === 0 && <tr><td colSpan={7}><div className="empty">No plans yet. Create one before onboarding subscribers.</div></td></tr>}
            {plans.data?.map((p) => (
              <tr key={p.id} className={p.is_active ? '' : 'dim-row'}>
                <td><strong>{p.name}</strong> {!p.is_active && <Badge value="retired" />}<div className="muted mono">{p.code}</div></td>
                <td>{money(p.price_monthly, p.currency)}</td>
                <td>{money(p.price_yearly, p.currency)}</td>
                <td>{p.max_branches ?? '∞'} branches · {p.max_staff ?? '∞'} staff</td>
                <td>{p.module_keys.map((k) => <Badge key={k} value={k} tone="info" />)}</td>
                <td>{p.subscribers}</td>
                <td className="right">{can.manageBilling && <button className="btn small" onClick={() => setEditing(p)}>Edit</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && modules.data && <PlanDialog plan={editing === 'new' ? null : editing} modules={modules.data} onClose={() => setEditing(null)} />}
    </>
  );
}

function PlanDialog({ plan, modules, onClose }: { plan: Plan | null; modules: GlobalModule[]; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({
    code: plan?.code ?? '', name: plan?.name ?? '', currency: plan?.currency ?? 'INR',
    price_monthly: plan?.price_monthly ?? '', price_yearly: plan?.price_yearly ?? '',
    max_branches: plan?.max_branches?.toString() ?? '', max_staff: plan?.max_staff?.toString() ?? '',
    is_active: plan?.is_active ?? true, reason: '',
  });
  const [keys, setKeys] = useState<string[]>(plan?.module_keys ?? ['pos']);
  const set = (k: keyof typeof f, v: string | boolean) => setF((s) => ({ ...s, [k]: v }));
  const toggle = (key: string) => setKeys((ks) => (ks.includes(key) ? ks.filter((k) => k !== key) : [...ks, key]));

  const save = useMutation({
    mutationFn: () => {
      const common = {
        name: f.name.trim(), price_monthly: f.price_monthly || null, price_yearly: f.price_yearly || null,
        max_branches: f.max_branches ? Number(f.max_branches) : null, max_staff: f.max_staff ? Number(f.max_staff) : null, module_keys: keys,
      };
      return plan
        ? api.updatePlan(plan.id, { ...common, is_active: f.is_active, reason: f.reason.trim() })
        : api.createPlan({ ...common, code: f.code.trim(), currency: f.currency.trim().toUpperCase() });
    },
    onSuccess: async () => {
      await Promise.all([qc.invalidateQueries({ queryKey: ['plans'] }), qc.invalidateQueries({ queryKey: ['organization'] })]);
      toast(plan ? 'Plan updated' : 'Plan created');
      onClose();
    },
  });

  return (
    <Modal title={plan ? `Edit ${plan.name}` : 'New plan'} onClose={onClose} wide
      lead={plan && plan.subscribers > 0 ? `${plan.subscribers} subscriber${plan.subscribers === 1 ? '' : 's'} will see module and limit changes immediately. Price changes apply from their next renewal.` : undefined}>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <ErrorBox error={save.error} />
        <div className="row">
          <Field label="Name" htmlFor="pl-name"><input id="pl-name" className="input" required minLength={2} value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
          {!plan && <Field label="Code" hint="Lowercase, digits, underscore. Cannot change later." htmlFor="pl-code">
            <input id="pl-code" className="input mono" required pattern="[a-z][a-z0-9_]{2,39}" value={f.code} onChange={(e) => set('code', e.target.value)} /></Field>}
          {!plan && <Field label="Currency" htmlFor="pl-cur"><input id="pl-cur" className="input" required pattern="[A-Za-z]{3}" maxLength={3} value={f.currency} onChange={(e) => set('currency', e.target.value)} /></Field>}
        </div>
        <div className="row">
          <Field label="Monthly price" htmlFor="pl-pm"><input id="pl-pm" className="input" type="number" min={0} step="0.01" value={f.price_monthly} onChange={(e) => set('price_monthly', e.target.value)} /></Field>
          <Field label="Yearly price" htmlFor="pl-py"><input id="pl-py" className="input" type="number" min={0} step="0.01" value={f.price_yearly} onChange={(e) => set('price_yearly', e.target.value)} /></Field>
        </div>
        <div className="row">
          <Field label="Max branches" hint="Empty = unlimited" htmlFor="pl-mb"><input id="pl-mb" className="input" type="number" min={1} value={f.max_branches} onChange={(e) => set('max_branches', e.target.value)} /></Field>
          <Field label="Max staff accounts" hint="Empty = unlimited" htmlFor="pl-ms"><input id="pl-ms" className="input" type="number" min={1} value={f.max_staff} onChange={(e) => set('max_staff', e.target.value)} /></Field>
        </div>
        <div className="field">
          <span className="label">Modules</span>
          {modules.map((m) => (
            <label key={m.key} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input type="checkbox" checked={m.is_core || keys.includes(m.key)} disabled={m.is_core} onChange={() => toggle(m.key)} />
              <span><strong>{m.name}</strong> <span className="muted">— {m.description}{m.is_core ? ' (always included)' : ''}</span></span>
            </label>
          ))}
        </div>
        {plan && (
          <>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 }}>
              <input type="checkbox" checked={f.is_active} onChange={(e) => set('is_active', e.target.checked)} /> Available for new subscribers
            </label>
            <Field label="Reason" hint="Recorded in the audit log" htmlFor="pl-reason"><textarea id="pl-reason" required minLength={3} maxLength={500} value={f.reason} onChange={(e) => set('reason', e.target.value)} /></Field>
          </>
        )}
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save plan'}</button>
        </div>
      </form>
    </Modal>
  );
}
