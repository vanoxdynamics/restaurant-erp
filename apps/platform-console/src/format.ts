export function money(amount: string | number | null | undefined, currency = 'INR'): string {
  if (amount === null || amount === undefined || amount === '') return '—';
  const n = typeof amount === 'string' ? Number(amount) : amount;
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency, maximumFractionDigits: 2 }).format(n);
}

export function date(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('en-IN', withTime
    ? { dateStyle: 'medium', timeStyle: 'short' }
    : { dateStyle: 'medium' });
}

export function relativeDays(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const days = Math.round((new Date(iso).getTime() - now.getTime()) / 86_400_000);
  if (days === 0) return 'today';
  return days > 0 ? `in ${days} day${days === 1 ? '' : 's'}` : `${-days} day${days === -1 ? '' : 's'} ago`;
}

export const label = (s: string | null | undefined) => (s ?? '—').replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

export const TERM_LABEL = { monthly: 'Monthly', yearly: 'Yearly' } as const;

/** Convert a datetime-local value (browser local time) to an ISO string, or undefined when empty. */
export const localToIso = (v: string): string | undefined => (v ? new Date(v).toISOString() : undefined);
