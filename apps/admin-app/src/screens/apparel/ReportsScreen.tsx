import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, Card, colors, fonts, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApparelDayReport, useApparelDayReport, useApparelSalesByProduct } from '@repo/api-client';
import { QueryState } from '../../components/QueryState';
import { useAuthStore } from '../../stores/authStore';

const money = (paise: number) => formatPaiseToRupees(paise);
const MODE: Record<string, string> = { cash: 'Cash', card: 'Card', upi: 'UPI', credit_note: 'Store credit' };

const isoDate = (d: Date) => d.toLocaleDateString('en-CA'); // yyyy-mm-dd in the device's own day
const shift = (iso: string, days: number) => {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return isoDate(d);
};
const validDate = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(new Date(`${s}T12:00:00`).getTime());

const Line: React.FC<{ label: string; value: string; strong?: boolean }> = ({ label, value, strong }) => (
  <View style={styles.line}>
    <Text style={strong ? styles.strong : styles.label}>{label}</Text>
    <Text style={strong ? styles.strong : styles.value}>{value}</Text>
  </View>
);

/** The day report (replaces the Z-report for dress shops) and sales by product and size. Computed live, nothing to close. */
export const ReportsScreen: React.FC = () => {
  const [view, setView] = useState<'day' | 'products'>('day');
  return (
    <View style={styles.container}>
      <View style={styles.segmented} accessibilityRole="tablist">
        {([['day', 'Day report'], ['products', 'Sales by product']] as const).map(([key, label]) => (
          <Pressable key={key} onPress={() => setView(key)} accessibilityRole="tab" accessibilityState={{ selected: view === key }}
            style={[styles.segment, view === key ? styles.segmentOn : null]}>
            <Text style={[styles.segmentText, view === key ? styles.segmentTextOn : null]}>{label}</Text>
          </Pressable>
        ))}
      </View>
      {view === 'day' ? <DayReport /> : <ProductSales />}
    </View>
  );
};

const DayReport: React.FC = () => {
  const branchId = useAuthStore((s) => s.activeBranchId) ?? undefined;
  const today = isoDate(new Date());
  const [date, setDate] = useState(today);
  const [typed, setTyped] = useState(today);
  const query = useApparelDayReport({ date, branch_id: branchId }, !!branchId);
  const go = (iso: string) => {
    setDate(iso);
    setTyped(iso);
  };
  const r: ApparelDayReport | undefined = query.data;

  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <View style={styles.dateRow}>
        <Button title="‹" variant="secondary" size="sm" onPress={() => go(shift(date, -1))} accessibilityLabel="Previous day" />
        <View style={{ width: 150 }}>
          <Input value={typed} onChangeText={(v) => { setTyped(v); if (validDate(v)) setDate(v); }} placeholder="yyyy-mm-dd" accessibilityLabel="Report date" />
        </View>
        <Button title="›" variant="secondary" size="sm" onPress={() => go(shift(date, 1))} disabled={date >= today} accessibilityLabel="Next day" />
        <Button title="Today" variant="ghost" size="sm" onPress={() => go(today)} />
      </View>
      <QueryState query={query} isEmpty={!r} emptyText="No report.">
        {r ? (
          <>
            <Card style={styles.card}>
              <Text style={styles.title}>Sales</Text>
              <Line label="Invoices (paid)" value={String(r.sales.count)} />
              <Line label="Pieces sold" value={String(r.sales.units)} />
              <Line label="Subtotal" value={money(r.sales.subtotal_paise)} />
              <Line label="Discounts" value={`−${money(r.sales.discount_paise)}`} />
              <Line label="Taxable value" value={money(r.sales.taxable_paise)} />
              <Line label="CGST" value={money(r.sales.cgst_paise)} />
              <Line label="SGST" value={money(r.sales.sgst_paise)} />
              <Line label="Round off" value={money(r.sales.round_off_paise)} />
              <Line label="Total" value={money(r.sales.total_paise)} strong />
            </Card>
            <Card style={styles.card}>
              <Text style={styles.title}>GST by rate</Text>
              {r.tax_by_rate.length === 0 ? <Text style={styles.label}>No sales.</Text> : null}
              {r.tax_by_rate.map((t) => (
                <Line key={t.rate_percent} label={`${t.rate_percent}% on ${money(t.taxable_paise)}`} value={money(t.tax_paise)} />
              ))}
            </Card>
            <Card style={styles.card}>
              <Text style={styles.title}>Payments</Text>
              {r.payments.length === 0 ? <Text style={styles.label}>No payments.</Text> : null}
              {r.payments.map((p) => (
                <Line key={p.mode} label={`${MODE[p.mode] ?? p.mode} (${p.count})`} value={money(p.amount_paise)} />
              ))}
            </Card>
            <Card style={styles.card}>
              <Text style={styles.title}>Returns</Text>
              <Line label={`Taken back (${r.returns.count})`} value={money(r.returns.total_paise)} />
              <Line label="Refunded in cash" value={money(r.returns.cash_paise)} />
              <Line label="Given as store credit" value={money(r.returns.store_credit_paise)} />
              <Line label="Net cash (cash taken minus cash refunds)" value={money(r.net_cash_paise)} strong />
            </Card>
            <Card style={styles.card}>
              <Text style={styles.title}>Top products</Text>
              {r.top_products.length === 0 ? <Text style={styles.label}>No sales.</Text> : null}
              {r.top_products.map((p) => (
                <Line key={p.name} label={`${p.name} · ${p.units} pcs`} value={money(p.value_paise)} />
              ))}
              {r.units_by_size.length ? (
                <>
                  <Text style={[styles.title, { marginTop: spacing.md }]}>Pieces by size</Text>
                  <Text style={styles.label}>{r.units_by_size.map((z) => `${z.size}: ${z.units}`).join('   ')}</Text>
                </>
              ) : null}
            </Card>
          </>
        ) : null}
      </QueryState>
    </ScrollView>
  );
};

const PRESETS = [['Today', 0], ['7 days', 6], ['30 days', 29]] as const;

const ProductSales: React.FC = () => {
  const branchId = useAuthStore((s) => s.activeBranchId) ?? undefined;
  const today = isoDate(new Date());
  const [from, setFrom] = useState(shift(today, -6));
  const [to, setTo] = useState(today);
  const ok = validDate(from) && validDate(to) && from <= to;
  const query = useApparelSalesByProduct({ from, to, branch_id: branchId }, ok && !!branchId);
  const data = query.data;

  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <View style={styles.dateRow}>
        <View style={{ width: 150 }}><Input label="From" value={from} onChangeText={setFrom} placeholder="yyyy-mm-dd" /></View>
        <View style={{ width: 150 }}><Input label="To" value={to} onChangeText={setTo} placeholder="yyyy-mm-dd" /></View>
        {PRESETS.map(([label, back]) => (
          <Button key={label} title={label} variant="secondary" size="sm" onPress={() => { setFrom(shift(today, -back)); setTo(today); }} />
        ))}
      </View>
      {!ok ? <Text style={styles.error}>Enter the dates as yyyy-mm-dd, with the first before the last.</Text> : null}
      {ok ? (
        <QueryState query={query} isEmpty={!data || data.products.length === 0} emptyText="No sales in this period.">
          {data ? (
            <>
              <Card style={styles.card}>
                <Line label={`${data.units} pieces`} value={money(data.value_paise)} strong />
              </Card>
              {data.products.map((p) => (
                <Card key={p.name} style={styles.card}>
                  <Line label={p.name} value={money(p.value_paise)} strong />
                  <Text style={styles.label}>{p.units} pcs · {p.sizes.map((z) => `${z.size}: ${z.units}`).join('   ')}</Text>
                </Card>
              ))}
            </>
          ) : null}
        </QueryState>
      ) : null}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  segmented: { flexDirection: 'row', backgroundColor: colors.surfaceLight, borderRadius: radii.md, padding: 3, marginHorizontal: spacing.xl, marginTop: spacing.md },
  segment: { flex: 1, minHeight: 36, alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm },
  segmentOn: { backgroundColor: colors.surface },
  segmentText: { fontSize: 13, fontFamily: fonts.medium, color: colors.textSecondary },
  segmentTextOn: { color: colors.text, fontFamily: fonts.semibold },
  content: { padding: spacing.xl, gap: spacing.md },
  dateRow: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm, flexWrap: 'wrap' },
  card: { gap: spacing.xs },
  title: { ...typography.bodyBold, color: colors.text, marginBottom: spacing.xs },
  line: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  label: { ...typography.body, color: colors.textSecondary, flexShrink: 1 },
  value: { ...typography.body, color: colors.text },
  strong: { ...typography.bodyBold, color: colors.text, flexShrink: 1 },
  error: { ...typography.caption, color: colors.danger },
});
