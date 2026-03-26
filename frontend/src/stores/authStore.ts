import { create } from 'zustand';
import { api } from '../api/client';
import type { User } from '../types';

interface AuthState {
  user: User | null;
  token: string | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: localStorage.getItem('whatsarch_token'),
  loading: false,
  isAuthenticated: false,

  login: async (email, password) => {
    set({ loading: true });
    try {
      const res = await api.login(email, password);
      localStorage.setItem('whatsarch_token', res.access_token);
      localStorage.setItem('whatsarch_refresh', res.refresh_token);
      set({ user: res.user, token: res.access_token, isAuthenticated: true, loading: false });
    } catch (e) {
      set({ loading: false });
      throw e;
    }
  },

  signup: async (email, password, name) => {
    set({ loading: true });
    try {
      const res = await api.signup(email, password, name);
      localStorage.setItem('whatsarch_token', res.access_token);
      localStorage.setItem('whatsarch_refresh', res.refresh_token);
      set({ user: res.user, token: res.access_token, isAuthenticated: true, loading: false });
    } catch (e) {
      set({ loading: false });
      throw e;
    }
  },

  logout: () => {
    localStorage.removeItem('whatsarch_token');
    localStorage.removeItem('whatsarch_refresh');
    set({ user: null, token: null, isAuthenticated: false });
  },

  checkAuth: async () => {
    const token = localStorage.getItem('whatsarch_token');
    if (!token) {
      set({ isAuthenticated: false });
      return;
    }
    try {
      const user = await api.getMe();
      set({ user, token, isAuthenticated: true });
    } catch {
      // Try refresh
      const refresh = localStorage.getItem('whatsarch_refresh');
      if (refresh) {
        try {
          const res = await api.refreshToken(refresh);
          localStorage.setItem('whatsarch_token', res.access_token);
          set({ user: res.user, token: res.access_token, isAuthenticated: true });
        } catch {
          set({ isAuthenticated: false });
        }
      } else {
        set({ isAuthenticated: false });
      }
    }
  },
}));
