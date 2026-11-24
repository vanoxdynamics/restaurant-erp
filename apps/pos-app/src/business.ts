import { useEffect, useState } from 'react';
import { storage } from './stores/storage';
import { catalogLabel } from '@repo/shared-utils';
import { BusinessCategory, BusinessSettings, ItemUnit, RESTAURANT_SETTINGS, useEntitlements } from '@repo/api-client';

const CATEGORY_KEY = 'business.category';

/**
 * What kind of business this is and which workflow steps it uses. The category (restaurant, café, grocery, retail) is set by the
 * platform and presets the settings; the server enforces the rules, the app only picks screens and words. Until the plan has
 * loaded the app behaves like a restaurant, which is what every business was before categories existed.
 */
/** Keep the category on this device (the sign-in roster carries it), so screens before sign-in use the business's words. */
export function rememberCategory(category: string | undefined): void {
  if (category) void storage.trySet(CATEGORY_KEY, category);
}

export function useBusiness() {
  const entitlements = useEntitlements();
  // The plan needs a signed-in session, so before sign-in the last known category (kept on this device) names the roles.
  const [remembered, setRemembered] = useState<BusinessCategory | null>(null);
  useEffect(() => {
    void storage.get<BusinessCategory>(CATEGORY_KEY).then(setRemembered);
  }, []);
  const loadedCategory = entitlements.data?.category;
  useEffect(() => {
    if (loadedCategory) void storage.trySet(CATEGORY_KEY, loadedCategory);
  }, [loadedCategory]);
  const category: BusinessCategory = loadedCategory ?? remembered ?? 'restaurant';
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
      items: catalogLabel(category),
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
