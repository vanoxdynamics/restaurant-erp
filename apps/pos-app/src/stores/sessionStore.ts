import { create } from 'zustand';
import { apiClient, StaffUser } from '@repo/api-client';

/** Lock the till after this long without a touch. */
export const IDLE_LOCK_MS = 2 * 60 * 1000;

interface SessionState {
  user: StaffUser | null;
  lastActivity: number;
  signIn: (branchId: string, pin: string, deviceId: string, staffId: string) => Promise<void>;
  lock: () => void;
  touch: () => void;
}

/**
 * The signed-in waiter. Kept in memory only: a shared tablet always returns to the PIN pad
 * after a restart, a lock, or two minutes idle.
 */
export const useSessionStore = create<SessionState>((set) => ({
  user: null,
  lastActivity: Date.now(),

  signIn: async (branchId, pin, deviceId, staffId) => {
    const session = await apiClient.pinLogin(branchId, pin, deviceId, staffId);
    set({ user: session.user, lastActivity: Date.now() });
  },

  lock: () => {
    apiClient.setToken(undefined);
    set({ user: null });
  },

  touch: () => set({ lastActivity: Date.now() }),
}));

// An expired or revoked token anywhere locks the till.
apiClient.configure({ onUnauthorized: () => useSessionStore.getState().lock() });
