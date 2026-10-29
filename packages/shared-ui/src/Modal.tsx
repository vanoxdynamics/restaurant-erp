import React from 'react';
import {
  Modal as RNModal,
  View,
  Text,
  Pressable,
  StyleSheet,
  ViewStyle,
  ScrollView,
  useWindowDimensions,
} from 'react-native';
import { colors, radii, shadows, spacing, typography, fonts } from './theme';

export interface ModalProps {
  visible: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  contentStyle?: ViewStyle;
}

/**
 * Bottom sheet on phones (thumb-reachable), centered dialog on tablets.
 */
export const Modal: React.FC<ModalProps> = ({
  visible,
  onClose,
  title,
  subtitle,
  children,
  footer,
  contentStyle,
}) => {
  const { width } = useWindowDimensions();
  const asSheet = width < 600;

  return (
    <RNModal
      visible={visible}
      transparent
      animationType={asSheet ? 'slide' : 'fade'}
      onRequestClose={onClose}
    >
      <View style={[styles.overlay, asSheet ? styles.overlaySheet : styles.overlayDialog]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} accessibilityLabel="Close" />
        <View style={[styles.dialog, asSheet ? styles.sheet : styles.centered, contentStyle]}>
          {asSheet ? <View style={styles.grabber} /> : null}
          <View style={styles.header}>
            <View style={{ flex: 1 }}>
              <Text style={[typography.h2, { color: colors.text }]}>{title}</Text>
              {subtitle ? (
                <Text style={[typography.caption, { color: colors.textSecondary, marginTop: 2 }]}>
                  {subtitle}
                </Text>
              ) : null}
            </View>
            <Pressable
              onPress={onClose}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel="Close"
              style={({ pressed }) => [styles.closeBtn, pressed ? { backgroundColor: colors.surfaceBorder } : null]}
            >
              <Text style={styles.closeGlyph}>×</Text>
            </Pressable>
          </View>

          <ScrollView style={styles.body} contentContainerStyle={{ paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled">
            {children}
          </ScrollView>

          {footer ? <View style={styles.footer}>{footer}</View> : null}
        </View>
      </View>
    </RNModal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: colors.overlay,
  },
  overlaySheet: {
    justifyContent: 'flex-end',
  },
  overlayDialog: {
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.xxl,
  },
  dialog: {
    backgroundColor: colors.surface,
    overflow: 'hidden',
    ...shadows.raised,
  },
  sheet: {
    width: '100%',
    maxHeight: '92%',
    borderTopLeftRadius: radii.xl,
    borderTopRightRadius: radii.xl,
  },
  centered: {
    width: '100%',
    maxWidth: 520,
    maxHeight: '90%',
    borderRadius: radii.xl,
  },
  grabber: {
    alignSelf: 'center',
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.surfaceBorder,
    marginTop: spacing.sm,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.lg,
    paddingBottom: spacing.sm,
  },
  closeBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.surfaceLight,
    alignItems: 'center',
    justifyContent: 'center',
    marginLeft: spacing.md,
  },
  closeGlyph: {
    color: colors.textSecondary,
    fontSize: 20,
    lineHeight: 22,
    fontFamily: fonts.medium,
  },
  body: {
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.sm,
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: spacing.md,
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.md,
    paddingBottom: spacing.xl,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceBorder,
  },
});
