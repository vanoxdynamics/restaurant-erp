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
}

const newDeviceId = () => `pos-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export const useDeviceStore = create<DeviceState>((set) => ({
  status: 'loading',
  device: null,

  load: async () => {
    const stored = await storage.get<DeviceConfig>(DEVICE_KEY);
    // A tablet linked before device tokens existed has to be linked once more.
    const device = stored?.deviceToken ? stored : null;
    set({ status: device ? 'registered' : 'unregistered', device });
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

  unregister: async () => {
    await storage.remove(DEVICE_KEY);
    set({ status: 'unregistered', device: null });
  },
}));
