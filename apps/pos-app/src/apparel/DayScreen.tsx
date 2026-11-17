import React from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { colors, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { useApparelDayReport } from '@repo/api-client';
import { QueryState } from '../components/QueryState';
import { TopBar } from '../components/TopBar';
import { useDeviceStore } from '../stores/deviceStore';

const money = (paise: number) => formatPaiseToRupees(paise);
const MODE: Record<string, string> = { cash: 'Cash', card: 'Card', upi: 'UPI', credit_note: 'Store credit' };

const Line: React.FC<{ label: string; value: string; strong?: boolean }> = ({ label, value, strong }) => (
  <View style={styles.line}>
    <Text style={[styles.label, strong ? styles.strong : null]}>{label}</Text>
    <Text style={[styles.value, strong ? styles.strong : null]}>{value}</Text>
  </View>
);

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <View style={styles.card}>
    <Text style={styles.title}>{title}</Text>
    {children}
  </View>
);

/** Today's takings for managers: invoices, GST by rate, payments, returns and net cash. Live, with nothing to close. */
export const DayScreen: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const device = useDeviceStore((s) => s.device)!;
  const query = useApparelDayReport({ branch_id: device.branchId });
  const r = query.data;

  return (
    <View style={styles.container}>
      <TopBar title="Today" subtitle={r ? `${device.branchName} · ${r.date}` : device.branchName} onBack={onBack} />
      <QueryState query={query} isEmpty={!r} emptyText="No report.">
        {r ? (
          <ScrollView contentContainerStyle={styles.body} refreshControl={<RefreshControl refreshing={query.isRefetching} onRefresh={() => void query.refetch()} />}>
            <Section title="Sales">
              <Line label={`${r.sales.count} paid ${r.sales.count === 1 ? 'invoice' : 'invoices'} · ${r.sales.units} pieces`} value={money(r.sales.total_paise)} strong />
              <Line label="Discounts given" value={`−${money(r.sales.discount_paise)}`} />
              <Line label="Taxable value" value={money(r.sales.taxable_paise)} />
              <Line label="CGST + SGST" value={money(r.sales.cgst_paise + r.sales.sgst_paise)} />
            </Section>
            <Section title="GST by rate">
              {r.tax_by_rate.length === 0 ? <Text style={styles.label}>No sales yet.</Text> : null}
              {r.tax_by_rate.map((t) => (
                <Line key={t.rate_percent} label={`${t.rate_percent}% on ${money(t.taxable_paise)}`} value={money(t.tax_paise)} />
              ))}
            </Section>
            <Section title="Payments">
              {r.payments.length === 0 ? <Text style={styles.label}>No payments yet.</Text> : null}
              {r.payments.map((p) => (
                <Line key={p.mode} label={`${MODE[p.mode] ?? p.mode} (${p.count})`} value={money(p.amount_paise)} />
              ))}
            </Section>
            <Section title="Returns">
              <Line label={`Taken back (${r.returns.count})`} value={money(r.returns.total_paise)} />
              <Line label="Refunded in cash" value={money(r.returns.cash_paise)} />
              <Line label="Given as store credit" value={money(r.returns.store_credit_paise)} />
            </Section>
            <Section title="Cash">
              <Line label="Cash taken minus cash refunds" value={money(r.net_cash_paise)} strong />
            </Section>
            <Section title="Selling best">
              {r.top_products.length === 0 ? <Text style={styles.label}>Nothing sold yet.</Text> : null}
              {r.top_products.slice(0, 5).map((p) => (
                <Line key={p.name} label={`${p.name} · ${p.units} pcs`} value={money(p.value_paise)} />
              ))}
              {r.units_by_size.length ? <Text style={styles.label}>By size: {r.units_by_size.map((z) => `${z.size} ${z.units}`).join(' · ')}</Text> : null}
            </Section>
          </ScrollView>
        ) : null}
      </QueryState>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.lg, gap: spacing.md, maxWidth: 720, width: '100%', alignSelf: 'center' },
  card: { gap: spacing.xs, padding: spacing.lg, borderRadius: radii.lg, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.surfaceBorder },
  title: { ...typography.bodyBold, color: colors.text, marginBottom: spacing.xs },
  line: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  label: { ...typography.body, color: colors.textSecondary, flexShrink: 1 },
  value: { ...typography.body, color: colors.text },
  strong: { ...typography.bodyBold, color: colors.text },
});
