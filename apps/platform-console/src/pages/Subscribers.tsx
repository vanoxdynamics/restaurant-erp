import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, newKey, type BillingTerm, type Plan } from '../api';
import { useCan } from '../auth';
import { date, label, money, relativeDays } from '../format';
import { Badge, ErrorBox, Field, Loading, Modal, PageHead, Pager, Segmented, useDebounced, useToast } from '../ui';

const PAGE = 25;

export function SubscribersPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const can = useCan();
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState(params.get('q') ?? '');
  const q = useDebounced(search);
  const offset = Number(params.get('offset') ?? 0);
  const status = params.get('status') ?? '';
  const term = params.get('term') ?? '';
  const subStatus = params.get('subscription_status') ?? '';
  const planId = params.get('plan_id') ?? '';
  const expiring = params.get('expiring_within_days') ?? '';

  const plans = useQuery({ queryKey: ['plans'], queryFn: api.plans });
  const list = useQuery({
    queryKey: ['organizations', { q, status, term, subStatus, planId, expiring, offset }],
    queryFn: () => api.organizations({
      q: q || undefined, status: status || undefined, term: term || undefined, subscription_status: subStatus || undefined,
      plan_id: planId || undefined, expiring_within_days: expiring || undefined, limit: PAGE, offset,
    }),
    placeholderData: (prev) => prev,
  });

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    next.delete('offset');
    setParams(next, { replace: true });
  }

  return (
    <>
      <PageHead title="Subscribers" sub="Every restaurant business on the platform.">
        {can.manageBilling && <button className="btn primary" onClick={() => setCreating(true)}>New subscriber</button>}
      </PageHead>

      <div className="filters" role="search">
        <input className="input grow" type="search" placeholder="Search name, slug or billing email" aria-label="Search subscribers"
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <select aria-label="Status" value={status} onChange={(e) => setFilter('status', e.target.value)}>
          <option value="">Any status</option>
          {['trial', 'active', 'past_due', 'suspended', 'cancelled'].map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
        <select aria-label="Billing term" value={term} onChange={(e) => setFilter('term', e.target.value)}>
          <option value="">Any term</option>
          <option value="monthly">Monthly</option>
          <option value="yearly">Yearly</option>
        </select>
        <select aria-label="Plan" value={planId} onChange={(e) => setFilter('plan_id', e.target.value)}>
          <option value="">Any plan</option>
          {plans.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select aria-label="Renewal window" value={expiring} onChange={(e) => setFilter('expiring_within_days', e.target.value)}>
          <option value="">Any renewal date</option>
          <option value="7">Renews within 7 days</option>
          <option value="30">Renews within 30 days</option>
        </select>
      </div>

      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Subscriber</th><th>Status</th><th>Plan</th><th>Term</th><th>Subscription</th><th>Period ends</th></tr></thead>
              <tbody>
                {list.data?.items.length === 0 && <tr><td colSpan={6}><div className="empty">No subscribers match.</div></td></tr>}
                {list.data?.items.map((o) => (
                  <tr key={o.id} className="link" onClick={() => navigate(`/subscribers/${o.id}`)}>
                    <td>
                      <Link to={`/subscribers/${o.id}`} onClick={(e) => e.stopPropagation()}><strong>{o.name}</strong></Link>
                      <div className="muted">{o.slug}{o.billing_email ? ` · ${o.billing_email}` : ''}</div>
                    </td>
                    <td><Badge value={o.status} /></td>
                    <td>{o.plan_code ?? '—'}</td>
                    <td>{o.billing_term ? label(o.billing_term) : '—'}</td>
                    <td><Badge value={o.subscription_status} /></td>
                    <td className="nowrap">{date(o.current_period_end)} <span className="muted">{relativeDays(o.current_period_end)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={list.data?.total ?? 0} limit={PAGE} offset={offset} onChange={(o) => {
            const next = new URLSearchParams(params);
            if (o) next.set('offset', String(o)); else next.delete('offset');
            setParams(next);
          }} />
        </>
      )}
      {creating && plans.data && <CreateSubscriber plans={plans.data.filter((p) => p.is_active)} onClose={() => setCreating(false)} />}
    </>
  );
}

function CreateSubscriber({ plans, onClose }: { plans: Plan[]; onClose: () => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const key = useRef(newKey()); // a retry of the same form reuses the key, so it can never create two subscribers
  const [f, setF] = useState({
    name: '', slug: '', legal_name: '', billing_email: '', country: 'IN', plan_id: plans[0]?.id ?? '',
    billing_term: 'monthly' as BillingTerm, start: 'trial' as 'trial' | 'active', trial_days: '', price_amount: '',
    username: '', full_name: '', password: '', pin: '', branch_code: '', branch_name: 'Main Branch',
  });
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }));
  const plan = plans.find((p) => p.id === f.plan_id);
  const listPrice = f.billing_term === 'monthly' ? plan?.price_monthly : plan?.price_yearly;

  const create = useMutation({
    mutationFn: () => api.createOrganization({
      name: f.name.trim(), slug: f.slug.trim(), legal_name: f.legal_name.trim() || undefined, billing_email: f.billing_email.trim() || undefined,
      country: f.country.trim().toUpperCase() || undefined, plan_id: f.plan_id, billing_term: f.billing_term, start: f.start,
      trial_days: f.start === 'trial' && f.trial_days ? Number(f.trial_days) : undefined, price_amount: f.price_amount || undefined,
      owner: { username: f.username.trim(), full_name: f.full_name.trim(), password: f.password, pin: f.pin,
        branch_code: f.branch_code.trim().toUpperCase(), branch_name: f.branch_name.trim() },
    }, key.current),
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: ['organizations'] });
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      toast(res.invoice_number ? `Created. Invoice ${res.invoice_number}: ${money(res.amount_due)} due` : 'Subscriber created');
      navigate(`/subscribers/${res.organization.id}`);
    },
  });

  return (
    <Modal title="New subscriber" lead="Creates the business, its first branch and the owner login. Share the owner's credentials over a secure channel." onClose={onClose} wide>
      <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <ErrorBox error={create.error} />
        <h3 style={{ margin: '4px 0 10px' }}>Business</h3>
        <div className="row">
          <Field label="Name" htmlFor="c-name"><input id="c-name" className="input" required value={f.name} maxLength={150}
            onChange={(e) => { set('name', e.target.value); if (!f.slug || f.slug === slugify(f.name)) set('slug', slugify(e.target.value)); }} /></Field>
          <Field label="Slug" hint="Lowercase letters, digits, dashes" htmlFor="c-slug"><input id="c-slug" className="input" required minLength={3} maxLength={60}
            pattern="[a-z0-9][a-z0-9\-]{1,58}[a-z0-9]" value={f.slug} onChange={(e) => set('slug', e.target.value)} /></Field>
        </div>
        <div className="row">
          <Field label="Billing email" htmlFor="c-email"><input id="c-email" className="input" type="email" value={f.billing_email} onChange={(e) => set('billing_email', e.target.value)} /></Field>
          <Field label="Country" hint="2-letter code" htmlFor="c-country"><input id="c-country" className="input" maxLength={2} pattern="[A-Za-z]{2}" value={f.country} onChange={(e) => set('country', e.target.value)} /></Field>
        </div>

        <h3 style={{ margin: '8px 0 10px' }}>Plan & billing</h3>
        <div className="row">
          <Field label="Plan" htmlFor="c-plan">
            <select id="c-plan" value={f.plan_id} onChange={(e) => set('plan_id', e.target.value)} required>
              {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <div className="field">
            <span className="label">Billing term</span>
            <Segmented name="Billing term" value={f.billing_term} onChange={(v) => set('billing_term', v)} options={[{ value: 'monthly', label: 'Monthly' }, { value: 'yearly', label: 'Yearly' }]} />
          </div>
        </div>
        <div className="row">
          <div className="field">
            <span className="label">Start</span>
            <Segmented name="Start" value={f.start} onChange={(v) => set('start', v)} options={[{ value: 'trial', label: 'Free trial' }, { value: 'active', label: 'Paid now' }]} />
          </div>
          {f.start === 'trial' && <Field label="Trial days" hint="Default 14" htmlFor="c-trial"><input id="c-trial" className="input" type="number" min={1} max={90} value={f.trial_days} onChange={(e) => set('trial_days', e.target.value)} /></Field>}
          <Field label="Custom price" hint={listPrice ? `Leave empty for the list price (${money(listPrice, plan?.currency)})` : 'This plan has no list price for the term — enter one'} htmlFor="c-price">
            <input id="c-price" className="input" type="number" min={0} step="0.01" value={f.price_amount} onChange={(e) => set('price_amount', e.target.value)} required={!listPrice} />
          </Field>
        </div>

        <h3 style={{ margin: '8px 0 10px' }}>First branch & owner</h3>
        <div className="row">
          <Field label="Branch code" hint="2–6 letters or digits" htmlFor="c-bcode"><input id="c-bcode" className="input" required pattern="[A-Za-z0-9]{2,6}" maxLength={6} value={f.branch_code} onChange={(e) => set('branch_code', e.target.value)} /></Field>
          <Field label="Branch name" htmlFor="c-bname"><input id="c-bname" className="input" required minLength={2} value={f.branch_name} onChange={(e) => set('branch_name', e.target.value)} /></Field>
        </div>
        <div className="row">
          <Field label="Owner's full name" htmlFor="c-oname"><input id="c-oname" className="input" required minLength={2} value={f.full_name} onChange={(e) => set('full_name', e.target.value)} /></Field>
          <Field label="Owner username" hint="Lowercase letters, digits, . _ -" htmlFor="c-ouser"><input id="c-ouser" className="input" required minLength={3} pattern="[a-z0-9._\-]+" value={f.username} onChange={(e) => set('username', e.target.value)} autoComplete="off" /></Field>
        </div>
        <div className="row">
          <Field label="Initial password" hint="At least 10 characters" htmlFor="c-opass"><input id="c-opass" className="input" type="password" required minLength={10} value={f.password} onChange={(e) => set('password', e.target.value)} autoComplete="new-password" /></Field>
          <Field label="Quick-login PIN" hint="4–6 digits" htmlFor="c-opin"><input id="c-opin" className="input" inputMode="numeric" required pattern="\d{4,6}" value={f.pin} onChange={(e) => set('pin', e.target.value)} autoComplete="off" /></Field>
        </div>

        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create subscriber'}</button>
        </div>
      </form>
    </Modal>
  );
}

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
}
