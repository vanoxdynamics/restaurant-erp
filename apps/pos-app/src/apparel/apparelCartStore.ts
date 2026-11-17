import { create } from 'zustand';

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

interface ApparelCartState {
  lines: ApparelCartLine[];
  billDiscountPaise: number;
  discountReason: string;
  /** Adds one piece, or bumps the quantity when the variant is already in the cart. */
  add: (line: Omit<ApparelCartLine, 'quantity' | 'line_discount_paise'>, quantity?: number) => void;
  setQuantity: (variantId: string, quantity: number) => void;
  setLineDiscount: (variantId: string, paise: number) => void;
  setBillDiscount: (paise: number) => void;
  setDiscountReason: (reason: string) => void;
  clear: () => void;
}

/** Lives in memory, like the restaurant cart: a sale not yet made is discarded when the till locks. */
export const useApparelCartStore = create<ApparelCartState>((set) => ({
  lines: [],
  billDiscountPaise: 0,
  discountReason: '',

  add: (line, quantity = 1) =>
    set((state) => {
      const existing = state.lines.find((l) => l.variant_id === line.variant_id);
      if (existing) {
        return { lines: state.lines.map((l) => (l === existing ? { ...l, quantity: l.quantity + quantity } : l)) };
      }
      return { lines: [...state.lines, { ...line, quantity, line_discount_paise: 0 }] };
    }),

  setQuantity: (variantId, quantity) =>
    set((state) => ({
      lines: state.lines.map((l) => (l.variant_id === variantId ? { ...l, quantity } : l)).filter((l) => l.quantity > 0),
    })),

  setLineDiscount: (variantId, paise) =>
    set((state) => ({ lines: state.lines.map((l) => (l.variant_id === variantId ? { ...l, line_discount_paise: Math.max(0, paise) } : l)) })),

  setBillDiscount: (paise) => set({ billDiscountPaise: Math.max(0, paise) }),
  setDiscountReason: (reason) => set({ discountReason: reason }),
  clear: () => set({ lines: [], billDiscountPaise: 0, discountReason: '' }),
}));

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
