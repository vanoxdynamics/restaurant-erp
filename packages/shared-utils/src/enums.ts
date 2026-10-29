/**
 * Core Domain Enums for Restaurant ERP Monorepo
 */

export enum TableStatus {
  AVAILABLE = 'AVAILABLE',
  OCCUPIED = 'OCCUPIED',
  BILLED = 'BILLED',
  DIRTY = 'DIRTY',
  RESERVED = 'RESERVED',
}

export enum StaffRole {
  OWNER = 'OWNER',
  MANAGER = 'MANAGER',
  CASHIER = 'CASHIER',
  CAPTAIN = 'CAPTAIN',
  WAITER = 'WAITER',
  KITCHEN = 'KITCHEN',
}

export enum KOTStatus {
  QUEUED = 'QUEUED',
  PREPARING = 'PREPARING',
  READY = 'READY',
  SERVED = 'SERVED',
  VOIDED = 'VOIDED',
}

export enum KitchenStation {
  MAIN_KITCHEN = 'Main Kitchen',
  BAKERY = 'Bakery',
  TANDOOR = 'Tandoor',
  GRILL = 'Grill',
  DESSERT = 'Dessert',
}

export enum PaymentMode {
  CASH = 'cash',
  CARD = 'card',
  UPI = 'upi',
}

export enum FloorSection {
  AC_HALL = 'AC Hall',
  NON_AC = 'Non-AC',
  OUTDOORS = 'Outdoors',
  TAKEAWAY_COUNTER = 'Takeaway Counter',
}

export enum VoidApprovalStatus {
  PENDING = 'PENDING',
  APPROVED = 'APPROVED',
  REJECTED = 'REJECTED',
}

export enum ShiftStatus {
  OPEN = 'OPEN',
  CLOSED = 'CLOSED',
}

export enum WastageReason {
  CUSTOMER_CHANGED_MIND = 'Customer Changed Mind',
  KITCHEN_SPILL = 'Kitchen Spill',
  WRONG_ORDER_PUNCH = 'Wrong Order Punch',
  QUALITY_REJECTION = 'Quality Rejection',
  EXCESS_PREPARATION = 'Excess Preparation',
}

export const WS_CHANNELS = {
  adminChannel: (branchId: string) => `branch:${branchId}:admin`,
  catalogChannel: (branchId: string) => `branch:${branchId}:catalog`,
  floorChannel: (branchId: string) => `branch:${branchId}:tables`,
  kotsChannel: (branchId: string) => `branch:${branchId}:kots`,
};
