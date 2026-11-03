import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { date, label, money, relativeDays } from '../format';
import { Badge, ErrorBox, Loading, PageHead } from '../ui';

export function DashboardPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;

  const total = Object.values(data.organizations_by_status).reduce((a, b) => a + b, 0);
  const currencies = Object.keys(data.mrr);
  return (
    <>
      <PageHead title="Dashboard" sub="Subscribers, revenue and what needs attention." />

      {data.past_due > 0 && (
        <div className="alert warn" role="status">
          {data.past_due} subscriber{data.past_due === 1 ? ' is' : 's are'} past due. <Link to="/subscribers?subscription_status=past_due">Review them</Link>
        </div>
      )}

      {data.overdue_invoices > 0 && (
        <div className="alert warn" role="status">
          {data.overdue_invoices} invoice{data.overdue_invoices === 1 ? ' is' : 's are'} overdue. <Link to="/billing?view=overdue">Review them</Link>
        </div>
      )}

      <div className="grid cols-4">
        <div className="card stat">
          <h3>Subscribers</h3>
          <div className="value">{total}</div>
          <div className="hint">{Object.entries(data.organizations_by_status).map(([k, v]) => `${v} ${label(k).toLowerCase()}`).join(' · ') || 'None yet'}</div>
        </div>
        <div className="card stat">
          <h3>Monthly recurring</h3>
          <div className="value">{currencies.length ? currencies.map((c) => money(data.mrr[c], c)).join(' + ') : money(0)}</div>
          <div className="hint">Active paying subscribers</div>
        </div>
        <div className="card stat">
          <h3>Annual run rate</h3>
          <div className="value">{currencies.length ? currencies.map((c) => money(data.arr[c], c)).join(' + ') : money(0)}</div>
          <div className="hint">MRR × 12</div>
        </div>
        <div className="card stat">
          <h3>Outstanding</h3>
          <div className="value">{Object.keys(data.outstanding).length ? Object.entries(data.outstanding).map(([c, v]) => money(v, c)).join(' + ') : money(0)}</div>
          <div className="hint"><Link to="/billing">{data.open_invoices} open invoice{data.open_invoices === 1 ? '' : 's'}</Link></div>
        </div>
        <div className="card stat">
          <h3>Billing terms</h3>
          <div className="value">{data.subscriptions_by_term.monthly ?? 0} / {data.subscriptions_by_term.yearly ?? 0}</div>
          <div className="hint">Monthly / yearly</div>
        </div>
      </div>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <section className="card">
          <div className="card-head"><h2>Renewals in the next 30 days</h2></div>
          {data.renewals_next_30_days.length === 0 ? <div className="empty">No renewals due.</div> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Subscriber</th><th>Term</th><th>Amount</th><th>Renews</th></tr></thead>
                <tbody>
                  {data.renewals_next_30_days.map((r) => (
                    <tr key={String(r.organization_id)}>
                      <td><Link to={`/subscribers/${r.organization_id}`}>{String(r.name)}</Link>{r.auto_renew === false && <> <Badge value="no auto-renew" tone="warn" /></>}</td>
                      <td>{label(String(r.billing_term))}</td>
                      <td>{money(String(r.price), String(r.currency))}</td>
                      <td className="nowrap">{date(String(r.renews_at))} <span className="muted">({relativeDays(String(r.renews_at))})</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card">
          <div className="card-head"><h2>Trials ending this week</h2></div>
          {data.trials_ending_next_7_days.length === 0 ? <div className="empty">No trials ending soon.</div> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Subscriber</th><th>Term</th><th>Ends</th></tr></thead>
                <tbody>
                  {data.trials_ending_next_7_days.map((r) => (
                    <tr key={r.organization_id}>
                      <td><Link to={`/subscribers/${r.organization_id}`}>{r.name}</Link></td>
                      <td>{label(r.billing_term)}</td>
                      <td className="nowrap">{date(r.trial_ends_at)} <span className="muted">({relativeDays(r.trial_ends_at)})</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
