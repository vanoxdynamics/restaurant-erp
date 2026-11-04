import { StaffRole } from '@repo/shared-utils';
import { useEntitlements } from '@repo/api-client';

const BILLING_ROLES: StaffRole[] = [StaffRole.OWNER, StaffRole.MANAGER, StaffRole.CASHIER];

/**
 * Which kitchen workflow this restaurant runs. With the kitchen display (`kds`) the kitchen works from its screen; without
 * it orders go out as printed slips, the floor marks them served and waiters may take payment. Until the plan has loaded
 * the app assumes the screen, which is the stricter of the two (the server decides either way).
 */
export function useKitchenPlan() {
  const entitlements = useEntitlements();
  const slip = entitlements.data ? !entitlements.data.modules.includes('kds') : false;
  return {
    slip,
    canBill: (role?: StaffRole) => !!role && (BILLING_ROLES.includes(role) || (slip && role === StaffRole.WAITER)),
  };
}
