import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { StaffRole, KitchenStation } from '@repo/shared-utils';
import { apiClient } from './client';
import {
  BranchSummary,
  CartLine,
  KitchenTicket,
  OrderDetail,
  Category,
  Floor,
  FraudAuditLog,
  LiveKPIs,
  MenuItemUpdate,
  PaymentBreakdown,
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
    mutationFn: (item: {
      category_id: string;
      name: string;
      base_price_paise: number;
      description?: string;
      station?: KitchenStation;
    }) => apiClient.createMenuItem(item),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog }),
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

export function useVoidApprovals(branchId: string, status?: 'pending' | 'approved' | 'rejected') {
  return useQuery<VoidApprovalItem[]>({
    queryKey: QUERY_KEYS.voidRequests(branchId, status),
    queryFn: () => apiClient.getVoidRequests(branchId, status),
    enabled: !!branchId,
    refetchInterval: status === 'pending' ? 10000 : undefined,
  });
}

function useReviewMutation(action: 'approve' | 'reject') {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, note }: { requestId: string; note?: string }) =>
      action === 'approve' ? apiClient.approveVoidRequest(requestId, note) : apiClient.rejectVoidRequest(requestId, note),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['voidRequests'] });
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
    refetchInterval: 15000,
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
    mutationFn: (input: { tableId?: string; orderType: 'dine_in' | 'takeaway'; guestCount: number }) =>
      apiClient.createOrder(input),
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

export function useCancelOrderLine() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ orderId, lineId, reason }: { orderId: string; lineId: string; reason: string }) =>
      apiClient.cancelOrderLine(orderId, lineId, reason),
    onSettled: (_data, _error, vars) => invalidate(vars.orderId),
  });
}

export function useRequestVoid() {
  const invalidate = useInvalidateOrder();
  return useMutation({
    mutationFn: ({ kotId, reason }: { kotId: string; reason: string; orderId: string }) =>
      apiClient.requestVoid(kotId, reason),
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
