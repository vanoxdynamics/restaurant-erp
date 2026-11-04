import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, BadgeProps, Button, colors, fonts, radii, spacing, typography } from '@repo/shared-ui';
import { PrintablePage, usePrintQueue } from '@repo/api-client';
import { Choice } from '../components/Choice';
import { TopBar } from '../components/TopBar';
import { paymentKind, printerKind, printsUnattended } from '../device';
import { useSimulator } from '../devices/simulator';
import { terminal } from '../devices/terminal';
import { JobState, usePrintQueueStore } from '../printing/printQueue';
import { useSlipPrinting } from '../printing/useSlipPrinting';
import { useDeviceStore } from '../stores/deviceStore';
import { usePrinterSettings } from '../stores/printerStore';

const STATE_BADGE: Record<JobState, { label: string; variant: BadgeProps['variant'] }> = {
  waiting: { label: 'Waiting', variant: 'neutral' },
  printing: { label: 'Printing', variant: 'warning' },
  printed: { label: 'Printed', variant: 'available' },
  failed: { label: 'Failed', variant: 'danger' },
};

const kotLabel = (kotNumber: string) => `KOT ${kotNumber.split('-').pop()}`;

function testPage(paperMm: 58 | 80, branch: string): PrintablePage {
  const width = paperMm === 58 ? 32 : 48;
  return {
    reference: 'TEST',
    paper_mm: paperMm,
    text: '',
    rows: [
      { style: 'big', align: 'center', text: 'TEST PRINT' },
      { style: 'normal', align: 'center', text: branch.slice(0, width) },
      { style: 'normal', align: 'left', text: '-'.repeat(width) },
      { style: 'bold', align: 'left', text: '2 x Butter Naan' },
      { style: 'normal', align: 'left', text: '  - less butter' },
      { style: 'normal', align: 'left', text: `${'1234567890'.repeat(5)}`.slice(0, width) },
      { style: 'normal', align: 'left', text: '-'.repeat(width) },
      { style: 'bold', align: 'center', text: `${paperMm} mm paper OK` },
    ],
  };
}

/** The counter's KOT printer: what is printing or failed, the paper width, a test print, and the terminal's details. */
export const KotPrinterScreen: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const device = useDeviceStore((s) => s.device)!;
  const { paperMm, setPaperMm } = usePrinterSettings();
  const jobs = usePrintQueueStore((s) => s.jobs);
  const retry = usePrintQueueStore((s) => s.retry);
  const unattended = printsUnattended();
  // A browser counter prints through the system dialog, so waiting KOTs are printed by hand from here.
  const waiting = usePrintQueue(device.branchId, !unattended && printerKind() === 'system');
  const dialog = useSlipPrinting('');
  const [message, setMessage] = useState<string | null>(null);
  const [info, setInfo] = useState<Record<string, string>>({});
  const simulator = paymentKind() === 'simulator';
  const sim = useSimulator();

  useEffect(() => {
    if (paymentKind() !== 'manual') void terminal.info().then(setInfo).catch(() => setInfo({}));
  }, []);

  const testPrint = async () => {
    setMessage(null);
    const result = await terminal.print(testPage(paperMm, device.branchName));
    setMessage(result.ok ? 'Test page printed.' : `Not printed: ${result.message}`);
  };

  return (
    <View style={styles.container}>
      <TopBar title="KOT printer" subtitle={device.branchName} onBack={onBack} />
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Paper</Text>
          <View style={styles.choices}>
            {([58, 80] as const).map((mm) => (
              <Choice key={mm} label={`${mm} mm`} selected={paperMm === mm} onPress={() => void setPaperMm(mm)} />
            ))}
          </View>
          {unattended ? <Button title="Test print" variant="secondary" onPress={testPrint} /> : null}
          {message ? <Text style={styles.hint}>{message}</Text> : null}
          <Text style={styles.hint}>
            {unattended
              ? 'KOTs sent from waiters’ phones print here automatically.'
              : printerKind() === 'system'
                ? 'This counter prints through the print dialog. KOTs sent from phones wait below until you print them.'
                : 'This device has no printer.'}
          </Text>
        </View>

        {unattended ? (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Recent KOTs</Text>
            {jobs.length === 0 ? <Text style={styles.hint}>Nothing printed yet on this device.</Text> : null}
            {jobs.slice(0, 30).map((job) => (
              <View key={job.key} style={styles.job}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.jobTitle}>
                    {kotLabel(job.kot.kot_number)}
                    {job.kot.table_label ? ` · ${job.kot.table_label}` : ''}
                    {job.kind === 'void' ? ' · VOID notice' : job.kind === 'reprint' ? ' · reprint' : ''}
                  </Text>
                  {job.error ? <Text style={styles.jobError}>{job.error}</Text> : null}
                </View>
                <Badge label={STATE_BADGE[job.state].label} variant={STATE_BADGE[job.state].variant} size="sm" />
                {job.state === 'failed' ? <Button title="Retry" size="sm" variant="secondary" onPress={() => retry(job.key)} /> : null}
              </View>
            ))}
          </View>
        ) : printerKind() === 'system' ? (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Waiting to print</Text>
            {(waiting.data ?? []).length === 0 ? <Text style={styles.hint}>Nothing waiting.</Text> : null}
            {(waiting.data ?? []).map((kot) => (
              <View key={kot.id} style={styles.job}>
                <Text style={[styles.jobTitle, { flex: 1 }]}>
                  {kotLabel(kot.kot_number)} · {kot.table_label}
                  {kot.status === 'voided' ? ' · VOID notice' : ''}
                </Text>
                <Button title="Print" size="sm" onPress={() => void dialog.print([kot])} />
              </View>
            ))}
            {dialog.awaiting ? (
              <View style={styles.choices}>
                <Button title="Yes, printed" variant="success" size="sm" onPress={dialog.confirmPrinted} loading={dialog.confirming} />
                <Button title="Not printed" variant="ghost" size="sm" onPress={dialog.notPrinted} />
              </View>
            ) : null}
          </View>
        ) : null}

        {Object.keys(info).length > 0 ? (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Terminal</Text>
            {Object.entries(info).map(([key, value]) => (
              <View key={key} style={styles.infoRow}>
                <Text style={styles.hint}>{key}</Text>
                <Text style={styles.infoValue}>{value}</Text>
              </View>
            ))}
          </View>
        ) : null}

        {simulator ? (
          <View style={[styles.card, styles.simCard]}>
            <Text style={styles.sectionTitle}>Simulator (development only)</Text>
            <View style={styles.choices}>
              <Choice label="Printer OK" selected={!sim.printerFault} onPress={() => sim.setPrinterFault(null)} />
              <Choice label="Out of paper" selected={sim.printerFault === '1002'} onPress={() => sim.setPrinterFault('1002')} />
              <Choice label="Paper jam" selected={sim.printerFault === '1009'} onPress={() => sim.setPrinterFault('1009')} />
            </View>
            {sim.paper.slice(0, 3).map((page) => (
              <View key={`${page.ref}-${page.at}`} style={[styles.paper, { width: paperMm === 58 ? 224 : 334 }]}>
                {page.lines.map((line, i) => (
                  <Text
                    key={i}
                    style={[styles.paperLine, line.center ? { textAlign: 'center' } : null, line.bold ? { fontWeight: '700' } : null, line.big ? styles.paperBig : null]}
                  >
                    {line.text}
                  </Text>
                ))}
              </View>
            ))}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: spacing.lg, gap: spacing.lg, maxWidth: 560, width: '100%', alignSelf: 'center' },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.surfaceBorder,
    padding: spacing.lg,
    gap: spacing.md,
  },
  simCard: { borderStyle: 'dashed' },
  sectionTitle: { ...typography.bodyBold, color: colors.text },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  hint: { ...typography.caption, color: colors.textSecondary },
  job: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  jobTitle: { ...typography.body, color: colors.text },
  jobError: { ...typography.caption, color: colors.danger, fontFamily: fonts.semibold },
  infoRow: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
  infoValue: { ...typography.caption, color: colors.text, fontFamily: fonts.semibold },
  paper: { backgroundColor: '#fff', borderWidth: 1, borderColor: colors.surfaceBorder, padding: spacing.sm, alignSelf: 'flex-start' },
  paperLine: { fontFamily: 'Courier New', fontSize: 11, color: '#000' },
  paperBig: { fontSize: 15, fontWeight: '700' },
});
