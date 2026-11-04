import React from 'react';
import { StyleSheet, Text } from 'react-native';
import { Button, colors, Modal, spacing, typography } from '@repo/shared-ui';
import { formatPaiseToRupees } from '@repo/shared-utils';
import { useSimulator } from '../devices/simulator';

/** Development only: plays the Pine Labs terminal's part when a payment is charged. */
export const SimulatorSheet: React.FC = () => {
  const prompt = useSimulator((s) => s.prompt);
  return (
    <Modal
      visible={prompt !== null}
      onClose={() => prompt?.decide('decline')}
      title="Simulated Pine Labs terminal"
      subtitle={prompt ? `${prompt.kind.toUpperCase()} · ${formatPaiseToRupees(prompt.amountPaise)} · ref ${prompt.billingRef}` : ''}
      footer={
        <>
          <Button title="Decline" variant="secondary" onPress={() => prompt?.decide('decline')} />
          <Button title="No answer" variant="ghost" onPress={() => prompt?.decide('no_answer')} />
          <Button title="Approve" variant="success" onPress={() => prompt?.decide('approve')} />
        </>
      }
    >
      <Text style={styles.text}>
        On a real device the guest would tap, insert or swipe the card (or pay by UPI) on the Pine Labs terminal now.
        "No answer" takes the money but loses the reply, to try the recovery path.
      </Text>
    </Modal>
  );
};

const styles = StyleSheet.create({
  text: { ...typography.body, color: colors.textSecondary, marginBottom: spacing.sm },
});
