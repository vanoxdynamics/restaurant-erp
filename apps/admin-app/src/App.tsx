import React, { useState, useEffect } from 'react';
import {
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  View,
  Pressable,
  Platform,
} from 'react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  useFonts,
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
} from '@expo-google-fonts/inter';
import { colors, radii, spacing, typography, fonts } from '@repo/shared-ui';
import { Icon } from './components/Icon';
import { LivePulseScreen } from './screens/LivePulse';
import { FloorObserverScreen } from './screens/FloorObserverScreen';
import { MenuBuilderScreen } from './screens/MenuBuilder';
import { VoidApprovalsScreen } from './screens/VoidApprovals';
import { MoreHubScreen, MoreSubTab } from './screens/MoreHubScreen';
import { AppStoreBottomBar, AppStoreTabKey } from './components/AppStoreBottomBar';
import { useAuthStore } from './stores/authStore';
import { SignInScreen } from './screens/SignInScreen';
import { BranchPicker } from './components/BranchPicker';
import { useVoidApprovals } from '@repo/api-client';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      refetchOnWindowFocus: true,
    },
  },
});

type TabType =
  | 'LivePulse'
  | 'FloorObserver'
  | 'MenuBuilder'
  | 'VoidApprovals'
  | 'More'
  | 'StaffPINs'
  | 'ZReports'
  | 'Settings';

export function AdminAppMain(): React.JSX.Element {
  const [activeTab, setActiveTab] = useState<TabType>('LivePulse');
  const [moreSubTab, setMoreSubTab] = useState<MoreSubTab>('StaffPINs');
  const { currentUser, activeBranchId, activeBranchName, branches } = useAuthStore();

  const { data: pendingRequests = [] } = useVoidApprovals(activeBranchId, 'pending');
  const pendingCount = pendingRequests.length;
  const [branchPickerOpen, setBranchPickerOpen] = useState(false);
  const initials = (currentUser?.full_name ?? '?')
    .split(' ')
    .map((part) => part[0])
    .slice(0, 2)
    .join('')
    .toUpperCase();

  const handleBottomTabSelect = (key: AppStoreTabKey) => {
    switch (key) {
      case 'Today':
        setActiveTab('LivePulse');
        break;
      case 'Floor':
        setActiveTab('FloorObserver');
        break;
      case 'Menu':
        setActiveTab('MenuBuilder');
        break;
      case 'Approvals':
        setActiveTab('VoidApprovals');
        break;
      case 'More':
        setActiveTab('More');
        break;
    }
  };

  const handleNavigateFromScreen = (tab: string) => {
    if (tab === 'StaffPINs' || tab === 'ZReports' || tab === 'Settings') {
      setMoreSubTab(tab as MoreSubTab);
      setActiveTab('More');
    } else {
      setActiveTab(tab as TabType);
    }
  };

  const renderActiveScreen = () => {
    switch (activeTab) {
      case 'LivePulse':
        return <LivePulseScreen onNavigateToTab={handleNavigateFromScreen} />;
      case 'FloorObserver':
        return <FloorObserverScreen />;
      case 'MenuBuilder':
        return <MenuBuilderScreen />;
      case 'VoidApprovals':
        return <VoidApprovalsScreen />;
      case 'More':
      case 'StaffPINs':
      case 'ZReports':
      case 'Settings':
        return (
          <MoreHubScreen
            activeSubTab={moreSubTab}
            onChangeSubTab={(sub) => setMoreSubTab(sub)}
          />
        );
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar barStyle="dark-content" backgroundColor={colors.surface} />

      {/* Top bar: where you are and who you are */}
      <View style={styles.topBar}>
        <Pressable
          style={{ flex: 1 }}
          onPress={() => branches.length > 1 && setBranchPickerOpen(true)}
          disabled={branches.length <= 1}
          accessibilityRole="button"
          accessibilityLabel={branches.length > 1 ? `Branch: ${activeBranchName}. Change branch` : `Branch: ${activeBranchName}`}
        >
          <Text style={styles.branchLabel}>Branch</Text>
          <View style={styles.branchRow}>
            <Text style={styles.branchName} numberOfLines={1}>{activeBranchName}</Text>
            {branches.length > 1 ? <Icon name="chevron-down" size={18} color={colors.textSecondary} /> : null}
          </View>
        </Pressable>

        <View style={styles.topRightRow}>
          <Pressable
            style={({ pressed }) => [styles.iconBtn, pressed ? { backgroundColor: colors.surfaceBorder } : null]}
            onPress={() => {
              setMoreSubTab('Settings');
              setActiveTab('More');
            }}
            accessibilityRole="button"
            accessibilityLabel="Settings"
            hitSlop={6}
          >
            <Icon name="settings" size={18} color={colors.textSecondary} />
          </Pressable>
          <View style={styles.avatar} accessibilityLabel={`Signed in as ${currentUser?.full_name ?? 'owner'}`}>
            <Text style={styles.avatarText}>{initials}</Text>
          </View>
        </View>
      </View>

      {/* Main Screen Body */}
      <View style={styles.screenContainer}>{renderActiveScreen()}</View>

      {/* iOS App Store Styled Bottom Bar */}
      <AppStoreBottomBar
        currentTab={activeTab}
        onSelectTab={handleBottomTabSelect}
        pendingApprovalsCount={pendingCount}
      />

      <BranchPicker visible={branchPickerOpen} onClose={() => setBranchPickerOpen(false)} />
    </SafeAreaView>
  );
}

export default function App(): React.JSX.Element {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
  });

  // Hold the first frame until Inter is ready so text never jumps between faces.
  // If loading fails, carry on with the system font rather than blocking the app.
  const status = useAuthStore((s) => s.status);
  const restore = useAuthStore((s) => s.restore);

  useEffect(() => {
    void restore();
  }, [restore]);

  useEffect(() => {
    // Never show one account's cached data to the next person who signs in.
    if (status === 'signed_out') queryClient.clear();
  }, [status]);

  if ((!fontsLoaded && !fontError) || status === 'restoring') {
    return <View style={styles.container} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      {status === 'signed_in' ? <AdminAppMain /> : <SignInScreen />}
    </QueryClientProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.md,
    paddingBottom: spacing.md,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.surfaceBorder,
  },
  greeting: {
    ...typography.caption,
    color: colors.textSecondary,
  },
  branchLabel: {
    ...typography.caption,
    color: colors.textMuted,
  },
  branchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  onlineDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.success,
    marginRight: spacing.sm,
  },
  branchName: {
    ...typography.h2,
    color: colors.text,
    flexShrink: 1,
  },
  topRightRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginLeft: spacing.md,
  },
  iconBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surfaceLight,
  },
  avatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
  },
  avatarText: {
    color: colors.textInverse,
    fontSize: 14,
    fontFamily: fonts.bold,
  },
  screenContainer: {
    flex: 1,
    marginBottom: Platform.OS === 'ios' ? 84 : 68, // Leave space for the bottom tab bar
  },
});
