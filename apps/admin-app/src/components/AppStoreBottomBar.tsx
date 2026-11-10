import React from 'react';
import { View, Text, Pressable, StyleSheet, Platform } from 'react-native';
import { colors, spacing, fonts } from '@repo/shared-ui';
import { Icon, IconName } from './Icon';

export type AppStoreTabKey = 'Today' | 'Insights' | 'Menu' | 'Staff' | 'Approvals';

export interface AppStoreBottomBarProps {
  currentTab: string; // the active screen, e.g. 'LivePulse', 'Insights', 'StaffPINs', 'VoidApprovals'
  onSelectTab: (tab: AppStoreTabKey) => void;
  pendingApprovalsCount?: number;
  /**
   * Only businesses with the kitchen display get cancel requests to approve (without it a KOT is cancelled at the till with a
   * manager's PIN), so only they get the Approvals tab.
   */
  showApprovals?: boolean;
}

type Tab = { key: AppStoreTabKey; label: string; icon: IconName; matches: string[] };

// Settings and Profile open from the top bar (gear and avatar); the floor from Today's tables card.
const TABS: Tab[] = [
  { key: 'Today', label: 'Today', icon: 'sun', matches: ['LivePulse', 'FloorObserver'] },
  { key: 'Insights', label: 'Insights', icon: 'bar-chart-2', matches: ['Insights'] },
  { key: 'Menu', label: 'Menu', icon: 'book-open', matches: ['MenuBuilder'] },
  { key: 'Staff', label: 'Staff', icon: 'users', matches: ['StaffPINs'] },
  { key: 'Approvals', label: 'Approvals', icon: 'check-circle', matches: ['VoidApprovals'] },
];

const tabsFor = (showApprovals: boolean) => (showApprovals ? TABS : TABS.filter((t) => t.key !== 'Approvals'));

export const AppStoreBottomBar: React.FC<AppStoreBottomBarProps> = ({
  currentTab,
  onSelectTab,
  pendingApprovalsCount = 0,
  showApprovals = true,
}) => (
  <View style={styles.bar} accessibilityRole="tablist">
    {tabsFor(showApprovals).map((tab) => {
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
