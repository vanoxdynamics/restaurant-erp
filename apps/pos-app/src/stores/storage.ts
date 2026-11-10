import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

/**
 * Small key/value store: Keychain / Keystore on devices, localStorage in the web preview.
 * Failures read as "nothing stored" so a broken store never blocks the till.
 */
export const storage = {
  async get<T>(key: string): Promise<T | null> {
    try {
      const raw = Platform.OS === 'web' ? globalThis.localStorage?.getItem(key) ?? null : await SecureStore.getItemAsync(key);
      return raw ? (JSON.parse(raw) as T) : null;
    } catch {
      return null;
    }
  },
  async set(key: string, value: unknown): Promise<void> {
    const raw = JSON.stringify(value);
    if (Platform.OS === 'web') globalThis.localStorage?.setItem(key, raw);
    else await SecureStore.setItemAsync(key, raw);
  },
  /** Like `set`, for data that is only a convenience (a cache): a failure is ignored. */
  async trySet(key: string, value: unknown): Promise<void> {
    try {
      await this.set(key, value);
    } catch {
      // nothing: the next successful load writes it again
    }
  },
  async remove(key: string): Promise<void> {
    if (Platform.OS === 'web') globalThis.localStorage?.removeItem(key);
    else await SecureStore.deleteItemAsync(key);
  },
};
