import { create } from 'zustand';
import { api } from '../api/client';
import type { Chat, ChatStats } from '../types';

interface ChatState {
  chats: Chat[];
  currentChat: string;
  stats: ChatStats | null;
  loading: boolean;
  setCurrentChat: (name: string) => void;
  loadChats: () => Promise<void>;
  loadStats: (chat: string) => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  chats: [],
  currentChat: '',
  stats: null,
  loading: false,

  setCurrentChat: (name) => {
    set({ currentChat: name, stats: null });
    if (name && name !== '__all__') {
      get().loadStats(name);
    }
  },

  loadChats: async () => {
    set({ loading: true });
    try {
      const chats = await api.getChats();
      set({ chats, loading: false });
      // Auto-select first ready chat if none selected
      if (!get().currentChat && chats.length > 0) {
        const first = chats.find(c => c.ready);
        if (first) get().setCurrentChat(first.name);
      }
    } catch {
      set({ loading: false });
    }
  },

  loadStats: async (chat) => {
    try {
      const stats = await api.getStats(chat);
      set({ stats });
    } catch {
      set({ stats: null });
    }
  },
}));
