import type {
  Chat,
  ChatStats,
  SearchResponse,
  ContextResponse,
  AIChatRequest,
  AIChatResponse,
  AIStatus,
  SettingsResponse,
  HardwareResponse,
  ModelsResponse,
  ProcessingStatus,
  TaskProgress,
  AnalyticsData,
  MediaListResponse,
  User,
  AuthResponse,
} from '../types';

const BASE = '';  // Same origin, proxied by Vite in dev

function enc(s: string) { return encodeURIComponent(s); }

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('whatsarch_token');
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(BASE + url, {
    ...options,
    headers: {
      ...getAuthHeaders(),
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(error.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Chats
  getChats: () => request<Chat[]>('/api/chats'),
  getStats: (chat: string) => request<ChatStats>(`/api/stats?chat=${enc(chat)}`),
  getContext: (id: number, chat: string, before = 5, after = 5) =>
    request<ContextResponse>(`/api/context/${id}?chat=${enc(chat)}&before=${before}&after=${after}`),

  // Search
  search: (params: Record<string, string>) =>
    request<SearchResponse>(`/api/search?${new URLSearchParams(params)}`),
  searchAll: (params: Record<string, string>) =>
    request<SearchResponse>(`/api/search/all?${new URLSearchParams(params)}`),

  // AI
  getAIStatus: () => request<AIStatus>('/api/ai/status'),
  askAI: (body: AIChatRequest) =>
    request<AIChatResponse>('/api/ai/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  askAIStream: (body: AIChatRequest) =>
    fetch(BASE + '/api/ai/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(body),
    }),

  // Settings
  getSettings: () => request<SettingsResponse>('/api/settings'),
  updateSettings: (data: Record<string, unknown>) =>
    request<{ status: string }>('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  getHardware: () => request<HardwareResponse>('/api/hardware'),
  getModels: () => request<ModelsResponse>('/api/models'),

  // Processing
  getProcessStatus: (chat: string) =>
    request<ProcessingStatus>(`/api/process/status?chat=${enc(chat)}`),
  startProcess: (chat: string, task: string) =>
    request<{ status: string }>('/api/process/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat, task }),
    }),
  getProgress: (chat: string) =>
    request<TaskProgress>(`/api/process/progress?chat=${enc(chat)}`),
  stopProcess: (chat: string) =>
    request<{ status: string }>('/api/process/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat }),
    }),

  // Media & Analytics
  getMediaList: (chat: string, type = 'all', page = 1) =>
    request<MediaListResponse>(`/api/media/list?chat=${enc(chat)}&type=${type}&page=${page}`),
  getAnalytics: (chat: string) =>
    request<AnalyticsData>(`/api/analytics?chat=${enc(chat)}`),

  // Export
  getExportUrl: (params: Record<string, string>) =>
    `${BASE}/api/export?${new URLSearchParams(params)}`,

  // Upload
  uploadZip: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ status: string; chat_name: string }>('/api/upload', {
      method: 'POST',
      body: form,
    });
  },

  // Auth
  login: (email: string, password: string) =>
    request<AuthResponse>('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    }),
  signup: (email: string, password: string, display_name?: string) =>
    request<AuthResponse>('/api/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, display_name }),
    }),
  getGoogleAuthUrl: () => request<{ url: string }>('/api/auth/google'),
  refreshToken: (refresh_token: string) =>
    request<AuthResponse>('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token }),
    }),
  getMe: () => request<User>('/api/auth/me'),
};

// Re-export types for convenience
export type {
  Chat,
  ChatStats,
  SearchResponse,
  ContextResponse,
  AIChatRequest,
  AIChatResponse,
  AIStatus,
  SettingsResponse,
  HardwareResponse,
  ModelsResponse,
  ProcessingStatus,
  TaskProgress,
  AnalyticsData,
  MediaListResponse,
  User,
  AuthResponse,
} from '../types';

export type {
  SearchResult,
  ContextMessage,
  Settings,
  ApiKeyStatus,
  Hardware,
  OllamaPerformance,
  ModelOption,
  MediaFile,
  AISource,
  RAGDebug,
  RAGChunkDetail,
} from '../types';
