import { create } from 'zustand';
import { storage } from '../stores/storage';

const CART_KEY = 'apparel.cart';
/** The most pieces of one size and colour a line may hold (the server refuses more). */
export const MAX_LINE_QUANTITY = 999;
/** The most lines one invoice may hold (the server refuses more). */
export const MAX_LINES = 200;

const clampQuantity = (n: number) => Math.min(MAX_LINE_QUANTITY, Math.max(0, Math.floor(Number.isFinite(n) ? n : 0)));
/** A line's own discount can never be more than the line, so changing the quantity keeps it valid. */
const fitDiscount = (l: ApparelCartLine): ApparelCartLine => ({
  ...l,
  line_discount_paise: Math.min(Math.max(0, l.line_discount_paise || 0), l.price_paise * l.quantity),
});
/** The bill discount can never be more than what the lines come to after their own discounts. */
const fitBillDiscount = (lines: ApparelCartLine[], paise: number) =>
  Math.min(Math.max(0, paise || 0), lines.reduce((n, l) => n + lineNet(l), 0));

let loaded = false;

/** One size and colour in the dress shop cart, with what the shelf said when it was added. */
export interface ApparelCartLine {
  variant_id: string;
  name: string;
  size: string;
  colour: string;
  mrp_paise?: number;
  price_paise: number;
  quantity: number;
  line_discount_paise: number;
  on_hand?: number;
}

/** The invoice made from this cart that is not paid yet. The cart stays until it is paid, so leaving or cancelling the payment
 * brings the same sale back. */
export interface PendingApparelSale {
  id: string;
  number?: string;
  /** The cart as it was invoiced: an unchanged cart resumes this invoice instead of making another. */
  signature: string;
}

interface ApparelCartState {
  lines: ApparelCartLine[];
  billDiscountPaise: number;
  discountReason: string;
  pendingSale: PendingApparelSale | null;
  /** Reads the cart kept on this device (after a reload or restart). */
  load: () => Promise<void>;
  setPendingSale: (sale: PendingApparelSale | null) => void;
  /** Adds one piece, or bumps the quantity when the variant is already in the cart. */
  add: (line: Omit<ApparelCartLine, 'quantity' | 'line_discount_paise'>, quantity?: number) => void;
  setQuantity: (variantId: string, quantity: number) => void;
  setLineDiscount: (variantId: string, paise: number) => void;
  setBillDiscount: (paise: number) => void;
  setDiscountReason: (reason: string) => void;
  clear: () => void;
}

/** What identifies the cart's contents: pieces, discounts and reason. */
export const cartSignature = (s: Pick<ApparelCartState, 'lines' | 'billDiscountPaise' | 'discountReason'>) =>
  JSON.stringify([s.lines.map((l) => [l.variant_id, l.quantity, l.line_discount_paise]), s.billDiscountPaise, s.discountReason.trim()]);

/**
 * The sale being rung up. It is kept on the device and is only emptied when its invoice is paid (or the cashier clears a cart
 * that has no invoice), so cancelling the payment, going back, locking or reloading never loses it.
 */
export const useApparelCartStore = create<ApparelCartState>((set) => ({
  lines: [],
  billDiscountPaise: 0,
  discountReason: '',
  pendingSale: null,

  load: async () => {
    // Read once per app run: after that the cart in memory is the newest one. Reading again (the counter screen remounting
    // after a lock, say) could bring back an older copy whose write had not finished and drop pieces added since.
    if (loaded) return;
    loaded = true;
    let saved: Pick<ApparelCartState, 'lines' | 'billDiscountPaise' | 'discountReason' | 'pendingSale'> | null = null;
    try {
      saved = await storage.get(CART_KEY);
    } catch {
      return; // nothing readable kept: start with an empty cart
    }
    if (!saved || !Array.isArray(saved.lines)) return;
    set((state) => {
      // Pieces scanned while the saved cart was being read are kept, not overwritten.
      if (state.lines.length || state.pendingSale) return state;
      const lines = saved.lines
        .filter((l) => l && typeof l.variant_id === 'string' && l.quantity > 0)
        .map((l) => fitDiscount({ ...l, quantity: clampQuantity(l.quantity) }));
      return {
        lines,
        billDiscountPaise: fitBillDiscount(lines, saved.billDiscountPaise ?? 0),
        discountReason: saved.discountReason ?? '',
        pendingSale: saved.pendingSale ?? null,
      };
    });
  },
  setPendingSale: (pendingSale) => set({ pendingSale }),

  add: (line, quantity = 1) =>
    set((state) => {
      const existing = state.lines.find((l) => l.variant_id === line.variant_id);
      if (existing) {
        // The same size and colour again: one line with more pieces. The shelf's latest price and stock are kept.
        const lines = state.lines.map((l) =>
          l === existing ? fitDiscount({ ...l, ...line, quantity: clampQuantity(l.quantity + quantity), line_discount_paise: l.line_discount_paise }) : l,
        );
        return { lines };
      }
      if (state.lines.length >= MAX_LINES) return state;
      return { lines: [...state.lines, { ...line, quantity: clampQuantity(quantity) || 1, line_discount_paise: 0 }] };
    }),

  setQuantity: (variantId, quantity) =>
    set((state) => {
      const lines = state.lines
        .map((l) => (l.variant_id === variantId ? fitDiscount({ ...l, quantity: clampQuantity(quantity) }) : l))
        .filter((l) => l.quantity > 0);
      return { lines, billDiscountPaise: fitBillDiscount(lines, state.billDiscountPaise) };
    }),

  setLineDiscount: (variantId, paise) =>
    set((state) => {
      const lines = state.lines.map((l) => (l.variant_id === variantId ? fitDiscount({ ...l, line_discount_paise: paise }) : l));
      return { lines, billDiscountPaise: fitBillDiscount(lines, state.billDiscountPaise) };
    }),

  setBillDiscount: (paise) => set((state) => ({ billDiscountPaise: fitBillDiscount(state.lines, paise) })),
  setDiscountReason: (reason) => set({ discountReason: reason }),
  clear: () => set({ lines: [], billDiscountPaise: 0, discountReason: '', pendingSale: null }),
}));

// Every change is written to the device, so the cart survives a reload or restart.
useApparelCartStore.subscribe((s) => {
  void storage.trySet(CART_KEY, { lines: s.lines, billDiscountPaise: s.billDiscountPaise, discountReason: s.discountReason, pendingSale: s.pendingSale });
});

/** What a line comes to after its own discount. Display only: the server's invoice is the truth. */
export const lineNet = (l: ApparelCartLine) => Math.max(0, l.price_paise * l.quantity - l.line_discount_paise);

export function cartTotals(lines: ApparelCartLine[], billDiscountPaise: number) {
  const gross = lines.reduce((n, l) => n + l.price_paise * l.quantity, 0);
  const lineDiscounts = lines.reduce((n, l) => n + l.line_discount_paise, 0);
  const net = lines.reduce((n, l) => n + lineNet(l), 0);
  const total = Math.max(0, net - billDiscountPaise);
  const pieces = lines.reduce((n, l) => n + l.quantity, 0);
  return { gross, lineDiscounts, billDiscountPaise, total, pieces };
}

/** True when any line or the bill has a discount, which then needs a reason. */
export const hasDiscount = (lines: ApparelCartLine[], billDiscountPaise: number) =>
  billDiscountPaise > 0 || lines.some((l) => l.line_discount_paise > 0);
