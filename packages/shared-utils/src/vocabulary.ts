import { StaffRole } from './enums';

/**
 * The words a business sees for the same things, by the kind of business. The roles and screens are the same everywhere (the
 * server only knows StaffRole); only the labels change, so a dress shop sees "Sales associate" where a restaurant sees "Waiter".
 */
const SHOP_ROLES: Partial<Record<StaffRole, string>> = {
  [StaffRole.OWNER]: 'Owner',
  [StaffRole.MANAGER]: 'Store manager',
  [StaffRole.CASHIER]: 'Cashier',
  [StaffRole.CAPTAIN]: 'Floor supervisor',
  [StaffRole.WAITER]: 'Sales associate',
  [StaffRole.KITCHEN]: 'Stockroom',
};

const ROLE_LABELS: Record<string, Partial<Record<StaffRole, string>>> = {
  apparel: SHOP_ROLES,
  retail: { ...SHOP_ROLES, [StaffRole.MANAGER]: 'Store manager' },
  grocery: { ...SHOP_ROLES, [StaffRole.WAITER]: 'Sales staff', [StaffRole.CAPTAIN]: 'Floor supervisor', [StaffRole.KITCHEN]: 'Stock keeper' },
  hardware: { ...SHOP_ROLES, [StaffRole.WAITER]: 'Counter staff', [StaffRole.KITCHEN]: 'Store keeper' },
};

const titleCase = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();

/** "WAITER" -> "Waiter" in a restaurant, "Sales associate" in a dress shop. */
export function roleLabel(role: StaffRole | string, category?: string | null): string {
  return (category && ROLE_LABELS[category]?.[role as StaffRole]) || titleCase(String(role));
}

/** The bottom-bar tab and screen names; the Menu tab is the product catalogue in a shop. */
export function catalogLabel(category?: string | null): string {
  if (category === 'apparel') return 'Products';
  if (category === 'restaurant' || category === 'cafe' || !category) return 'Menu';
  return 'Items';
}

export function staffLabel(category?: string | null): string {
  return category && category !== 'restaurant' && category !== 'cafe' ? 'Team' : 'Staff';
}

/** A restaurant or café takes orders; a shop rings up bills. */
export function isFoodBusiness(category?: string | null): boolean {
  return !category || category === 'restaurant' || category === 'cafe';
}

/** "Orders" in a restaurant or café, "Bills" in a shop. */
export function orderLabel(category?: string | null): string {
  return isFoodBusiness(category) ? 'Orders' : 'Bills';
}
