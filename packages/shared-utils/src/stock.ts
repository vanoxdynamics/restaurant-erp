/**
 * How far below zero a sale may take a piece's stock (the server's `NEGATIVE_STOCK_LIMIT`). The count may run a little behind
 * the shelf, but once it reaches this the piece is locked for billing until a delivery is received or the stock is counted.
 */
export const NEGATIVE_STOCK_LIMIT = -5;

/** ok: enough · low: at or below its low level (or none left) · minus: below zero · locked: at the limit, cannot be billed. */
export type StockState = 'ok' | 'low' | 'minus' | 'locked';

export function stockState(quantity: number, lowLevel?: number | null): StockState {
  if (quantity <= NEGATIVE_STOCK_LIMIT) return 'locked';
  if (quantity < 0) return 'minus';
  if (quantity === 0 || (lowLevel != null && quantity <= lowLevel)) return 'low';
  return 'ok';
}

/** Whether `pieces` more of a piece with `onHand` recorded can still be billed. Unknown stock (hidden from the user) is allowed. */
export function canBill(onHand: number | null | undefined, pieces: number): boolean {
  return onHand == null || onHand - pieces >= NEGATIVE_STOCK_LIMIT;
}
