import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { colors, spacing, radii, fonts } from '@repo/shared-ui';
import { StaffPINsScreen } from './StaffPINs';
import { ZReportsScreen } from './ZReports';
import { SettingsScreen } from './SettingsScreen';

export type MoreSubTab = 'StaffPINs' | 'ZReports' | 'Settings';

export interface MoreHubScreenProps {
  activeSubTab: MoreSubTab;
  onChangeSubTab: (tab: MoreSubTab) => void;
}

export const MoreHubScreen: React.FC<MoreHubScreenProps> = ({
  activeSubTab,
  onChangeSubTab,
}) => {
  return (
    <View style={styles.container}>
      {/* iOS Segmented Control Header */}
      <View style={styles.segmentWrapper}>
        <View style={styles.segmentedControl}>
          <TouchableOpacity
            style={[
              styles.segmentItem,
              activeSubTab === 'StaffPINs' && styles.segmentItemActive,
            ]}
            activeOpacity={0.8}
            onPress={() => onChangeSubTab('StaffPINs')}
          >
            <Text
              style={[
                styles.segmentText,
                activeSubTab === 'StaffPINs' && styles.segmentTextActive,
              ]}
            >
              Staff
            </Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={[
              styles.segmentItem,
              activeSubTab === 'ZReports' && styles.segmentItemActive,
            ]}
            activeOpacity={0.8}
            onPress={() => onChangeSubTab('ZReports')}
          >
            <Text
              style={[
                styles.segmentText,
                activeSubTab === 'ZReports' && styles.segmentTextActive,
              ]}
            >
              Reports
            </Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={[
              styles.segmentItem,
              activeSubTab === 'Settings' && styles.segmentItemActive,
            ]}
            activeOpacity={0.8}
            onPress={() => onChangeSubTab('Settings')}
          >
            <Text
              style={[
                styles.segmentText,
                activeSubTab === 'Settings' && styles.segmentTextActive,
              ]}
            >
              Settings
            </Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Sub-screen body */}
      <View style={styles.body}>
        {activeSubTab === 'StaffPINs' && <StaffPINsScreen />}
        {activeSubTab === 'ZReports' && <ZReportsScreen />}
        {activeSubTab === 'Settings' && <SettingsScreen />}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  segmentWrapper: {
    backgroundColor: '#ffffff',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm + 2,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceBorder,
  },
  segmentedControl: {
    flexDirection: 'row',
    backgroundColor: '#eeeff2',
    borderRadius: radii.md,
    padding: 3,
  },
  segmentItem: {
    flex: 1,
    paddingVertical: 7,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.sm + 1,
  },
  segmentItemActive: {
    backgroundColor: '#ffffff',
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.12,
    shadowRadius: 2.5,
    elevation: 2,
  },
  segmentText: {
    fontSize: 12,
    fontFamily: fonts.medium,
    color: colors.textSecondary,
  },
  segmentTextActive: {
    color: '#000000',
    fontFamily: fonts.bold,
  },
  body: {
    flex: 1,
  },
});
