import React, { useEffect, useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
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

  useEffect(() => {
    if (profile) setName(profile.display_name ?? profile.registered_name);
  }, [profile]);

  const run = async (change: Parameters<typeof update.mutateAsync>[0], done: string) => {
    setMessage(null);
    try {
      await update.mutateAsync(change);
      setMessage({ text: done });
    } catch (e) {
      setMessage({ text: errorText(e), error: true });
    }
  };

  const saveName = () => {
    const trimmed = name.trim();
    if (trimmed.length < 2) return setMessage({ text: 'Enter a name of at least 2 letters.', error: true });
    // Typing the registered name back means "no separate name".
    void run({ displayName: trimmed === profile?.registered_name ? null : trimmed }, 'Name saved. New bills print it.');
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
      await run({ logoBase64: image.base64 }, 'Logo saved. New bills print it.');
    } catch (e) {
      setMessage({ text: e instanceof ApiError ? e.message : 'Could not read that image. Try a PNG or JPEG.', error: true });
    } finally {
      setPicking(false);
    }
  };

  if (!profile) return <Card style={styles.card}><Text style={styles.sub}>Loading…</Text></Card>;

  return (
    <Card style={styles.card}>
      <View style={styles.logoRow}>
        {profile.logo_data_url ? (
          <Image source={{ uri: profile.logo_data_url }} style={styles.logo} resizeMode="contain" accessibilityLabel="Logo on bills" />
        ) : (
          <View style={[styles.logo, styles.logoEmpty]}>
            <Text style={styles.sub}>No logo</Text>
          </View>
        )}
        <View style={{ flex: 1, gap: spacing.xs }}>
          <Text style={styles.name}>{profile.name}</Text>
          <Text style={styles.sub}>Printed at the top of every bill.</Text>
        </View>
      </View>

      {canEdit ? (
        <>
          <Input label="Name on bills" value={name} onChangeText={setName} maxLength={150} />
          {profile.display_name ? <Text style={styles.sub}>Registered as {profile.registered_name}.</Text> : null}
          <View style={styles.actions}>
            <Button title="Save name" size="sm" onPress={saveName} loading={update.isPending && update.variables?.displayName !== undefined}
              disabled={name.trim() === profile.name} />
            <Button title={profile.logo_data_url ? 'Change logo' : 'Add logo'} size="sm" variant="secondary" onPress={chooseLogo}
              loading={picking} />
            {profile.logo_data_url ? (
              <Button title="Remove logo" size="sm" variant="ghost" onPress={() => void run({ logoBase64: null }, 'Logo removed.')}
                textStyle={{ color: colors.danger }} />
            ) : null}
          </View>
          <Text style={styles.sub}>Use a square or wide logo on a white background; it prints in black and white.</Text>
        </>
      ) : (
        <Text style={styles.sub}>Owners and managers can change the name and logo.</Text>
      )}
      {message ? <Text style={[styles.message, message.error ? { color: colors.danger } : null]}>{message.text}</Text> : null}
    </Card>
  );
};

const styles = StyleSheet.create({
  card: { gap: spacing.md },
  logoRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  logo: { width: 72, height: 72, borderRadius: radii.md, backgroundColor: colors.surfaceLight },
  logoEmpty: { alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.surfaceBorder, borderStyle: 'dashed' },
  name: { ...typography.bodyBold, color: colors.text, fontFamily: fonts.semibold },
  sub: { ...typography.caption, color: colors.textSecondary },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  message: { ...typography.caption, color: colors.success, fontFamily: fonts.semibold },
});
