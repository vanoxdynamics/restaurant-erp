import { StaffRole } from '@repo/shared-utils';
import { useEntitlements } from '@repo/api-client';
import { IS_COUNTER } from './device';

const BILLING_ROLES: StaffRole[] = [StaffRole.OWNER, StaffRole.MANAGER, StaffRole.CASHIER];

/**
 * Which kitchen workflow this restaurant runs. With the kitchen display (`kds`) the kitchen works from its screen; without
 * it orders go out as printed KOTs and the floor marks them served. Until the plan has loaded the app assumes the screen,
 * which is the stricter of the two (the server decides either way).
 *
 * Payment happens at the counter: on a plan without the kitchen display a waiter may bill there (a small place with no cashier),
 * but never from a phone, which has no card machine and no printer.
 */
export function useKitchenPlan() {
  const entitlements = useEntitlements();
  const slip = entitlements.data ? !entitlements.data.modules.includes('kds') : false;
  return {
    slip,
    canBill: (role?: StaffRole) => !!role && (BILLING_ROLES.includes(role) || (IS_COUNTER && slip && role === StaffRole.WAITER)),
  };
}
