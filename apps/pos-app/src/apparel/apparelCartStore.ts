import { create } from 'zustand';
import type { Customer } from '@repo/api-client';
import { storage } from '../stores/storage';

/** Each staff member's cart is kept under their own key, so one person's bill never shows up for the next. */
const cartKey = (staffId: string) => `apparel.cart.${staffId}`;
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
  /** Who the sale is to, when the customer gave a name or phone: printed on the invoice and counted in their visits. */
  customer: Customer | null;
  pendingSale: PendingApparelSale | null;
  /** Whose cart this is: the staff member signed in. */
  owner: string | null;
  /** True while the owner's saved cart is being read; nothing is written meanwhile. */
  loading: boolean;
  /** Shows this staff member's own cart (as they left it on this device), putting the previous person's away. */
  openFor: (staffId: string) => Promise<void>;
  setPendingSale: (sale: PendingApparelSale | null) => void;
  /** Adds one piece, or bumps the quantity when the variant is already in the cart. */
  add: (line: Omit<ApparelCartLine, 'quantity' | 'line_discount_paise'>, quantity?: number) => void;
  setQuantity: (variantId: string, quantity: number) => void;
  setLineDiscount: (variantId: string, paise: number) => void;
  setBillDiscount: (paise: number) => void;
  setDiscountReason: (reason: string) => void;
  setCustomer: (customer: Customer | null) => void;
  clear: () => void;
}

/** What identifies the cart's contents: pieces, discounts, reason and customer. The customer is left out when there is none, so a
 * cart without one keeps the signature it had before customers could be added. */
export const cartSignature = (
  s: Pick<ApparelCartState, 'lines' | 'billDiscountPaise' | 'discountReason'> & { customer?: Pick<Customer, 'id'> | null },
) =>
  JSON.stringify([
    s.lines.map((l) => [l.variant_id, l.quantity, l.line_discount_paise]), s.billDiscountPaise, s.discountReason.trim(),
    ...(s.customer ? [s.customer.id] : []),
  ]);

/**
 * The sale being rung up. It is kept on the device and is only emptied when its invoice is paid (or the cashier clears a cart
 * that has no invoice), so cancelling the payment, going back, locking or reloading never loses it.
 */
export const useApparelCartStore = create<ApparelCartState>((set, get) => ({
  lines: [],
  billDiscountPaise: 0,
  discountReason: '',
  customer: null,
  pendingSale: null,
  owner: null,
  loading: false,

  openFor: async (staffId) => {
    // The same person back (after a lock, say): the cart in memory is the newest one. Reading again could bring back an older
    // copy whose write had not finished and drop pieces added since.
    if (get().owner === staffId) return;
    // The previous person's cart is already saved under their key (every change is). Start this person empty and read theirs.
    set({ owner: staffId, loading: true, lines: [], billDiscountPaise: 0, discountReason: '', customer: null, pendingSale: null });
    let saved: Pick<ApparelCartState, 'lines' | 'billDiscountPaise' | 'discountReason' | 'customer' | 'pendingSale'> | null = null;
    try {
      saved = await storage.get(cartKey(staffId));
    } catch {
      saved = null; // nothing readable kept: start with an empty cart
    }
    set((state) => {
      if (state.owner !== staffId) return state; // someone else signed in while this was read
      // Pieces scanned while the saved cart was being read are kept, not overwritten.
      if (!saved || !Array.isArray(saved.lines) || state.lines.length || state.pendingSale) return { loading: false };
      const lines = saved.lines
        .filter((l) => l && typeof l.variant_id === 'string' && l.quantity > 0)
        .map((l) => fitDiscount({ ...l, quantity: clampQuantity(l.quantity) }));
      return {
        lines,
        billDiscountPaise: fitBillDiscount(lines, saved.billDiscountPaise ?? 0),
        discountReason: saved.discountReason ?? '',
        customer: saved.customer && typeof saved.customer.id === 'string' ? saved.customer : null,
        pendingSale: saved.pendingSale ?? null,
        loading: false,
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
  setCustomer: (customer) => set({ customer }),
  clear: () => set({ lines: [], billDiscountPaise: 0, discountReason: '', customer: null, pendingSale: null }),
}));

// Every change is written to the device under its owner, so the cart survives a reload or restart.
useApparelCartStore.subscribe((s) => {
  if (!s.owner || s.loading) return; // nobody signed in yet, or their saved cart is still being read
  void storage.trySet(cartKey(s.owner), {
    lines: s.lines, billDiscountPaise: s.billDiscountPaise, discountReason: s.discountReason, customer: s.customer, pendingSale: s.pendingSale,
  });
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
