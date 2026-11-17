import React, { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Button, Card, colors, Input, spacing, Switch, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { ApiError, useApparelSettings, useUpdateApparelSettings } from '@repo/api-client';

const toPaise = (rupees: string) => Math.round((parseFloat(rupees) || 0) * 100);

/**
 * The dress shop's GST and returns settings. GST changes with the government, so the rates and the price slab are settings and
 * not code: below the slab price a piece is taxed at the low rate, above it at the high rate (decided per piece, on its price
 * after discounts). Confirm the values with an accountant before selling.
 */
export const SettingsCard: React.FC<{ canEdit: boolean }> = ({ canEdit }) => {
  const query = useApparelSettings();
  const update = useUpdateApparelSettings();
  const s = query.data;
  const [low, setLow] = useState('');
  const [high, setHigh] = useState('');
  const [slab, setSlab] = useState('');
  const [days, setDays] = useState('');
  const [inclusive, setInclusive] = useState(true);
  const [round, setRound] = useState(true);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);

  useEffect(() => {
    if (!s) return;
    setLow(String(s.low_rate_percent));
    setHigh(String(s.high_rate_percent));
    setSlab(String(s.slab_price_paise / 100));
    setDays(String(s.return_days));
    setInclusive(s.prices_include_tax);
    setRound(s.round_to_rupee);
  }, [s]);

  if (!s) return query.isError ? <Text style={styles.error}>Could not load the dress shop settings.</Text> : null;

  const save = async () => {
    setMessage(null);
    try {
      await update.mutateAsync({
        low_rate_percent: parseFloat(low) || 0, high_rate_percent: parseFloat(high) || 0, slab_price_paise: toPaise(slab),
        return_days: Math.max(0, parseInt(days, 10) || 0), prices_include_tax: inclusive, round_to_rupee: round,
      });
      setMessage({ text: 'Saved.' });
    } catch (e) {
      setMessage({ text: e instanceof ApiError ? e.message : 'Could not save. Try again.', error: true });
    }
  };

  return (
    <Card style={styles.card}>
      <Text style={styles.title}>GST on garments</Text>
      <Text style={styles.sub}>
        {low}% up to {formatPaiseToRupees(toPaise(slab))} per piece, {high}% above. Confirm with your accountant.
      </Text>
      <View style={styles.row}>
        <View style={styles.field}><Input label="Rate up to the slab (%)" value={low} onChangeText={setLow} keyboardType="decimal-pad" editable={canEdit} /></View>
        <View style={styles.field}><Input label="Slab price per piece (₹)" value={slab} onChangeText={setSlab} keyboardType="decimal-pad" editable={canEdit} /></View>
        <View style={styles.field}><Input label="Rate above the slab (%)" value={high} onChangeText={setHigh} keyboardType="decimal-pad" editable={canEdit} /></View>
      </View>
      <View style={styles.switchRow}>
        <Text style={styles.label}>Prices already include GST</Text>
        <Switch value={inclusive} onValueChange={canEdit ? setInclusive : () => undefined} />
      </View>
      <View style={styles.switchRow}>
        <Text style={styles.label}>Round the invoice to the nearest rupee</Text>
        <Switch value={round} onValueChange={canEdit ? setRound : () => undefined} />
      </View>
      <Input label="Returns after this many days need a manager (0 = no limit)" value={days} onChangeText={setDays} keyboardType="number-pad" editable={canEdit} />
      {message ? <Text style={[styles.sub, message.error ? { color: colors.danger } : null]}>{message.text}</Text> : null}
      {canEdit ? <Button title="Save" onPress={save} loading={update.isPending} /> : <Text style={styles.sub}>Only an owner or manager can change these.</Text>}
    </Card>
  );
};

const styles = StyleSheet.create({
  card: { gap: spacing.md },
  title: { ...typography.bodyBold, color: colors.text },
  sub: { ...typography.caption, color: colors.textSecondary },
  label: { ...typography.body, color: colors.text, flex: 1 },
  row: { flexDirection: 'row', gap: spacing.md, flexWrap: 'wrap' },
  field: { flex: 1, minWidth: 160 },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  error: { ...typography.caption, color: colors.danger },
});
