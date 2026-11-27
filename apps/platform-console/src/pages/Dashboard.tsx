import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Dashboard, type PineLabsCloudStatus } from '../api';
import { date, label, money, relativeDays } from '../format';
import { Badge, ErrorBox, Loading, PageHead } from '../ui';

export function DashboardPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard });
  const categories = useQuery({ queryKey: ['business-categories'], queryFn: api.businessCategories, staleTime: Infinity });
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
        <BusinessTypes counts={data.organizations_by_category ?? {}}
          name={(k) => categories.data?.categories.find((c) => c.key === k)?.name ?? label(k)} />
        <PaymentsIntegration status={data.integrations?.pinelabs_cloud} />
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

/** Subscribers per business type (restaurants, cafés, dress and hardware shops…), each linking to the filtered list. */
function BusinessTypes({ counts, name }: { counts: NonNullable<Dashboard['organizations_by_category']>; name: (key: string) => string }) {
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <section className="card">
      <div className="card-head"><h2>Business types</h2></div>
      {rows.length === 0 ? <div className="empty">No subscribers yet.</div> : (
        <ul className="bars">
          {rows.map(([key, n]) => (
            <li key={key}>
              <Link to={`/subscribers?category=${encodeURIComponent(key)}`}>{name(key)}</Link>
              <span className="bar" aria-hidden="true"><span style={{ width: `${(n / max) * 100}%` }} /></span>
              <strong>{n}</strong>
            </li>
          ))}
        </ul>
      )}
      <p className="muted" style={{ marginBottom: 0 }}>Cancelled subscribers are not counted.</p>
    </section>
  );
}

/** Pine Labs cloud (card and UPI on the shop's terminal) is set up once for the platform, in the API server's environment. */
function PaymentsIntegration({ status }: { status: PineLabsCloudStatus | undefined }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Card &amp; UPI payments</h2></div>
      {!status ? <div className="empty">Status not reported by this API version.</div> : (
        <>
          <p style={{ marginTop: 0 }}>
            <strong>Pine Labs cloud</strong>{' '}
            {status.configured ? <Badge value="configured" tone="good" /> : <Badge value="not configured" tone="warn" />}{' '}
            <Badge value={status.environment === 'uat' ? 'UAT (test)' : 'production'} tone={status.environment === 'uat' ? 'info' : undefined} />
          </p>
          {status.configured
            ? <p className="muted">Counters without a paired terminal send card and UPI payments through the cloud. Requests under {money(status.min_amount)} are taken in cash.</p>
            : <p className="muted">Card and UPI through the cloud fail until these are set in the API server's environment: {status.missing.map((m) => <code key={m} style={{ marginRight: 6 }}>{m}</code>)}</p>}
          {status.configured && status.environment === 'uat' && (
            <div className="alert warn" role="status" style={{ marginBottom: 0 }}>Payments go to the Pine Labs UAT server: no real money moves. Point the base URL at production before going live.</div>
          )}
        </>
      )}
    </section>
  );
}
