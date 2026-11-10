import React, { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  useFonts,
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
} from '@expo-google-fonts/inter';
import { colors } from '@repo/shared-ui';
import { StaffRole } from '@repo/shared-utils';
import { DeviceSetupScreen } from './screens/DeviceSetupScreen';
import { SignInScreen } from './screens/SignInScreen';
import { FloorScreen } from './screens/FloorScreen';
import { OrderScreen } from './screens/OrderScreen';
import { BillScreen } from './screens/BillScreen';
import { KitchenScreen } from './screens/KitchenScreen';
import { CounterHomeScreen } from './screens/CounterHomeScreen';
import { KotPrinterScreen } from './screens/KotPrinterScreen';
import { DayEndScreen } from './screens/DayEndScreen';
import { SimulatorSheet } from './components/SimulatorSheet';
import { IS_COUNTER, IS_RETAIL, paymentKind } from './device';
import { RetailMain } from './retail/RetailApp';
import { WrongApp } from './components/WrongApp';
import { ReadyAlerts } from './components/ReadyAlerts';
import { useBusiness } from './business';
import { PrintStation } from './printing/usePrintStation';
import { usePrinterSettings } from './stores/printerStore';
import { useDeviceStore } from './stores/deviceStore';
import { IDLE_LOCK_MS, useSessionStore } from './stores/sessionStore';
import { useCartStore } from './stores/cartStore';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5000, retry: 1 } },
});

export type Route =
  | { name: 'home' }
  | { name: 'floor' }
  | { name: 'order'; orderId: string; title: string }
  | { name: 'bill'; orderId: string; title: string }
  | { name: 'printer' }
  | { name: 'dayend' };

/** The counter device starts on the bills to collect; a waiter's phone on the tables. */
const START: Route = IS_COUNTER ? { name: 'home' } : { name: 'floor' };

function Main(): React.JSX.Element {
  if (IS_RETAIL) return <RetailMain />;
  return <RestaurantMain />;
}

/** The restaurant apps (counter and waiter): tables, waiters, KOTs. Shops and cafés use the retail app instead. */
function RestaurantMain(): React.JSX.Element {
  const [route, setRoute] = useState<Route>(START);
  const role = useSessionStore((s) => s.user?.role);
  const business = useBusiness();

  if (business.loaded && business.category !== 'restaurant') return <WrongApp expected="retail" />;

  // Kitchen staff only handle the kitchen's own queue; they never see the floor.
  if (role === StaffRole.KITCHEN) return <KitchenScreen />;

  const home = () => setRoute(START);
  const openOrder = (orderId: string, title: string) => setRoute({ name: 'order', orderId, title });

  const screen = (() => {
    if (route.name === 'bill') {
      return (
        <BillScreen
          orderId={route.orderId}
          title={route.title}
          onBack={() => openOrder(route.orderId, route.title)}
          onDone={home}
        />
      );
    }
    if (route.name === 'order') {
      return (
        <OrderScreen
          orderId={route.orderId}
          title={route.title}
          onBack={home}
          onBill={() => setRoute({ name: 'bill', orderId: route.orderId, title: route.title })}
        />
      );
    }
    if (route.name === 'printer') return <KotPrinterScreen onBack={home} />;
    if (route.name === 'dayend') return <DayEndScreen onBack={home} />;
    if (route.name === 'home') {
      return (
        <CounterHomeScreen
          onOpenOrder={openOrder}
          onOpenBill={(orderId, title) => setRoute({ name: 'bill', orderId, title })}
          onNewOrder={() => setRoute({ name: 'floor' })}
          onPrinter={() => setRoute({ name: 'printer' })}
          onDayEnd={() => setRoute({ name: 'dayend' })}
        />
      );
    }
    return <FloorScreen onOpenOrder={openOrder} onBack={IS_COUNTER ? home : undefined} />;

  })();

  // Floor staff hear about food ready in the kitchen on whatever screen they are on.
  return (
    <View style={{ flex: 1 }}>
      {screen}
      <ReadyAlerts onOpenOrder={openOrder} />
    </View>
  );
}

export default function App(): React.JSX.Element {
  const [fontsLoaded, fontError] = useFonts({ Inter_400Regular, Inter_500Medium, Inter_600SemiBold, Inter_700Bold });
  const deviceStatus = useDeviceStore((s) => s.status);
  const loadDevice = useDeviceStore((s) => s.load);
  const user = useSessionStore((s) => s.user);
  const touch = useSessionStore((s) => s.touch);

  useEffect(() => {
    void loadDevice();
    void usePrinterSettings.getState().load();
  }, [loadDevice]);

  // Lock after a period without touches; clear unsent carts and cached data with it.
  useEffect(() => {
    if (!user) return;
    // A kitchen device is left running on the pass, so it must not lock out between requests.
    if (user.role === StaffRole.KITCHEN) return;
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
    <SafeAreaProvider>
    <QueryClientProvider client={queryClient}>
      {/* Capture (and immediately release) every press so touch, mouse and pen all count as activity. */}
      <SafeAreaView
        style={styles.container}
        onStartShouldSetResponderCapture={() => {
          touch();
          return false;
        }}
      >
        <StatusBar style="dark" />
        {deviceStatus === 'unregistered' ? <DeviceSetupScreen /> : user ? <Main /> : <SignInScreen />}
        {paymentKind() === 'simulator' ? <SimulatorSheet /> : null}
        {/* The counter prints waiters' KOTs on its own credential, signed in or locked. */}
        {deviceStatus === 'registered' ? <PrintStation /> : null}
      </SafeAreaView>
    </QueryClientProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
});
