import { create } from 'zustand';
import { CartLine } from '@repo/api-client';

/**
 * Items the waiter has picked but not yet sent, per order. Lives in memory: anything unsent
 * when the till locks is discarded, which matches how a paper pad works.
 */
interface CartState {
  carts: Record<string, CartLine[]>;
  add: (orderId: string, line: Omit<CartLine, 'key'>) => void;
  setQuantity: (orderId: string, key: string, quantity: number) => void;
  setNotes: (orderId: string, key: string, notes: string) => void;
  remove: (orderId: string, key: string) => void;
  clear: (orderId: string) => void;
  clearAll: () => void;
  /** Tickets this device has asked a manager to void (the waiter cannot read the approval queue). */
  voidRequested: Record<string, true>;
  markVoidRequested: (kotId: string) => void;
}

const sameChoice = (a: Omit<CartLine, 'key'>, b: CartLine) =>
  a.item_id === b.item_id &&
  a.variant_id === b.variant_id &&
  !a.notes &&
  !b.notes &&
  a.modifier_ids.slice().sort().join() === b.modifier_ids.slice().sort().join();

export const useCartStore = create<CartState>((set) => ({
  carts: {},

  add: (orderId, line) =>
    set((state) => {
      const cart = state.carts[orderId] ?? [];
      // Tapping the same plain item again bumps its quantity instead of adding a row.
      const existing = cart.find((c) => sameChoice(line, c));
      const next = existing
        ? cart.map((c) => (c === existing ? { ...c, quantity: c.quantity + line.quantity } : c))
        : [...cart, { ...line, key: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}` }];
      return { carts: { ...state.carts, [orderId]: next } };
    }),

  setQuantity: (orderId, key, quantity) =>
    set((state) => ({
      carts: {
        ...state.carts,
        [orderId]: (state.carts[orderId] ?? [])
          .map((c) => (c.key === key ? { ...c, quantity } : c))
          .filter((c) => c.quantity > 0),
      },
    })),

  setNotes: (orderId, key, notes) =>
    set((state) => ({
      carts: {
        ...state.carts,
        [orderId]: (state.carts[orderId] ?? []).map((c) => (c.key === key ? { ...c, notes } : c)),
      },
    })),

  remove: (orderId, key) =>
    set((state) => ({
      carts: { ...state.carts, [orderId]: (state.carts[orderId] ?? []).filter((c) => c.key !== key) },
    })),

  clear: (orderId) =>
    set((state) => {
      const next = { ...state.carts };
      delete next[orderId];
      return { carts: next };
    }),

  clearAll: () => set({ carts: {}, voidRequested: {} }),

  voidRequested: {},
  markVoidRequested: (kotId) => set((state) => ({ voidRequested: { ...state.voidRequested, [kotId]: true } })),
}));
