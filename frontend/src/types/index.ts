// Chat
export interface Chat {
  name: string;
  ready: boolean;
  total_messages?: number;
  platform?: string;
  language?: string;
}

// Search
export interface SearchResult {
  id: number;
  datetime: string;
  sender: string;
  text: string;
  attachment?: string;
  media_type?: string;
  transcription?: string;
  visual_description?: string;
  video_transcription?: string;
  pdf_text?: string;
  text_snippet?: string;
  transcription_snippet?: string;
  visual_description_snippet?: string;
  video_transcription_snippet?: string;
  pdf_text_snippet?: string;
  has_transcription?: boolean;
  has_visual?: boolean;
  has_video_transcription?: boolean;
  has_pdf?: boolean;
  relevance_score?: number;
  chat_name?: string; // for cross-chat search
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  page: number;
  per_page: number;
}

// Context
export interface ContextMessage {
  id: number;
  datetime: string;
  sender: string;
  text: string;
  transcription?: string;
  visual_description?: string;
  video_transcription?: string;
  pdf_text?: string;
  attachment?: string;
  media_type?: string;
}

export interface ContextResponse {
  messages: ContextMessage[];
  focus_id: number;
}

// Stats
export interface ChatStats {
  total_messages: number;
  date_range?: { first: string; last: string };
  senders?: Record<string, number>;
  media_counts?: Record<string, number>;
  chat_type?: string;
  chunk_count?: number;
}

// AI Chat
export interface AIChatRequest {
  chat: string;
  question: string;
  history?: { role: string; content: string }[];
}

export interface AIChatResponse {
  answer: string;
  sources: AISource[];
  keywords: string[];
  provider: string;
  debug?: RAGDebug;
}

export interface AISource {
  message_id: number;
  datetime: string;
  sender: string;
  preview: string;
}

export interface RAGDebug {
  chunks_retrieved: number;
  chunks_detail: RAGChunkDetail[];
}

export interface RAGChunkDetail {
  chunk_id: number;
  score: number;
  start_message_id: number;
  end_message_id: number;
  thread_id?: number;
  message_count?: number;
  senders: string;
  preview: string;
}

// AI Status
export interface AIStatus {
  configured: boolean;
  providers: string[];
  current_rag_provider?: string;
  current_rag_model?: string;
}

// Settings
export interface Settings {
  vision_provider: string;
  vision_model: string;
  video_provider: string;
  video_model: string;
  rag_provider: string;
  rag_model: string;
  ollama_base_url: string;
  ollama_vision_model: string;
  ollama_rag_model: string;
}

export interface ApiKeyStatus {
  anthropic_configured: boolean;
  anthropic_preview: string;
  openai_configured: boolean;
  openai_preview: string;
  gemini_configured: boolean;
  gemini_preview: string;
}

export interface SettingsResponse {
  settings: Settings;
  api_keys: ApiKeyStatus;
}

// Hardware
export interface Hardware {
  cpu: string;
  cpu_cores: number;
  ram_gb: number;
  ram_available_gb: number;
  gpu: string;
  gpu_dedicated: boolean;
  gpu_vram_gb: number;
  os: string;
  device_name: string;
}

export interface OllamaPerformance {
  rag_feasible: boolean;
  rag_model_recommended: string;
  rag_speed: string;
  rag_ram_usage_gb: number;
  vision_feasible: boolean;
  vision_speed_per_image: string;
  vision_ram_usage_gb: number;
  overall_rating: 'low' | 'medium' | 'good' | 'excellent';
  recommendation_text: string;
}

export interface HardwareResponse {
  hardware: Hardware;
  performance: OllamaPerformance;
}

// Models
export interface ModelOption {
  provider: string;
  model: string;
  display: string;
  cost_per_image?: number;
  cost_per_minute?: number;
  cost_per_query?: number;
  hebrew_quality: number;
  speed: 'fast' | 'slow';
  badge?: 'recommended' | 'free' | 'best';
}

export interface ModelsResponse {
  vision: ModelOption[];
  video: ModelOption[];
  rag: ModelOption[];
}

// Processing
export interface ProcessingStatus {
  active_task: string | null;
  audio: { files: { name: string; done: boolean }[]; total: number; processed: number };
  images: { files: { name: string; done: boolean }[]; total: number; processed: number };
  videos: { files: { name: string; done: boolean }[]; total: number; described: number; transcribed: number };
  pdfs: { files: { name: string; done: boolean }[]; total: number; processed: number };
  index: { status: string };
  embeddings: { status: string; done?: number; total?: number };
}

export interface TaskProgress {
  status: string;
  task?: string;
  current_file?: string;
  processed?: number;
  total?: number;
  error?: string;
  eta_seconds?: number;
}

// Analytics
export interface AnalyticsData {
  senders: { sender: string; count: number }[];
  activity: { month: string; count: number }[];
  hourly: { hour: number; count: number }[];
  media: { type: string; count: number }[];
  msg_lengths: { sender: string; avg_length: number }[];
  busiest_days: { date: string; count: number }[];
}

// Media Gallery
export interface MediaFile {
  filename: string;
  type: 'image' | 'video';
  description: string;
  url: string;
  thumbnail_url?: string;
}

export interface MediaListResponse {
  files: MediaFile[];
  total: number;
  page: number;
  per_page: number;
}

// Auth
export interface User {
  id: string;
  email: string;
  display_name?: string;
  plan?: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}
