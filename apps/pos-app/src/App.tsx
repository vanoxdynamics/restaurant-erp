import React, { useEffect, useState } from 'react';
import { SafeAreaView, StatusBar, StyleSheet, View } from 'react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  useFonts,
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
} from '@expo-google-fonts/inter';
import { colors } from '@repo/shared-ui';
import { DeviceSetupScreen } from './screens/DeviceSetupScreen';
import { PinScreen } from './screens/PinScreen';
import { FloorScreen } from './screens/FloorScreen';
import { OrderScreen } from './screens/OrderScreen';
import { useDeviceStore } from './stores/deviceStore';
import { IDLE_LOCK_MS, useSessionStore } from './stores/sessionStore';
import { useCartStore } from './stores/cartStore';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5000, retry: 1 } },
});

export type Route = { name: 'floor' } | { name: 'order'; orderId: string; title: string };

function Main(): React.JSX.Element {
  const [route, setRoute] = useState<Route>({ name: 'floor' });

  if (route.name === 'order') {
    return <OrderScreen orderId={route.orderId} title={route.title} onBack={() => setRoute({ name: 'floor' })} />;
  }
  return <FloorScreen onOpenOrder={(orderId, title) => setRoute({ name: 'order', orderId, title })} />;
}

export default function App(): React.JSX.Element {
  const [fontsLoaded, fontError] = useFonts({ Inter_400Regular, Inter_500Medium, Inter_600SemiBold, Inter_700Bold });
  const deviceStatus = useDeviceStore((s) => s.status);
  const loadDevice = useDeviceStore((s) => s.load);
  const user = useSessionStore((s) => s.user);
  const touch = useSessionStore((s) => s.touch);

  useEffect(() => {
    void loadDevice();
  }, [loadDevice]);

  // Lock after a period without touches; clear unsent carts and cached data with it.
  useEffect(() => {
    if (!user) return;
    const timer = setInterval(() => {
      if (Date.now() - useSessionStore.getState().lastActivity > IDLE_LOCK_MS) useSessionStore.getState().lock();
    }, 15000);
    return () => clearInterval(timer);
  }, [user]);

  useEffect(() => {
    if (!user) {
      useCartStore.getState().clearAll();
      queryClient.clear();
    }
  }, [user]);

  if ((!fontsLoaded && !fontError) || deviceStatus === 'loading') {
    return <View style={styles.container} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      {/* Capture (and immediately release) every press so touch, mouse and pen all count as activity. */}
      <SafeAreaView
        style={styles.container}
        onStartShouldSetResponderCapture={() => {
          touch();
          return false;
        }}
      >
        <StatusBar barStyle="dark-content" backgroundColor={colors.surface} />
        {deviceStatus === 'unregistered' ? <DeviceSetupScreen /> : user ? <Main /> : <PinScreen />}
      </SafeAreaView>
    </QueryClientProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
});
