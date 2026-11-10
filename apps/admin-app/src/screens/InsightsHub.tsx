import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, fonts, radii, spacing } from '@repo/shared-ui';
import { InsightsScreen } from './InsightsScreen';
import { ZReportsScreen } from './ZReports';

export type InsightsView = 'overview' | 'reports';

/** Insights: the business over time (Overview) and the end-of-day reports (Daily reports), one switch apart. */
export const InsightsHub: React.FC<{ view: InsightsView; onChangeView: (view: InsightsView) => void }> = ({ view, onChangeView }) => (
  <View style={styles.container}>
    <View style={styles.segmentWrapper}>
      <View style={styles.segmented} accessibilityRole="tablist">
        {([
          ['overview', 'Overview'],
          ['reports', 'Daily reports'],
        ] as const).map(([key, label]) => (
          <Pressable
            key={key}
            onPress={() => onChangeView(key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: view === key }}
            style={[styles.segment, view === key ? styles.segmentActive : null]}
          >
            <Text style={[styles.segmentText, view === key ? styles.segmentTextActive : null]}>{label}</Text>
          </Pressable>
        ))}
      </View>
    </View>
    <View style={{ flex: 1 }}>{view === 'overview' ? <InsightsScreen /> : <ZReportsScreen />}</View>
  </View>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  segmentWrapper: { paddingHorizontal: spacing.xl, paddingTop: spacing.md },
  segmented: { flexDirection: 'row', backgroundColor: colors.surfaceLight, borderRadius: radii.md, padding: 3 },
  segment: { flex: 1, minHeight: 36, alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm },
  segmentActive: { backgroundColor: colors.surface },
  segmentText: { fontSize: 13, fontFamily: fonts.medium, color: colors.textSecondary },
  segmentTextActive: { color: colors.text, fontFamily: fonts.semibold },
});
