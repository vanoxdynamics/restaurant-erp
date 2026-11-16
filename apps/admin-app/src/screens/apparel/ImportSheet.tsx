import React, { useState } from 'react';
import { Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Button, colors, fonts, Input, Modal, radii, spacing, typography } from '@repo/shared-ui';
import { ApiError, ApparelImportResult, useApparelImport } from '@repo/api-client';

const TEMPLATE = `style_code,name,category,brand,hsn,size,colour,mrp,price,barcode,sku,gst_rate,opening_stock
OX-01,Oxford Shirt,Shirts,Arrow,6205,S,Blue,1299,999,,,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,M,Blue,1299,999,,,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,M,White,1299,999,8901234567890,,,
,Leather Belt,Accessories,,4203,Free,Brown,,499,,,18,
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
  a.download = 'dress-shop-template.csv';
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Load a dress shop's catalogue from a spreadsheet saved as CSV: one row per size and colour. Rows with the same style code (or
 * the same name in the same category) become one product. "Check" validates without saving; "Import" saves the good rows and
 * lists every row it skipped with the reason.
 */
export const ImportSheet: React.FC<{ visible: boolean; onClose: () => void; branchId?: string }> = ({ visible, onClose, branchId }) => {
  const run = useApparelImport();
  const [csv, setCsv] = useState('');
  const [result, setResult] = useState<ApparelImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const web = Platform.OS === 'web';

  const go = async (dryRun: boolean) => {
    setError(null);
    try {
      setResult(await run.mutateAsync({ csv, dryRun, branchId }));
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
      title="Import products"
      subtitle="One row per size and colour, from a spreadsheet saved as CSV"
      footer={
        <>
          <Button title="Close" variant="secondary" onPress={close} />
          <View style={{ flex: 1 }} />
          <Button title="Check" variant="outline" onPress={() => go(true)} disabled={!csv.trim()} loading={run.isPending && run.variables?.dryRun} />
          <Button title="Import" onPress={() => go(false)} disabled={!csv.trim()} loading={run.isPending && !run.variables?.dryRun} />
        </>
      }
    >
      <Text style={styles.help}>
        Required: name, category, size, price. Optional: style_code (rows with the same code are one product), brand, hsn, colour
        (blank means Default), mrp, barcode (blank: one is made), sku, gst_rate (blank: the price-based rate; a number: a fixed
        rate for belts, bags and the like).
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
        onChangeText={(v) => { setCsv(v); setResult(null); }}
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
            {result.products_created} new products, {result.variants_created} new sizes/colours, {result.variants_updated} updated
            {result.categories_created ? `, ${result.categories_created} new categories` : ''}
            {result.errors.length ? `, ${result.errors.length} skipped` : ''}
          </Text>
          {result.dry_run && !result.errors.length ? <Text style={styles.ok}>No problems found. Tap Import to save.</Text> : null}
          <ScrollView style={{ maxHeight: 220 }}>
            {result.errors.map((e) => (
              <Text key={e.row} style={styles.rowError}>
                Line {e.row}{e.data.name ? ` (${e.data.name}${e.data.size ? ` ${e.data.size}` : ''})` : ''}: {e.errors.join('; ')}
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
