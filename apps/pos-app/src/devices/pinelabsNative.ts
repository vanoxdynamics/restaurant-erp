import { Platform } from 'react-native';
import { requireOptionalNativeModule } from 'expo';

/** The Kotlin module in `modules/pinelabs`: sends one JSON request to Pine Labs' Plutus app and returns its JSON answer. */
interface PineLabsNativeModule {
  isAvailable(): boolean;
  request(json: string): Promise<string>;
}

const native: PineLabsNativeModule | null = Platform.OS === 'android' ? requireOptionalNativeModule<PineLabsNativeModule>('PineLabs') : null;

/** True on a Pine Labs device with the Plutus app installed, in a build that includes our module. */
export function pineLabsNativeAvailable(): boolean {
  try {
    return !!native && native.isAvailable();
  } catch {
    return false;
  }
}

export async function pineLabsNativeRequest(json: string): Promise<string> {
  if (!native) throw new Error('This build cannot talk to a Pine Labs terminal.');
  return native.request(json);
}
