import { create } from 'zustand';
import { apiClient } from '@repo/api-client';
import { storage } from './storage';

const DEVICE_KEY = 'restaurant_pos_device';

export interface DeviceConfig {
  deviceId: string;
  branchId: string;
  branchName: string;
  /** Issued when a manager linked this tablet; unlocks the 'who is working' list. */
  deviceToken: string;
}

interface DeviceState {
  status: 'loading' | 'unregistered' | 'registered';
  device: DeviceConfig | null;
  load: () => Promise<void>;
  /** Link this tablet to a branch with the one-time code a manager made in the admin app. */
  register: (code: string) => Promise<void>;
  unregister: () => Promise<void>;
  /** Picks up the branch's current name (an owner may have renamed it). Quiet when offline or unlinked. */
  refreshBranch: () => Promise<void>;
}

const newDeviceId = () => `pos-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export const useDeviceStore = create<DeviceState>((set, get) => ({
  status: 'loading',
  device: null,

  load: async () => {
    const stored = await storage.get<DeviceConfig>(DEVICE_KEY);
    // A tablet linked before device tokens existed has to be linked once more.
    const device = stored?.deviceToken ? stored : null;
    set({ status: device ? 'registered' : 'unregistered', device });
    if (device) void get().refreshBranch();
  },

  register: async (code) => {
    const deviceId = newDeviceId();
    const link = await apiClient.linkDevice(code, deviceId);
    const device: DeviceConfig = {
      deviceId,
      branchId: link.branch_id,
      branchName: link.branch_name,
      deviceToken: link.device_token,
    };
    await storage.set(DEVICE_KEY, device);
    set({ status: 'registered', device });
  },

  refreshBranch: async () => {
    const device = get().device;
    if (!device) return;
    try {
      const branch = await apiClient.getDeviceBranch(device.deviceToken);
      const current = get().device;
      // Only this tablet's own branch, and only when the name really changed.
      if (!current || branch.branch_id !== current.branchId || branch.branch_name === current.branchName) return;
      const updated = { ...current, branchName: branch.branch_name };
      await storage.set(DEVICE_KEY, updated);
      set({ device: updated });
    } catch {
      // Offline, or the link was revoked (the roster screen handles that): keep the name this tablet has.
    }
  },

  unregister: async () => {
    await storage.remove(DEVICE_KEY);
    set({ status: 'unregistered', device: null });
  },
}));
