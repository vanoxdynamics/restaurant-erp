import { Platform } from 'react-native';
import { pineLabsNativeAvailable } from './devices/pinelabsNative';

/**
 * What this build of the POS app is for. One code base, two builds:
 *
 * - `waiter`: staff phones from the Play Store. No printer and no card machine: KOTs print at the counter and payment
 *   happens there.
 * - `counter`: the restaurant's one Pine Labs device at the counter (also the default for the browser preview). It prints
 *   every KOT and bill, takes card/UPI through Pine Labs, and is the branch's print station.
 *
 * The variant only says what this device can do. What a restaurant may use is still decided by its plan and the signed-in
 * person's role, on the server.
 */
export type AppVariant = 'waiter' | 'counter' | 'retail';

const variantSetting = (process.env.EXPO_PUBLIC_APP_VARIANT ?? 'counter').toLowerCase();
export const APP_VARIANT: AppVariant = variantSetting.startsWith('waiter')
  ? 'waiter'
  : variantSetting.startsWith('retail')
    ? 'retail'
    : 'counter';
/**
 * The device at the counter: it prints and takes payment. The restaurant counter and the retail app are both counter devices;
 * `retail` is a separate app for cafés and shops (quick sale), kept apart from the restaurant apps (tables, waiters, KOTs).
 */
export const IS_COUNTER = APP_VARIANT !== 'waiter';
export const IS_RETAIL = APP_VARIANT === 'retail';

/** Development only: pretend to be a Pine Labs terminal (payments and printer) in the browser or an emulator. */
const SIMULATOR = process.env.EXPO_PUBLIC_PINELABS_SIMULATOR === '1';

/**
 * - `pinelabs`: the Pine Labs device's built-in printer (reports success or the printer's error).
 * - `simulator`: the development stand-in for it.
 * - `system`: the browser's or the OS print dialog (cannot tell whether the paper came out, so staff confirm).
 * - `none`: this device does not print (waiters' phones).
 */
export type PrinterKind = 'pinelabs' | 'simulator' | 'system' | 'none';

/** - `pinelabs` / `simulator`: card and UPI are charged on the terminal. - `manual`: the cashier keys in the reference. */
export type PaymentKind = 'pinelabs' | 'simulator' | 'manual';

export function printerKind(): PrinterKind {
  if (!IS_COUNTER) return 'none';
  if (pineLabsNativeAvailable()) return 'pinelabs';
  if (SIMULATOR) return 'simulator';
  return 'system';
}

export function paymentKind(): PaymentKind {
  if (!IS_COUNTER) return 'manual';
  if (pineLabsNativeAvailable()) return 'pinelabs';
  if (SIMULATOR) return 'simulator';
  return 'manual';
}

/** A printer that reports its own result can print unattended (the counter's print queue). */
export const printsUnattended = () => printerKind() === 'pinelabs' || printerKind() === 'simulator';

/** Handhelds print on 58 mm rolls; counter printers on browsers and tablets are usually 80 mm. */
export const defaultPaperMm = (): 58 | 80 => (printerKind() === 'pinelabs' || Platform.OS !== 'web' ? 58 : 80);
