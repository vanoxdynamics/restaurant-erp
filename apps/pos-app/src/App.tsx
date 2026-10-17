import React from 'react';
import { SafeAreaView, StatusBar, StyleSheet, Text, View } from 'react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient();

export default function App(): React.JSX.Element {
  return (
    <QueryClientProvider client={queryClient}>
      <SafeAreaView style={styles.container}>
        <StatusBar barStyle="light-content" backgroundColor="#0f172a" />
        <View style={styles.content}>
          <Text style={styles.badge}>POS & CAPTAIN CLIENT</Text>
          <Text style={styles.title}>Restaurant ERP POS</Text>
          <Text style={styles.subtitle}>
            Optimized for tablets and captain handheld terminals.
          </Text>
          <View style={styles.statusBox}>
            <Text style={styles.statusText}>API Status: Ready to sync</Text>
          </View>
        </View>
      </SafeAreaView>
    </QueryClientProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0f172a',
  },
  content: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  badge: {
    fontSize: 12,
    fontWeight: '700',
    color: '#38bdf8',
    letterSpacing: 1.5,
    marginBottom: 8,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#f8fafc',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 15,
    color: '#94a3b8',
    textAlign: 'center',
    maxWidth: 360,
    marginBottom: 24,
  },
  statusBox: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: '#1e293b',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#334155',
  },
  statusText: {
    color: '#34d399',
    fontSize: 14,
    fontWeight: '500',
  },
});
