import { create } from 'zustand';
import { api } from '../api/client';
import type { SearchResult, SearchResponse } from '../types';

interface SearchFilters {
  sender: string;
  dateFrom: string;
  dateTo: string;
  searchType: string;
}

interface SearchState {
  query: string;
  results: SearchResult[];
  total: number;
  page: number;
  loading: boolean;
  filters: SearchFilters;
  setQuery: (q: string) => void;
  setFilter: (key: keyof SearchFilters, value: string) => void;
  search: (chat: string, reset?: boolean) => Promise<void>;
  loadMore: (chat: string) => Promise<void>;
  clear: () => void;
}

export const useSearchStore = create<SearchState>((set, get) => ({
  query: '',
  results: [],
  total: 0,
  page: 1,
  loading: false,
  filters: { sender: '', dateFrom: '', dateTo: '', searchType: 'all' },

  setQuery: (q) => set({ query: q }),
  setFilter: (key, value) => set(s => ({ filters: { ...s.filters, [key]: value } })),

  search: async (chat, reset = true) => {
    const { query, filters } = get();
    if (!query.trim()) return;

    if (reset) set({ page: 1, results: [] });
    set({ loading: true });

    const params: Record<string, string> = {
      q: query,
      sender: filters.sender,
      from: filters.dateFrom,
      to: filters.dateTo,
      type: filters.searchType,
      page: String(reset ? 1 : get().page),
    };

    try {
      let data: SearchResponse;
      if (chat === '__all__') {
        data = await api.searchAll(params);
      } else {
        data = await api.search({ ...params, chat });
      }
      set(s => ({
        results: reset ? data.results : [...s.results, ...data.results],
        total: data.total,
        page: data.page,
        loading: false,
      }));
    } catch {
      set({ loading: false });
    }
  },

  loadMore: async (chat) => {
    set(s => ({ page: s.page + 1 }));
    get().search(chat, false);
  },

  clear: () => set({ query: '', results: [], total: 0, page: 1 }),
}));
