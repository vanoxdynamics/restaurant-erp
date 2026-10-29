import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { Button, colors, spacing, typography } from '@repo/shared-ui';

interface QueryLike {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

export interface QueryStateProps {
  query: QueryLike;
  isEmpty?: boolean;
  emptyText?: string;
  children: React.ReactNode;
}

/** Loading, error and empty states for a query, so screens never show made-up numbers. */
export const QueryState: React.FC<QueryStateProps> = ({ query, isEmpty, emptyText = 'Nothing to show.', children }) => {
  if (query.isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }
  if (query.isError) {
    const message = query.error instanceof Error ? query.error.message : 'Could not load data.';
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{message}</Text>
        <Button title="Retry" variant="secondary" size="sm" onPress={() => void query.refetch()} />
      </View>
    );
  }
  if (isEmpty) {
    return (
      <View style={styles.center}>
        <Text style={styles.empty}>{emptyText}</Text>
      </View>
    );
  }
  return <>{children}</>;
};

const styles = StyleSheet.create({
  center: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: spacing.xxxl,
    gap: spacing.md,
  },
  error: {
    ...typography.body,
    color: colors.danger,
    textAlign: 'center',
  },
  empty: {
    ...typography.body,
    color: colors.textSecondary,
    textAlign: 'center',
  },
});
