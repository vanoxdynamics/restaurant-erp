import React, { useEffect, useState } from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import { Button, Card, colors, fonts, Input, radii, spacing, typography } from '@repo/shared-ui';
import { ApiError, useBusinessProfile, useUpdateBusinessProfile } from '@repo/api-client';

/** Logos are shrunk to this width before upload: plenty for a 58/80 mm bill, and well under the 500 KB limit. */
const LOGO_WIDTH = 480;

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong. Try again.');

/**
 * The name and logo the business prints on its bills. Owners and managers change it; the platform's registered name stays as
 * it is (it is shown for reference).
 */
export const BusinessProfileCard: React.FC<{ canEdit: boolean }> = ({ canEdit }) => {
  const profileQuery = useBusinessProfile();
  const update = useUpdateBusinessProfile();
  const profile = profileQuery.data;
  const [name, setName] = useState('');
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [picking, setPicking] = useState(false);
  const [nameSaved, setNameSaved] = useState(false);

  useEffect(() => {
    if (profile) setName(profile.display_name ?? profile.registered_name);
  }, [profile]);

  // The "Name saved" tick on the button goes away after a moment.
  useEffect(() => {
    if (!nameSaved) return;
    const timer = setTimeout(() => setNameSaved(false), 2500);
    return () => clearTimeout(timer);
  }, [nameSaved]);

  /** Saves a change; only a failure needs words, a success shows on the screen itself. */
  const run = async (change: Parameters<typeof update.mutateAsync>[0]): Promise<boolean> => {
    setMessage(null);
    try {
      await update.mutateAsync(change);
      return true;
    } catch (e) {
      setMessage({ text: errorText(e), error: true });
      return false;
    }
  };

  const saveName = () => {
    const trimmed = name.trim();
    if (trimmed.length < 2) return setMessage({ text: 'Enter a name of at least 2 letters.', error: true });
    // Typing the registered name back means "no separate name".
    void run({ displayName: trimmed === profile?.registered_name ? null : trimmed }).then((ok) => setNameSaved(ok));
  };

  const chooseLogo = async () => {
    setMessage(null);
    setPicking(true);
    try {
      const picked = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsEditing: false, quality: 1 });
      if (picked.canceled || !picked.assets[0]) return;
      const asset = picked.assets[0];
      const context = ImageManipulator.manipulate(asset.uri);
      if (asset.width > LOGO_WIDTH) context.resize({ width: LOGO_WIDTH });
      const image = await (await context.renderAsync()).saveAsync({ format: SaveFormat.PNG, base64: true });
      if (!image.base64) throw new Error('no image data');
      await run({ logoBase64: image.base64 });
    } catch (e) {
      setMessage({ text: e instanceof ApiError ? e.message : 'Could not read that image. Try a PNG or JPEG.', error: true });
    } finally {
      setPicking(false);
    }
  };

  if (!profile) return <Card style={styles.card}><Text style={styles.hint}>Loading…</Text></Card>;

  const hasLogo = Boolean(profile.logo_data_url);
  const nameChanged = name.trim() !== profile.name;

  return (
    <Card style={styles.card}>
      {/* Logo */}
      <View>
        <Text style={styles.fieldLabel}>Logo</Text>
        <View style={styles.logoBlock}>
          {hasLogo ? (
            <Image source={{ uri: profile.logo_data_url! }} style={styles.logo} resizeMode="contain" accessibilityLabel="Logo on bills" />
          ) : canEdit ? (
            <Pressable
              style={({ pressed }) => [styles.logo, styles.logoEmpty, pressed ? { backgroundColor: colors.surfaceBorder } : null]}
              onPress={chooseLogo}
              disabled={picking}
              accessibilityRole="button"
              accessibilityLabel="Add logo"
            >
              <Text style={styles.addPlus}>{picking ? '…' : '+'}</Text>
              <Text style={styles.addText}>Add logo</Text>
            </Pressable>
          ) : (
            <View style={[styles.logo, styles.logoEmpty]}>
              <Text style={styles.hint}>No logo</Text>
            </View>
          )}

          {canEdit && hasLogo ? (
            <View style={styles.logoActions}>
              <Button title="Change logo" size="sm" variant="secondary" onPress={chooseLogo} loading={picking} />
              <Button title="Remove" size="sm" variant="ghost" onPress={() => void run({ logoBase64: null })}
                textStyle={{ color: colors.danger }} />
            </View>
          ) : null}
          <Text style={styles.hint}>
            {canEdit
              ? 'Best: a square or wide picture on a white background. It prints in black and white.'
              : 'Owners and managers can change the logo.'}
          </Text>
        </View>
      </View>

      {/* Name */}
      {canEdit ? (
        <View style={{ gap: spacing.xs }}>
          <Input label="Business name" value={name} onChangeText={setName} maxLength={150} />
          <Text style={styles.hint}>* Printed at the top of every bill, under the logo.</Text>
        </View>
      ) : (
        <View>
          <Text style={styles.fieldLabel}>Business name</Text>
          <Text style={styles.name}>{profile.name}</Text>
          <Text style={styles.hint}>Owners and managers can change the name.</Text>
        </View>
      )}

      {canEdit ? (
        <Button title={nameSaved ? '✓ Name saved' : 'Save name'} onPress={saveName}
          loading={update.isPending && update.variables?.displayName !== undefined}
          disabled={!nameChanged || nameSaved} />
      ) : null}

      {message ? <Text style={styles.message}>{message.text}</Text> : null}
    </Card>
  );
};

const styles = StyleSheet.create({
  card: { gap: spacing.lg },
  fieldLabel: { ...typography.caption, color: colors.text, fontFamily: fonts.semibold, marginBottom: spacing.xs },
  logoBlock: { alignItems: 'flex-start', gap: spacing.md },
  logo: { width: 96, height: 96, borderRadius: radii.md, backgroundColor: colors.surfaceLight },
  logoEmpty: {
    alignItems: 'center', justifyContent: 'center', borderWidth: 1.5, borderColor: colors.primary, borderStyle: 'dashed',
  },
  logoActions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  addPlus: { fontSize: 28, lineHeight: 30, color: colors.primary, fontFamily: fonts.semibold },
  addText: { ...typography.caption, color: colors.primary, fontFamily: fonts.semibold },
  name: { ...typography.bodyBold, color: colors.text, fontFamily: fonts.semibold },
  hint: { ...typography.caption, color: colors.textSecondary },
  message: { ...typography.caption, color: colors.danger, fontFamily: fonts.semibold },
});
