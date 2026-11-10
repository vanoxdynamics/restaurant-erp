import { BusinessCategory, BusinessSettings, ItemUnit, RESTAURANT_SETTINGS, useEntitlements } from '@repo/api-client';

/**
 * What kind of business this is and which workflow steps it uses. The category (restaurant, café, grocery, retail) is set by the
 * platform and presets the settings; the server enforces the rules, the app only picks screens and words. Until the plan has
 * loaded the app behaves like a restaurant, which is what every business was before categories existed.
 */
export function useBusiness() {
  const entitlements = useEntitlements();
  const category: BusinessCategory = entitlements.data?.category ?? 'restaurant';
  const settings: BusinessSettings = entitlements.data?.settings ?? RESTAURANT_SETTINGS;
  const restaurant = category === 'restaurant';
  return {
    loaded: !!entitlements.data,
    /** The plan has loaded, or failed to (then the restaurant defaults apply). */
    ready: !entitlements.isLoading,
    category,
    settings,
    /** Words that differ between a restaurant and a shop. */
    words: {
      items: restaurant || category === 'cafe' ? 'Menu' : 'Items',
      searchPlaceholder: settings.barcode ? 'Search or scan a barcode' : restaurant || category === 'cafe' ? 'Search the menu' : 'Search items',
    },
  };
}

/** 0.75 -> "0.75", 2 -> "2"; with a unit other than pieces: "0.75 kg". */
export function formatQuantity(quantity: number, unit: ItemUnit = 'pcs'): string {
  const text = String(Math.round(quantity * 1000) / 1000);
  return unit === 'pcs' ? text : `${text} ${unit}`;
}

/** Quantities are kept to 3 decimals (grams of a kilo), the same as the server. */
export const roundQuantity = (q: number) => Math.round(q * 1000) / 1000;
