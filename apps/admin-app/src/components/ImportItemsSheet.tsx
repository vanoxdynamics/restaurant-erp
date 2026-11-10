import React, { useState } from 'react';
import { Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { ApiError, ItemImportResult, useImportItems } from '@repo/api-client';

const TEMPLATE = `name,category,price,gst_rate,hsn,unit,barcode,sku,sold_by_weight
Onion,Vegetables,40,0,0703,kg,,ONION-1,yes
Basmati Rice 1kg,Staples,120,5,1006,pcs,8901234567890,RICE-1KG,no
Bath Soap,Personal care,35,18,3401,pcs,8901030000001,,no
`;

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

/** Web only: pick a .csv file and read it as text. */
function pickCsvFile(): Promise<string | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.csv,text/csv';
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return resolve(null);
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result ?? ''));
      reader.onerror = () => resolve(null);
      reader.readAsText(file);
    };
    input.click();
  });
}

function downloadTemplate() {
  const url = URL.createObjectURL(new Blob([TEMPLATE], { type: 'text/csv' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'items-template.csv';
  a.click();
  URL.revokeObjectURL(url);
}

export interface ImportItemsSheetProps {
  visible: boolean;
  onClose: () => void;
}

/**
 * Load many items at once from a spreadsheet saved as CSV. "Check" validates without saving; "Import" saves the good rows and
 * lists every row it skipped with the reason, so the file can be fixed and imported again (rows update by SKU, barcode or name).
 */
export const ImportItemsSheet: React.FC<ImportItemsSheetProps> = ({ visible, onClose }) => {
  const importItems = useImportItems();
  const [csv, setCsv] = useState('');
  const [result, setResult] = useState<ItemImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const web = Platform.OS === 'web';

  const run = async (dryRun: boolean) => {
    setError(null);
    try {
      setResult(await importItems.mutateAsync({ csv, dryRun }));
    } catch (e) {
      setResult(null);
      setError(errorText(e));
    }
  };

  const close = () => {
    setResult(null);
    setError(null);
    onClose();
  };

  return (
    <Modal
      visible={visible}
      onClose={close}
      title="Import items"
      subtitle="From a spreadsheet saved as CSV"
      footer={
        <>
          <Button title="Close" variant="secondary" onPress={close} />
          <View style={{ flex: 1 }} />
          <Button title="Check" variant="outline" onPress={() => run(true)} disabled={!csv.trim()} loading={importItems.isPending && importItems.variables?.dryRun} />
          <Button title="Import" onPress={() => run(false)} disabled={!csv.trim()} loading={importItems.isPending && !importItems.variables?.dryRun} />
        </>
      }
    >
      <Text style={styles.help}>
        Columns: name, category, price (required), and gst_rate, hsn, unit (pcs, kg, g, l, ml), barcode, sku, sold_by_weight.
        Existing items with the same SKU, barcode or name are updated.
      </Text>
      {web ? (
        <View style={styles.row}>
          <Button title="Choose file" variant="secondary" size="sm" onPress={async () => { const text = await pickCsvFile(); if (text !== null) { setCsv(text); setResult(null); } }} />
          <Button title="Download template" variant="ghost" size="sm" onPress={downloadTemplate} />
        </View>
      ) : null}
      <Input
        label={web ? 'Or paste the CSV' : 'Paste the CSV'}
        value={csv}
        onChangeText={(v) => {
          setCsv(v);
          setResult(null);
        }}
        multiline
        placeholder={TEMPLATE}
        style={styles.textarea}
        autoCapitalize="none"
        autoCorrect={false}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {result ? (
        <View style={styles.result}>
          <Text style={styles.resultHead}>
            {result.dry_run ? 'Check: ' : 'Imported: '}
            {result.created} new, {result.updated} updated
            {result.categories_created ? `, ${result.categories_created} new categories` : ''}
            {result.errors.length ? `, ${result.errors.length} skipped` : ''}
          </Text>
          {result.dry_run && !result.errors.length ? <Text style={styles.ok}>No problems found. Tap Import to save.</Text> : null}
          <ScrollView style={{ maxHeight: 220 }}>
            {result.errors.map((e) => (
              <Text key={e.row} style={styles.rowError}>
                Line {e.row}{e.data.name ? ` (${e.data.name})` : ''}: {e.errors.join('; ')}
              </Text>
            ))}
          </ScrollView>
        </View>
      ) : null}
    </Modal>
  );
};

const styles = StyleSheet.create({
  help: { ...typography.caption, color: colors.textSecondary, marginBottom: spacing.md },
  row: { flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.md },
  textarea: { minHeight: 140, fontFamily: Platform.OS === 'web' ? 'monospace' : undefined, fontSize: 12 },
  error: { ...typography.caption, color: colors.danger },
  result: { padding: spacing.md, borderRadius: radii.md, backgroundColor: colors.surfaceLight, gap: spacing.xs },
  resultHead: { ...typography.bodyBold, color: colors.text },
  ok: { ...typography.caption, color: colors.success, fontFamily: fonts.semibold },
  rowError: { ...typography.caption, color: colors.danger, marginTop: 2 },
});
