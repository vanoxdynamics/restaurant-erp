import { create } from 'zustand';
import { ApiError, apiClient } from '@repo/api-client';
import { StaffRole } from '@repo/shared-utils';
import { storage } from './storage';

const DEVICE_KEY = 'restaurant_pos_device';

export interface DeviceConfig {
  deviceId: string;
  branchId: string;
  branchName: string;
}

interface DeviceState {
  status: 'loading' | 'unregistered' | 'registered';
  device: DeviceConfig | null;
  load: () => Promise<void>;
  /** A manager signs in once to link this tablet to a branch; their session is not kept. */
  register: (username: string, password: string) => Promise<void>;
  unregister: () => Promise<void>;
}

const newDeviceId = () => `pos-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export const useDeviceStore = create<DeviceState>((set) => ({
  status: 'loading',
  device: null,

  load: async () => {
    const device = await storage.get<DeviceConfig>(DEVICE_KEY);
    set({ status: device ? 'registered' : 'unregistered', device });
  },

  register: async (username, password) => {
    try {
      const session = await apiClient.login(username.trim(), password);
      if (session.user.role !== StaffRole.OWNER && session.user.role !== StaffRole.MANAGER) {
        throw new ApiError(403, 'forbidden', 'Only a manager or owner can set up this device.');
      }
      const branches = (await apiClient.getBranches()).filter((b) => b.is_active);
      const branch = branches.find((b) => b.id === session.user.branch_id) ?? branches[0];
      if (!branch) throw new ApiError(403, 'no_branch', 'This account is not linked to an active branch.');

      const device: DeviceConfig = { deviceId: newDeviceId(), branchId: branch.id, branchName: branch.name };
      await storage.set(DEVICE_KEY, device);
      set({ status: 'registered', device });
    } finally {
      apiClient.setToken(undefined);
    }
  },

  unregister: async () => {
    await storage.remove(DEVICE_KEY);
    set({ status: 'unregistered', device: null });
  },
}));
