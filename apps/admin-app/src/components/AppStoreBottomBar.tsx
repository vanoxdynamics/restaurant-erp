import React from 'react';
import { View, Text, Pressable, StyleSheet, Platform } from 'react-native';
import { colors, spacing, fonts } from '@repo/shared-ui';
import { Icon, IconName } from './Icon';

export type AppStoreTabKey =
  | 'Today'
  | 'Floor'
  | 'Menu'
  | 'Approvals'
  | 'More';

export interface AppStoreBottomBarProps {
  currentTab: string; // 'LivePulse' | 'FloorObserver' | 'MenuBuilder' | 'VoidApprovals' | 'StaffPINs' | 'ZReports' | 'Settings' | 'More'
  onSelectTab: (tab: AppStoreTabKey) => void;
  pendingApprovalsCount?: number;
}

const TABS: { key: AppStoreTabKey; label: string; icon: IconName; matches: string[] }[] = [
  { key: 'Today', label: 'Today', icon: 'sun', matches: ['LivePulse', 'Today'] },
  { key: 'Floor', label: 'Floor', icon: 'grid', matches: ['FloorObserver', 'Floor'] },
  { key: 'Menu', label: 'Menu', icon: 'book-open', matches: ['MenuBuilder', 'Menu'] },
  { key: 'Approvals', label: 'Approvals', icon: 'check-circle', matches: ['VoidApprovals', 'Approvals'] },
  { key: 'More', label: 'More', icon: 'menu', matches: ['More', 'StaffPINs', 'ZReports', 'Settings'] },
];

export const AppStoreBottomBar: React.FC<AppStoreBottomBarProps> = ({
  currentTab,
  onSelectTab,
  pendingApprovalsCount = 0,
}) => (
  <View style={styles.bar} accessibilityRole="tablist">
    {TABS.map((tab) => {
      const active = tab.matches.includes(currentTab);
      const showBadge = tab.key === 'Approvals' && pendingApprovalsCount > 0;
      const tint = active ? colors.primary : colors.textMuted;

      return (
        <Pressable
          key={tab.key}
          style={styles.tab}
          onPress={() => onSelectTab(tab.key)}
          accessibilityRole="tab"
          accessibilityState={{ selected: active }}
          accessibilityLabel={showBadge ? `${tab.label}, ${pendingApprovalsCount} waiting` : tab.label}
        >
          {({ pressed }) => (
            <View style={[styles.tabInner, { opacity: pressed ? 0.6 : 1 }]}>
              <View style={[styles.iconPill, active ? styles.iconPillActive : null]}>
                <Icon name={tab.icon} size={20} color={tint} />
                {showBadge ? (
                  <View style={styles.badge}>
                    <Text style={styles.badgeText}>{pendingApprovalsCount > 99 ? '99+' : pendingApprovalsCount}</Text>
                  </View>
                ) : null}
              </View>
              <Text style={[styles.label, { color: tint, fontFamily: active ? fonts.semibold : fonts.medium }]}>{tab.label}</Text>
            </View>
          )}
        </Pressable>
      );
    })}
  </View>
);

const styles = StyleSheet.create({
  bar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    flexDirection: 'row',
    backgroundColor: colors.backgroundSecondary,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceBorder,
    paddingTop: 6,
    paddingBottom: Platform.OS === 'ios' ? 24 : 10,
  },
  tab: {
    flex: 1,
    minHeight: 52,
  },
  tabInner: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconPill: {
    width: 56,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconPillActive: {
    backgroundColor: colors.primaryGlow,
  },
  label: {
    fontFamily: fonts.regular,
    fontSize: 11,
    marginTop: 2,
  },
  badge: {
    position: 'absolute',
    top: -2,
    right: 8,
    minWidth: 18,
    height: 18,
    borderRadius: 9,
    paddingHorizontal: spacing.xs,
    backgroundColor: colors.occupied,
    borderWidth: 2,
    borderColor: colors.backgroundSecondary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badgeText: {
    color: colors.textInverse,
    fontSize: 10,
    fontFamily: fonts.bold,
    lineHeight: 12,
  },
});
