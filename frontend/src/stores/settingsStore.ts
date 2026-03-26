import { create } from 'zustand';
import { api } from '../api/client';
import type { Settings, ApiKeyStatus, HardwareResponse, ModelsResponse } from '../types';

interface SettingsState {
  settings: Settings | null;
  apiKeys: ApiKeyStatus | null;
  hardware: HardwareResponse | null;
  models: ModelsResponse | null;
  loading: boolean;
  load: () => Promise<void>;
  updateSettings: (updates: Record<string, unknown>) => Promise<void>;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  settings: null,
  apiKeys: null,
  hardware: null,
  models: null,
  loading: false,

  load: async () => {
    set({ loading: true });
    try {
      const [settingsRes, hwRes, modelsRes] = await Promise.all([
        api.getSettings(),
        api.getHardware(),
        api.getModels(),
      ]);
      set({
        settings: settingsRes.settings,
        apiKeys: settingsRes.api_keys,
        hardware: hwRes,
        models: modelsRes,
        loading: false,
      });
    } catch {
      set({ loading: false });
    }
  },

  updateSettings: async (updates) => {
    await api.updateSettings(updates);
    // Reload settings after update
    const res = await api.getSettings();
    set({ settings: res.settings, apiKeys: res.api_keys });
  },
}));
