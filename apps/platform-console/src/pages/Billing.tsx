import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useCan } from '../auth';
import { ErrorBox, Loading, PageHead, Pager, Segmented, useToast } from '../ui';
import { InvoiceTable } from './invoices';

const PAGE = 25;
type View = 'open' | 'overdue' | 'paid' | 'all';
const FILTERS: Record<View, Record<string, string | undefined>> = {
  open: { status: 'open' }, overdue: { overdue: 'true' }, paid: { status: 'paid' }, all: {},
};

export function BillingPage() {
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const view = (params.get('view') as View) ?? 'open';
  const [offset, setOffset] = useState(0);
  const list = useQuery({
    queryKey: ['invoices', view, offset],
    queryFn: () => api.invoices({ ...FILTERS[view], limit: PAGE, offset }),
    placeholderData: (prev) => prev,
  });
  const run = useMutation({
    mutationFn: api.runBilling,
    onSuccess: async ({ result }) => {
      await qc.invalidateQueries();
      toast(`Sweep done: ${result.invoices_issued} invoice(s) issued, ${result.reminders} reminder(s), ${result.past_due} past due, ${result.expired} restricted`);
    },
    onError: (e) => toast((e as Error).message, true),
  });

  return (
    <>
      <PageHead title="Billing" sub="Invoices and payments. Renewal invoices are issued automatically two weeks before a period ends.">
        {can.superAdmin && <button className="btn" disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending ? 'Running…' : 'Run renewal sweep now'}</button>}
      </PageHead>
      <div className="filters">
        <Segmented name="Show" value={view} onChange={(v) => { setOffset(0); setParams({ view: v }, { replace: true }); }}
          options={[{ value: 'open', label: 'Open' }, { value: 'overdue', label: 'Overdue' }, { value: 'paid', label: 'Paid' }, { value: 'all', label: 'All' }]} />
      </div>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : (
        <>
          <InvoiceTable invoices={list.data?.items ?? []} showOrg />
          <Pager total={list.data?.total ?? 0} limit={PAGE} offset={offset} onChange={setOffset} />
        </>
      )}
    </>
  );
}
