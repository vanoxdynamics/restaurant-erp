import React, { useMemo, useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, Card, colors, fonts, Input, radii, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, ApparelLabelOptions, apparelReportsApi, useApparelPurchases, useApparelStock } from '@repo/api-client';
import { QueryState } from '../../components/QueryState';
import { useAuthStore } from '../../stores/authStore';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

/** Open a printable page in a new window and bring up the print dialog. Only the web admin can print; elsewhere say so. */
export function openPrintable(html: string): boolean {
  if (Platform.OS !== 'web' || typeof window === 'undefined') return false;
  const win = window.open('', '_blank');
  if (!win) return false;
  win.document.open();
  win.document.write(html);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 400);
  return true;
}

/** Sticker sizes most label printer rolls come in (width x height, mm). */
const ROLL_SIZES: [number, number][] = [[50, 25], [38, 25], [50, 30], [50, 38], [75, 50]];

interface PrinterSetup {
  paper: 'a4' | 'roll';
  columns: number;
  width: number;
  height: number;
  across: number;
}

const DEFAULT_SETUP: PrinterSetup = { paper: 'a4', columns: 3, width: 50, height: 25, across: 1 };
const SETUP_KEY = 'vanox.labelPrinter';

/** The printer set up last time on this computer (web only), so the shop picks its sticker size once. */
function loadSetup(): PrinterSetup {
  try {
    if (Platform.OS === 'web' && typeof localStorage !== 'undefined') {
      const saved = localStorage.getItem(SETUP_KEY);
      if (saved) return { ...DEFAULT_SETUP, ...JSON.parse(saved) };
    }
  } catch {
    // a broken saved value: start from the default
  }
  return DEFAULT_SETUP;
}

function saveSetup(setup: PrinterSetup) {
  try {
    if (Platform.OS === 'web' && typeof localStorage !== 'undefined') localStorage.setItem(SETUP_KEY, JSON.stringify(setup));
  } catch {
    // private mode or storage full: the choice just is not remembered
  }
}

const Choice: React.FC<{ label: string; on: boolean; onPress: () => void }> = ({ label, on, onPress }) => (
  <Pressable onPress={onPress} accessibilityRole="radio" accessibilityState={{ selected: on }} accessibilityLabel={label}
    style={[styles.colChoice, on ? styles.colChoiceOn : null]}>
    <Text style={[styles.meta, on ? { color: colors.primary, fontFamily: fonts.semibold } : null]}>{label}</Text>
  </Pressable>
);

/**
 * Barcode labels, for A4 sticker sheets or a label (sticker) printer. Pick the variants and how many labels of each (or take everything from the last
 * delivery), then print: the server makes the page, the browser's print dialog prints it. A tag scanned at the counter finds
 * the exact size and colour.
 */
export const LabelsScreen: React.FC = () => {
  const branchId = useAuthStore((s) => s.activeBranchId) ?? undefined;
  const [search, setSearch] = useState('');
  const [copies, setCopies] = useState<Record<string, number>>({});
  const [setup, setSetupState] = useState<PrinterSetup>(loadSetup);
  const setSetup = (changes: Partial<PrinterSetup>) => setSetupState((s) => {
    const next = { ...s, ...changes };
    saveSetup(next);
    return next;
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const stock = useApparelStock({ branch_id: branchId, search: search.trim() || undefined }, !!branchId);
  const purchases = useApparelPurchases(branchId, !!branchId);
  const lastDelivery = purchases.data?.items[0];

  const rows = stock.data ?? [];
  const chosen = useMemo(() => Object.entries(copies).filter(([, n]) => n > 0), [copies]);
  const total = chosen.reduce((n, [, c]) => n + c, 0);

  const set = (variantId: string, n: number) => {
    setCopies((c) => ({ ...c, [variantId]: Math.max(0, Math.min(200, n)) }));
    setMessage(null);
  };

  const fromLastDelivery = () => {
    if (!lastDelivery) return;
    const next: Record<string, number> = {};
    for (const line of lastDelivery.lines) next[line.variant_id] = Math.min(200, line.quantity);
    setCopies(next);
    setMessage({ text: `One label per piece of the delivery of ${lastDelivery.received_on}${lastDelivery.invoice_number ? ` (${lastDelivery.invoice_number})` : ''}.` });
  };

  const print = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const options: ApparelLabelOptions = setup.paper === 'roll'
        ? { paper: 'roll', label_width_mm: setup.width, label_height_mm: setup.height, across: setup.across }
        : { paper: 'a4', columns: setup.columns };
      const html = await apparelReportsApi.labelsHtml(chosen.map(([variant_id, c]) => ({ variant_id, copies: c })), options);
      if (!openPrintable(html)) setMessage({ text: 'Printing works from the web admin. Open this page in a browser.', error: true });
    } catch (e) {
      setMessage({ text: errorText(e), error: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={[typography.h1, { color: colors.text }]}>Barcode labels</Text>
        <Text style={styles.sub}>Choose how many labels of each size and colour. Each label carries the barcode, the name, size and price.</Text>

        <View style={styles.actions}>
          <View style={{ flex: 1, minWidth: 220 }}>
            <Input value={search} onChangeText={setSearch} placeholder="Search styles" accessibilityLabel="Search styles" />
          </View>
          <Button title="All from the last delivery" variant="secondary" onPress={fromLastDelivery} disabled={!lastDelivery} />
        </View>

        <QueryState query={stock} isEmpty={rows.length === 0} emptyText="No styles yet.">
          <Card style={{ padding: 0 }}>
            {rows.map((r) => {
              const n = copies[r.variant_id] ?? 0;
              return (
                <View key={r.variant_id} style={[styles.row, n > 0 ? styles.rowPicked : null]}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.name} numberOfLines={1}>{r.product_name}</Text>
                    <Text style={styles.meta}>{r.size} · {r.colour} · {formatPaiseToRupees(r.price_paise)} · {r.quantity} in stock</Text>
                  </View>
                  <View style={styles.stepper}>
                    <Pressable onPress={() => set(r.variant_id, n - 1)} accessibilityRole="button" accessibilityLabel={`One less label for ${r.product_name} ${r.size} ${r.colour}`} style={styles.step}>
                      <Text style={styles.stepText}>−</Text>
                    </Pressable>
                    <Text style={styles.count}>{n}</Text>
                    <Pressable onPress={() => set(r.variant_id, n + 1)} accessibilityRole="button" accessibilityLabel={`One more label for ${r.product_name} ${r.size} ${r.colour}`} style={styles.step}>
                      <Text style={styles.stepText}>+</Text>
                    </Pressable>
                  </View>
                </View>
              );
            })}
          </Card>
        </QueryState>
      </ScrollView>

      <View style={styles.foot}>
        <View style={styles.cols}>
          <Text style={styles.meta}>Print on</Text>
          <Choice label="A4 sticker sheet" on={setup.paper === 'a4'} onPress={() => setSetup({ paper: 'a4' })} />
          <Choice label="Label printer" on={setup.paper === 'roll'} onPress={() => setSetup({ paper: 'roll' })} />
        </View>
        {setup.paper === 'a4' ? (
          <View style={styles.cols}>
            <Text style={styles.meta}>Labels across</Text>
            {[2, 3, 4].map((n) => <Choice key={n} label={String(n)} on={setup.columns === n} onPress={() => setSetup({ columns: n })} />)}
          </View>
        ) : (
          <>
            <View style={styles.cols}>
              <Text style={styles.meta}>Sticker size (mm)</Text>
              {ROLL_SIZES.map(([w, h]) => (
                <Choice key={`${w}x${h}`} label={`${w} × ${h}`} on={setup.width === w && setup.height === h}
                  onPress={() => setSetup({ width: w, height: h })} />
              ))}
            </View>
            <View style={styles.cols}>
              <Text style={styles.meta}>Stickers across the roll</Text>
              {[1, 2].map((n) => <Choice key={n} label={String(n)} on={setup.across === n} onPress={() => setSetup({ across: n })} />)}
            </View>
            <Text style={styles.meta}>
              In the print window choose your label printer, set margins to None and scale to 100%. The paper size should be {setup.width * setup.across + 2 * (setup.across - 1)} × {setup.height} mm.
            </Text>
          </>
        )}
        {message ? <Text style={[styles.meta, message.error ? { color: colors.danger } : null]}>{message.text}</Text> : null}
        <View style={styles.actions}>
          {total > 0 ? <Button title="Clear" variant="ghost" onPress={() => setCopies({})} /> : null}
          <Button title={total ? `Print ${total} ${total === 1 ? 'label' : 'labels'}` : 'Print labels'} onPress={print} disabled={!total} loading={busy} style={{ flex: 1 }} />
        </View>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.xl, gap: spacing.md },
  sub: { ...typography.body, color: colors.textSecondary },
  actions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, padding: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.surfaceBorder },
  rowPicked: { backgroundColor: colors.primaryGlow },
  name: { ...typography.bodyBold, color: colors.text },
  meta: { ...typography.caption, color: colors.textSecondary },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  step: { width: 36, height: 36, borderRadius: radii.md, borderWidth: 1, borderColor: colors.surfaceBorder, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surface },
  stepText: { ...typography.bodyBold, color: colors.text },
  count: { ...typography.bodyBold, minWidth: 32, textAlign: 'center', color: colors.text },
  foot: { gap: spacing.sm, padding: spacing.lg, borderTopWidth: 1, borderTopColor: colors.surfaceBorder, backgroundColor: colors.surface },
  cols: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  colChoice: { minWidth: 36, minHeight: 32, paddingHorizontal: spacing.sm, borderRadius: radii.sm, borderWidth: 1, borderColor: colors.surfaceBorder, alignItems: 'center', justifyContent: 'center' },
  colChoiceOn: { borderColor: colors.primary, backgroundColor: colors.primaryGlow },
});
