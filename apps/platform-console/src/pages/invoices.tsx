import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Fragment, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, newKey, type Invoice, type PaymentMethod } from '../api';
import { useCan } from '../auth';
import { date, label, localToIso, money } from '../format';
import { Badge, ErrorBox, Field, Modal, ReasonDialog, useToast } from '../ui';

const METHODS: Array<{ value: PaymentMethod; label: string }> = [
  { value: 'bank_transfer', label: 'Bank transfer' },
  { value: 'upi', label: 'UPI' },
  { value: 'card_offline', label: 'Card (offline)' },
  { value: 'cash', label: 'Cash' },
  { value: 'cheque', label: 'Cheque' },
  { value: 'other', label: 'Other' },
];

const KIND: Record<Invoice['kind'], string> = { renewal: 'Renewal', activation: 'Subscription', plan_change: 'Plan change', manual: 'Manual' };

function invoiceTone(i: Invoice): string {
  if (i.status === 'paid') return 'good';
  if (i.status === 'void') return '';
  return i.overdue ? 'bad' : 'warn';
}

function useRefreshBilling() {
  const qc = useQueryClient();
  return () => Promise.all([
    qc.invalidateQueries({ queryKey: ['invoices'] }),
    qc.invalidateQueries({ queryKey: ['organization'] }),
    qc.invalidateQueries({ queryKey: ['organizations'] }),
    qc.invalidateQueries({ queryKey: ['dashboard'] }),
  ]);
}

/** Invoices with their payments. `showOrg` adds the subscriber column (the global billing page). */
export function InvoiceTable({ invoices, showOrg }: { invoices: Invoice[]; showOrg?: boolean }) {
  const can = useCan();
  const [paying, setPaying] = useState<Invoice | null>(null);
  const [voiding, setVoiding] = useState<Invoice | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  if (invoices.length === 0) return <div className="empty">No invoices.</div>;
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Invoice</th>{showOrg && <th>Subscriber</th>}<th>Covers</th><th className="right">Amount</th><th className="right">To pay</th><th>Due</th><th>Status</th><th /></tr>
          </thead>
          <tbody>
            {invoices.map((i) => (
              <Fragment key={i.id}>
                <tr className={i.status === 'void' ? 'dim-row' : ''}>
                  <td>
                    <button className="btn ghost small" style={{ padding: 0 }} aria-expanded={open === i.id} onClick={() => setOpen(open === i.id ? null : i.id)}>
                      <strong>{i.number}</strong>
                    </button>
                    <div className="muted">{KIND[i.kind]} · {label(i.billing_term)}</div>
                  </td>
                  {showOrg && <td><Link to={`/subscribers/${i.organization_id}`}>{i.organization_name}</Link></td>}
                  <td className="nowrap">{date(i.period_start)} → {date(i.period_end)}</td>
                  <td className="right">{money(i.amount, i.currency)}{Number(i.credit_applied) > 0 && <div className="muted">credit −{money(i.credit_applied, i.currency)}</div>}</td>
                  <td className="right"><strong>{i.status === 'open' ? money(i.balance, i.currency) : '—'}</strong></td>
                  <td className="nowrap">{date(i.due_at)}</td>
                  <td><Badge value={i.overdue ? 'overdue' : i.status} tone={invoiceTone(i)} /></td>
                  <td className="right nowrap">
                    {can.manageBilling && i.status === 'open' && (
                      <span className="actions" style={{ justifyContent: 'flex-end' }}>
                        <button className="btn small primary" onClick={() => setPaying(i)}>Record payment</button>
                        {i.payments.length === 0 && <button className="btn small ghost" onClick={() => setVoiding(i)}>Void</button>}
                      </span>
                    )}
                  </td>
                </tr>
                {open === i.id && (
                  <tr><td colSpan={showOrg ? 8 : 7}>
                    <div className="muted">{i.description}{i.status === 'void' && ` — voided: ${i.void_reason}`}</div>
                    {i.payments.length === 0 ? <div className="muted">No payments yet.</div> : (
                      <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                        {i.payments.map((p) => (
                          <li key={p.id}>{money(p.amount, p.currency)} by {label(p.method)} on {date(p.received_at)}{p.reference && <> · <code>{p.reference}</code></>}{p.note && <span className="muted"> — {p.note}</span>}</li>
                        ))}
                      </ul>
                    )}
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {paying && <PaymentDialog invoice={paying} onClose={() => setPaying(null)} />}
      {voiding && <VoidDialog invoice={voiding} onClose={() => setVoiding(null)} />}
    </>
  );
}

function PaymentDialog({ invoice, onClose }: { invoice: Invoice; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefreshBilling();
  const key = useRef(newKey()); // a retry of this same dialog can never record the money twice
  const nowLocal = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const [f, setF] = useState({ amount: invoice.balance, method: 'bank_transfer' as PaymentMethod, reference: '', received: nowLocal(), note: '' });
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }));
  const amount = Number(f.amount);
  const over = amount > Number(invoice.balance);

  const run = useMutation({
    mutationFn: () => api.recordPayment(invoice.id, {
      amount: f.amount, method: f.method, reference: f.reference.trim() || undefined, received_at: localToIso(f.received), note: f.note.trim() || undefined,
    }, key.current),
    onSuccess: async (r) => {
      await refresh();
      toast(r.renewed ? `Paid. Subscription renewed until ${date(r.invoice.period_end)}` : r.settled ? 'Invoice paid' : `Recorded. ${money(r.invoice.balance, r.invoice.currency)} still to pay`);
      onClose();
    },
  });

  return (
    <Modal title={`Record payment for ${invoice.number}`} onClose={onClose}
      lead={<>{money(invoice.balance, invoice.currency)} still to pay{invoice.kind === 'renewal' ? '. Paying in full extends the subscription and applies any scheduled term change.' : '.'}</>}>
      <form onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        <ErrorBox error={run.error} />
        <div className="row">
          <Field label="Amount received" htmlFor="pay-amount">
            <input id="pay-amount" className="input" type="number" min="0.01" step="0.01" required value={f.amount} onChange={(e) => set('amount', e.target.value)} />
          </Field>
          <Field label="Method" htmlFor="pay-method">
            <select id="pay-method" value={f.method} onChange={(e) => set('method', e.target.value)}>{METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}</select>
          </Field>
        </div>
        {over && <div className="alert info">{money(amount - Number(invoice.balance), invoice.currency)} more than the balance. The extra becomes account credit and is used on the next invoice.</div>}
        <div className="row">
          <Field label="Reference" hint="Bank reference, UPI id or cheque number. Prevents recording the same payment twice." htmlFor="pay-ref">
            <input id="pay-ref" className="input" minLength={2} maxLength={100} value={f.reference} onChange={(e) => set('reference', e.target.value)} />
          </Field>
          <Field label="Received on" htmlFor="pay-date">
            <input id="pay-date" className="input" type="datetime-local" required value={f.received} max={nowLocal()} onChange={(e) => set('received', e.target.value)} />
          </Field>
        </div>
        <Field label="Note (optional)" htmlFor="pay-note"><input id="pay-note" className="input" maxLength={500} value={f.note} onChange={(e) => set('note', e.target.value)} /></Field>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={run.isPending || !(amount > 0)}>{run.isPending ? 'Recording…' : 'Record payment'}</button>
        </div>
      </form>
    </Modal>
  );
}

function VoidDialog({ invoice, onClose }: { invoice: Invoice; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefreshBilling();
  const run = useMutation({
    mutationFn: (reason: string) => api.voidInvoice(invoice.id, reason),
    onSuccess: async () => { await refresh(); toast(`${invoice.number} voided`); onClose(); },
  });
  return (
    <ReasonDialog title={`Void ${invoice.number}?`} danger confirm="Void invoice" busy={run.isPending} error={run.error} onClose={onClose} onSubmit={(r) => run.mutate(r)}
      lead="Cancels an unpaid invoice. Any account credit it used is returned. If billing should stop altogether, also turn auto-renew off." />
  );
}
