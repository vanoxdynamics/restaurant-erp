import { create } from 'zustand';
import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { ApiError, apiClient, BranchSummary, StaffUser } from '@repo/api-client';
import { StaffRole } from '@repo/shared-utils';

const SESSION_KEY = 'restaurant_admin_session';

interface StoredSession {
  token: string;
  expiresAt: number;
  user: StaffUser;
  branchId: string;
  branchName: string;
}

// SecureStore keeps the token in the iOS Keychain / Android Keystore. It is unavailable on
// web, where the preview build falls back to localStorage.
const storage = {
  async get(): Promise<StoredSession | null> {
    try {
      const raw =
        Platform.OS === 'web' ? globalThis.localStorage?.getItem(SESSION_KEY) ?? null : await SecureStore.getItemAsync(SESSION_KEY);
      return raw ? (JSON.parse(raw) as StoredSession) : null;
    } catch {
      return null;
    }
  },
  async set(session: StoredSession): Promise<void> {
    const raw = JSON.stringify(session);
    if (Platform.OS === 'web') globalThis.localStorage?.setItem(SESSION_KEY, raw);
    else await SecureStore.setItemAsync(SESSION_KEY, raw);
  },
  async clear(): Promise<void> {
    if (Platform.OS === 'web') globalThis.localStorage?.removeItem(SESSION_KEY);
    else await SecureStore.deleteItemAsync(SESSION_KEY);
  },
};

type AuthStatus = 'restoring' | 'signed_out' | 'signed_in';

interface AuthState {
  status: AuthStatus;
  currentUser: StaffUser | null;
  activeBranchId: string;
  activeBranchName: string;
  branches: BranchSummary[];
  restore: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchBranch: (branchId: string, branchName: string) => Promise<void>;
}

const ADMIN_ROLES = new Set<string>([StaffRole.OWNER, StaffRole.MANAGER]);

async function pickBranches(user: StaffUser): Promise<{ branches: BranchSummary[]; branch: BranchSummary | undefined }> {
  const branches = (await apiClient.getBranches()).filter((b) => b.is_active);
  const branch = branches.find((b) => b.id === user.branch_id) ?? branches[0];
  return { branches, branch };
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: 'restoring',
  currentUser: null,
  activeBranchId: '',
  activeBranchName: '',
  branches: [],

  restore: async () => {
    const saved = await storage.get();
    if (!saved || saved.expiresAt <= Date.now()) {
      await storage.clear();
      set({ status: 'signed_out' });
      return;
    }
    apiClient.setToken(saved.token);
    try {
      const user = await apiClient.getMe();
      const { branches } = await pickBranches(user);
      set({
        status: 'signed_in',
        currentUser: user,
        branches,
        activeBranchId: saved.branchId,
        activeBranchName: saved.branchName,
      });
    } catch (error) {
      // Offline at launch: keep the saved session rather than signing the user out.
      if (error instanceof ApiError && error.status === 0) {
        set({
          status: 'signed_in',
          currentUser: saved.user,
          activeBranchId: saved.branchId,
          activeBranchName: saved.branchName,
        });
        return;
      }
      apiClient.setToken(undefined);
      await storage.clear();
      set({ status: 'signed_out' });
    }
  },

  login: async (username, password) => {
    const session = await apiClient.login(username.trim(), password);
    if (!ADMIN_ROLES.has(session.user.role)) {
      apiClient.setToken(undefined);
      throw new ApiError(403, 'forbidden', 'This app is for owners and managers.');
    }
    const { branches, branch } = await pickBranches(session.user);
    if (!branch) {
      apiClient.setToken(undefined);
      throw new ApiError(403, 'no_branch', 'Your account is not linked to an active branch.');
    }
    await storage.set({
      token: session.access_token,
      expiresAt: session.expires_at,
      user: session.user,
      branchId: branch.id,
      branchName: branch.name,
    });
    set({
      status: 'signed_in',
      currentUser: session.user,
      branches,
      activeBranchId: branch.id,
      activeBranchName: branch.name,
    });
  },

  logout: async () => {
    apiClient.setToken(undefined);
    await storage.clear();
    set({ status: 'signed_out', currentUser: null, activeBranchId: '', activeBranchName: '', branches: [] });
  },

  switchBranch: async (branchId, branchName) => {
    const saved = await storage.get();
    if (saved) await storage.set({ ...saved, branchId, branchName });
    set({ activeBranchId: branchId, activeBranchName: branchName });
  },
}));

// A rejected token (expired or revoked) anywhere in the app signs the user out.
apiClient.configure({ onUnauthorized: () => void useAuthStore.getState().logout() });
