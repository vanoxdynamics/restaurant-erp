import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { StaffRole, KitchenStation } from '@repo/shared-utils';
import { ApiError, apiClient } from './client';
import {
  Bill,
  BoardTicket,
  BranchSummary,
  PaymentInput,
  PaymentOutcome,
  BillDiscount,
  CartLine,
  BusinessOverview,
  BusinessProfile,
  CashDay,
  OverviewPeriod,
  CashShift,
  Entitlements,
  KitchenTicket,
  TerminalOutcome,
  OrderDetail,
  Category,
  Floor,
  FraudAuditLog,
  LiveKPIs,
  MenuItemUpdate,
  MenuItemCreate,
  PaymentBreakdown,
  RosterEntry,
  ShiftClosureAudit,
  StaffUser,
  VoidApprovalItem,
  ZReport,
} from './types';

export const QUERY_KEYS = {
  branches: ['branches'] as const,
  floorLayout: (branchId: string) => ['floorLayout', branchId] as const,
  catalog: ['catalog'] as const,
  voidRequests: (branchId: string, status?: string) => ['voidRequests', branchId, status ?? 'all'] as const,
  auditLog: (branchId: string) => ['auditLog', branchId] as const,
  staff: (branchId: string) => ['staff', branchId] as const,
  shifts: (branchId: string) => ['shifts', branchId] as const,
  livePulse: (branchId: string) => ['livePulse', branchId] as const,
  todayPayments: (branchId: string) => ['todayPayments', branchId] as const,
  zReport: (branchId: string, date?: string) => ['zReport', branchId, date ?? 'today'] as const,
};

// ---- Sign-in -----------------------------------------------------------------------

/**
 * Who can sign in on this tablet. Re-fetched each time the picker is shown so new hires appear. A slow or dropped connection is
 * retried a few times on its own; a refused device token (401) is not, since only linking the tablet again fixes that.
 */
export function useSignInRoster(deviceToken: string | undefined) {
  return useQuery<RosterEntry[]>({
    queryKey: ['signInRoster', deviceToken ?? ''],
    queryFn: () => apiClient.getSignInRoster(deviceToken!),
    enabled: !!deviceToken,
    staleTime: 0,
    retry: (failures, error) => !(error instanceof ApiError && error.status === 401) && failures < 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 5000),
  });
}

export function useCreateDeviceSetupCode() {
  return useMutation({ mutationFn: (branchId?: string) => apiClient.createDeviceSetupCode(branchId) });
}

// ---- Subscription ----------------------------------------------------------------

/** The organization's modules. Re-checked every minute so a plan change reaches a running tablet mid-shift. */
export function useEntitlements(enabled = true) {
  return useQuery<Entitlements>({
    queryKey: ['entitlements'],
    queryFn: () => apiClient.getEntitlements(),
    enabled,
    refetchInterval: 60000,
  });
}

// ---- Branches --------------------------------------------------------------------

export function useBranches(enabled = true) {
  return useQuery<BranchSummary[]>({
    queryKey: QUERY_KEYS.branches,
    queryFn: () => apiClient.getBranches(),
    enabled,
  });
}

// ---- Floor -------------------------------------------------------------------------

export function useFloorLayout(branchId: string) {
  return useQuery<Floor[]>({
    queryKey: QUERY_KEYS.floorLayout(branchId),
    queryFn: () => apiClient.getFloorLayout(branchId),
    enabled: !!branchId,
    refetchInterval: 15000,
  });
}

// ---- Catalog -------------------------------------------------------------------------

export function useCatalog() {
  return useQuery<Category[]>({
    queryKey: QUERY_KEYS.catalog,
    queryFn: () => apiClient.getCatalog(),
  });
}

export function useToggle86() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, isAvailable }: { itemId: string; isAvailable: boolean }) =>
      apiClient.toggleItem86(itemId, isAvailable),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog }),
  });
}

export function useCreateMenuItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (item: MenuItemCreate) => apiClient.createMenuItem(item),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog }),
  });
}

export function useImportItems() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ csv, dryRun }: { csv: string; dryRun?: boolean }) => apiClient.importItems(csv, dryRun),
    onSuccess: (result) => {
      if (!result.dry_run) queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog });
    },
  });
}

export function useUpdateMenuItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, changes }: { itemId: string; changes: MenuItemUpdate }) =>
      apiClient.updateMenuItem(itemId, changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog }),
  });
}

export function useCreateCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (category: { name: string; sort_order?: number }) => apiClient.createCategory(category),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog }),
  });
}

// ---- Void approvals -----------------------------------------------------------------

export function useVoidApprovals(branchId: string, status?: 'pending' | 'approved' | 'rejected', enabled = true) {
  return useQuery<VoidApprovalItem[]>({
    queryKey: QUERY_KEYS.voidRequests(branchId, status),
    queryFn: () => apiClient.getVoidRequests(branchId, status),
    enabled: enabled && !!branchId,
    refetchInterval: status === 'pending' ? 10000 : undefined,
  });
}

/** The kitchen's answers to cancellation requests on an order; polls while one is still waiting. */
export function useOrderVoidRequests(orderId: string | undefined) {
  return useQuery<VoidApprovalItem[]>({
    queryKey: ['orderVoidRequests', orderId ?? ''],
    queryFn: () => apiClient.getOrderVoidRequests(orderId!),
    enabled: !!orderId,
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === 'PENDING') ? 5000 : false),
  });
}

function useReviewMutation(action: 'approve' | 'reject') {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, note }: { requestId: string; note?: string }) =>
      action === 'approve' ? apiClient.approveVoidRequest(requestId, note) : apiClient.rejectVoidRequest(requestId, note),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['voidRequests'] });
      queryClient.invalidateQueries({ queryKey: ['orderVoidRequests'] });
      queryClient.invalidateQueries({ queryKey: ['auditLog'] });
      queryClient.invalidateQueries({ queryKey: ['floorLayout'] });
      queryClient.invalidateQueries({ queryKey: ['livePulse'] });
    },
  });
}

export const useApproveVoidKOT = () => useReviewMutation('approve');
export const useRejectVoidKOT = () => useReviewMutation('reject');

// ---- Alerts (audit log) ---------------------------------------------------------------

export function useFraudAlerts(branchId: string) {
  return useQuery<FraudAuditLog[]>({
    queryKey: QUERY_KEYS.auditLog(branchId),
    queryFn: () => apiClient.getAuditLog(branchId),
    enabled: !!branchId,
    refetchInterval: 30000,
  });
}

// ---- Staff & shifts -----------------------------------------------------------------------

export function useStaffUsers(branchId: string) {
  return useQuery<StaffUser[]>({
    queryKey: QUERY_KEYS.staff(branchId),
    queryFn: () => apiClient.getStaff(branchId),
    enabled: !!branchId,
  });
}

export function useCreateStaffUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (staff: {
      full_name: string;
      username: string;
      role: StaffRole;
      pin: string;
      password?: string;
      email?: string;
      branch_id?: string;
    }) => apiClient.createStaff(staff),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['staff'] }),
  });
}

export function useUpdateStaffPIN() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, newPin }: { userId: string; newPin: string }) => apiClient.resetStaffPin(userId, newPin),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['staff'] }),
  });
}

export function useDeleteStaffUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => apiClient.deleteStaff(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['staff'] }),
  });
}

export function useSetStaffActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, isActive }: { userId: string; isActive: boolean }) =>
      apiClient.setStaffActive(userId, isActive),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['staff'] }),
  });
}

export function useShiftAudits(branchId: string) {
  return useQuery<ShiftClosureAudit[]>({
    queryKey: QUERY_KEYS.shifts(branchId),
    queryFn: () => apiClient.getShiftHistory(branchId),
    enabled: !!branchId,
  });
}

// ---- Analytics -----------------------------------------------------------------------------

export function useLivePulse(branchId: string) {
  return useQuery<LiveKPIs>({
    queryKey: QUERY_KEYS.livePulse(branchId),
    queryFn: () => apiClient.getLiveKPIs(branchId),
    enabled: !!branchId,
    refetchInterval: 15000,
  });
}

export function useTodayPayments(branchId: string) {
  return useQuery<PaymentBreakdown>({
    queryKey: QUERY_KEYS.todayPayments(branchId),
    queryFn: () => apiClient.getTodayPayments(branchId),
    enabled: !!branchId,
    refetchInterval: 30000,
  });
}

export function useZReport(branchId: string, date?: string) {
  return useQuery<ZReport>({
    queryKey: QUERY_KEYS.zReport(branchId, date),
    queryFn: () => apiClient.getZReport(branchId, date),
    enabled: !!branchId,
  });
}

// ---- Waiter: orders & kitchen tickets ---------------------------------------------------

export const BILL_KEYS = { forOrder: (orderId: string) => ['bill', orderId] as const };

export const ORDER_KEYS = {
  order: (orderId: string) => ['order', orderId] as const,
  tickets: (orderId: string) => ['orderTickets', orderId] as const,
  takeaways: (branchId: string) => ['takeaways', branchId] as const,
};

export function useOrder(orderId?: string) {
  return useQuery<OrderDetail>({
    queryKey: ORDER_KEYS.order(orderId ?? ''),
    queryFn: () => apiClient.getOrder(orderId!),
    enabled: !!orderId,
    refetchInterval: 15000,
  });
}

export function useOrderTickets(orderId?: string) {
  return useQuery<KitchenTicket[]>({
    queryKey: ORDER_KEYS.tickets(orderId ?? ''),
    queryFn: () => apiClient.getOrderTickets(orderId!),
    enabled: !!orderId,
    refetchInterval: 5000,
  });
}

export function useOpenTakeaways(branchId: string) {
  return useQuery<OrderDetail[]>({
    queryKey: ORDER_KEYS.takeaways(branchId),
    queryFn: () => apiClient.getOpenTakeaways(branchId),
    enabled: !!branchId,
    refetchInterval: 15000,
  });
}

function useInvalidateOrder() {
  const queryClient = useQueryClient();
  return (orderId?: string) => {
    if (orderId) {
      queryClient.invalidateQueries({ queryKey: ORDER_KEYS.order(orderId) });
      queryClient.invalidateQueries({ queryKey: ORDER_KEYS.tickets(orderId) });
    }
    queryClient.invalidateQueries({ queryKey: ['floorLayout'] });
    queryClient.invalidateQueries({ queryKey: ['takeaways'] });
  };
}

export function useCreateOrder() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: (input: {
      tableId?: string;
      orderType: 'dine_in' | 'takeaway' | 'counter_sale';
      guestCount: number;
      items?: CartLine[];
      idempotencyKey?: string;
    }) => apiClient.createOrder(input),
    onSuccess: (order) => invalidate(order.id),
  });
}

export function useSendToKitchen() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ orderId, version, cart }: { orderId: string; version: number; cart: CartLine[] }) =>
      apiClient.sendToKitchen(orderId, version, cart),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

/** Open kitchen tickets for the branch, refreshed every few seconds for the board and the floor. */
export function useKitchenBoard(branchId: string) {
  return useQuery<BoardTicket[]>({
    queryKey: ['kitchenBoard', branchId],
    queryFn: () => apiClient.getKitchenBoard(branchId),
    enabled: !!branchId,
    refetchInterval: 5000,
  });
}

export function useAdvanceTicket() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ kotId, status }: { kotId: string; status: 'preparing' | 'ready' | 'served' }) =>
      apiClient.advanceTicket(kotId, status),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['kitchenBoard'] });
      queryClient.invalidateQueries({ queryKey: ['orderTickets'] });
    },
  });
}

export function useCancelOrderLine() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ orderId, lineId, reason }: { orderId: string; lineId: string; reason: string }) =>
      apiClient.cancelOrderLine(orderId, lineId, reason),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

export function useRequestVoid() {
  const queryClient = useQueryClient();
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ kotId, reason }: { kotId: string; reason: string; orderId: string }) =>
      apiClient.requestVoid(kotId, reason),
    onSettled: (_data, _error, vars) => {
      invalidate(vars.orderId);
      queryClient.invalidateQueries({ queryKey: ['orderVoidRequests', vars.orderId] });
    },
  });
}

/** Cancel a ticket straight away with a manager's PIN. */
export function useVoidTicket() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ kotId, reason, approverPin }: { kotId: string; reason: string; approverPin: string; orderId: string }) =>
      apiClient.voidTicket(kotId, reason, approverPin),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

export function useConfirmSlipPrinted() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ kotId, idempotencyKey }: { kotId: string; idempotencyKey: string; orderId: string }) =>
      apiClient.confirmSlipPrinted(kotId, idempotencyKey),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

export function useTransferTable() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ sourceTableId, targetTableId, reason }: { sourceTableId: string; targetTableId: string; reason?: string; orderId: string }) =>
      apiClient.transferTable(sourceTableId, targetTableId, reason),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

// ---- Billing -----------------------------------------------------------------------------

export function useOrderBill(orderId: string | undefined, enabled = true) {
  return useQuery<Bill | null>({
    queryKey: BILL_KEYS.forOrder(orderId ?? ''),
    queryFn: () => apiClient.getBillForOrder(orderId!),
    enabled: !!orderId && enabled,
  });
}

/** After any billing change the order status, bill and table colour all move together. */
function useInvalidateBilling() {
  const queryClient = useQueryClient();
  const invalidateOrder = useInvalidateOrder();
  return (orderId: string) => {
    queryClient.invalidateQueries({ queryKey: BILL_KEYS.forOrder(orderId) });
    invalidateOrder(orderId);
  };
}

export function useGenerateBill() {
  const invalidate = useInvalidateBilling();
  return useMutation({
    mutationFn: ({ orderId, idempotencyKey, discount }: { orderId: string; idempotencyKey: string; discount?: BillDiscount }) =>
      apiClient.generateBill(orderId, idempotencyKey, discount),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

export function useStartTerminalPayment() {
  const invalidate = useInvalidateBilling();
  return useMutation({
    mutationFn: ({ billId, mode, amountPaise, idempotencyKey }: {
      orderId: string; billId: string; mode: 'card' | 'upi'; amountPaise: number; idempotencyKey: string;
    }) => apiClient.startTerminalPayment(billId, mode, amountPaise, idempotencyKey),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

export function useCompleteTerminalPayment() {
  const invalidate = useInvalidateBilling();
  return useMutation({
    mutationFn: ({ paymentId, outcome }: { orderId: string; paymentId: string; outcome: TerminalOutcome }) =>
      apiClient.completeTerminalPayment(paymentId, outcome),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

export function useReverseTerminalPayment() {
  const invalidate = useInvalidateBilling();
  return useMutation({
    mutationFn: ({ paymentId, reason, approverPin, details, checkOnly }: {
      orderId: string; paymentId: string; reason: string; approverPin?: string; details?: TerminalOutcome['details']; checkOnly?: boolean;
    }) => apiClient.reverseTerminalPayment(paymentId, { reason, approver_pin: approverPin, details, check_only: checkOnly }),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

// ---- Print station and cash shift ---------------------------------------------------------

/** KOTs this branch's counter still has to print; polled so waiters' orders print within seconds. */
export function usePrintQueue(branchId: string, enabled = true) {
  return useQuery<KitchenTicket[]>({
    queryKey: ['printQueue', branchId],
    queryFn: () => apiClient.getPrintQueue(branchId),
    enabled: !!branchId && enabled,
    refetchInterval: 4000,
    // The counter keeps printing waiters' KOTs even while another screen or app (e.g. Pine Labs taking a card) is in front.
    refetchIntervalInBackground: true,
  });
}

export function useOverview(branchId: string, period: OverviewPeriod, allBranches = false) {
  return useQuery<BusinessOverview>({
    queryKey: ['overview', allBranches ? 'all' : branchId, period],
    queryFn: () => apiClient.getOverview(branchId, period, allBranches),
    enabled: allBranches || !!branchId,
    refetchInterval: period === 'today' ? 60000 : false,
  });
}

export const BUSINESS_PROFILE_KEY = ['businessProfile'] as const;

export function useBusinessProfile(enabled = true) {
  return useQuery<BusinessProfile>({ queryKey: BUSINESS_PROFILE_KEY, queryFn: () => apiClient.getBusinessProfile(), enabled });
}

/** Change the bill name and/or logo. `logo: null` removes it; leave a field out to keep it. */
export function useUpdateBusinessProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (change: { displayName?: string | null; logoBase64?: string | null }) => {
      let profile: BusinessProfile | undefined;
      if (change.displayName !== undefined) profile = await apiClient.setBusinessName(change.displayName);
      if (change.logoBase64 === null) profile = await apiClient.removeBusinessLogo();
      else if (change.logoBase64 !== undefined) profile = await apiClient.setBusinessLogo(change.logoBase64);
      return profile ?? apiClient.getBusinessProfile();
    },
    onSuccess: (profile) => queryClient.setQueryData(BUSINESS_PROFILE_KEY, profile),
  });
}

export const CASH_DAY_KEY = ['cashDay'] as const;

/** The branch's cash day; re-checked every minute so a day opened or closed on another device shows up. */
export function useCashDay(enabled = true) {
  return useQuery<CashDay>({ queryKey: CASH_DAY_KEY, queryFn: () => apiClient.getCashDay(), enabled, refetchInterval: 60000 });
}

export function useOpenCashDay() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (openingCashPaise: number) => apiClient.openCashDay(openingCashPaise),
    onSuccess: (day) => queryClient.setQueryData(CASH_DAY_KEY, day),
    onSettled: () => queryClient.invalidateQueries({ queryKey: CASH_DAY_KEY }),
  });
}

export function useCloseCashDay() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (countedCashPaise: number) => apiClient.closeCashDay(countedCashPaise),
    onSettled: () => queryClient.invalidateQueries({ queryKey: CASH_DAY_KEY }),
  });
}

export function useCurrentShift(enabled = true) {
  return useQuery<CashShift | null>({ queryKey: ['currentShift'], queryFn: () => apiClient.getCurrentShift(), enabled });
}

export function useOpenShift() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (openingCashPaise: number) => apiClient.openShift(openingCashPaise),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['currentShift'] }),
  });
}

export function useCloseShift() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (countedCashPaise: number) => apiClient.closeShift(countedCashPaise),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['currentShift'] }),
  });
}

export function useRecordPayment() {
  const invalidate = useInvalidateBilling();
  return useMutation<
    PaymentOutcome,
    Error,
    { orderId: string; billId: string; payment: PaymentInput; idempotencyKey: string }
  >({
    mutationFn: ({ billId, payment, idempotencyKey }) => apiClient.recordPayment(billId, payment, idempotencyKey),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}
