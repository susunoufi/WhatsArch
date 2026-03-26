# WhatsArch — Architecture Upgrade Plan

## Current State
- **Backend:** Python Flask, single-process, SQLite + FTS5, file-based caching
- **Frontend:** Single 2800-line HTML file with inline CSS/JS (Vanilla, no framework)
- **AI:** Anthropic/OpenAI/Gemini/Ollama support for RAG + Vision (just added)
- **Search:** Hybrid 5-tier retrieval (semantic + keyword), Hebrew NLP
- **Scale:** 6 chats, 716K total messages, works but monolithic

## Problem
The frontend is a single HTML file approaching 3000 lines. Every new feature makes it harder to maintain. This will become unmanageable within 1-2 months of active development. The backend is solid but will hit SQLite limits with 10+ large chats.

---

## Phase 1: Frontend Migration (Priority: CRITICAL)
**Goal:** Move from Vanilla HTML to React + TypeScript + Vite

### Structure
```
WhatsArch/
  backend/                    # Flask stays as API-only server
    chat_search/
      server.py               # Pure REST API (remove template rendering)
      ai_chat.py
      vision.py
      indexer.py
      config.py
      chunker.py
      parser.py
      process_manager.py
  frontend/                   # New React app
    package.json
    vite.config.ts
    src/
      App.tsx
      components/
        Layout/Header.tsx
        Search/SearchBar.tsx, SearchResults.tsx, ResultCard.tsx
        Gallery/GalleryGrid.tsx, MediaViewer.tsx
        Analytics/Dashboard.tsx, SenderChart.tsx, HourlyChart.tsx
        Settings/SettingsPage.tsx, ModelSelector.tsx, ApiKeyManager.tsx
        AIChat/ChatPanel.tsx, ChatMessage.tsx, SourceModal.tsx
        Management/ProcessingStatus.tsx, ChatCard.tsx
      hooks/
        useSearch.ts
        useAIChat.ts
        useSettings.ts
        useAnalytics.ts
      stores/                 # Zustand state management
        searchStore.ts
        chatStore.ts
        settingsStore.ts
      types/
        index.ts              # TypeScript interfaces for all API responses
      api/
        client.ts             # Centralized fetch wrapper
      utils/
        formatters.ts         # Hebrew date formatting, cost calculations, etc.
```

### Tech Stack
- **React 19** + TypeScript
- **Vite** (fast dev server, HMR)
- **Zustand** (lightweight state management, better than Redux for this scale)
- **TanStack Query** (API caching, loading states, error handling)
- **Tailwind CSS** (utility-first, RTL support built-in)
- **Recharts** or **Chart.js** (for analytics dashboard)
- **Framer Motion** (animations)

### Migration Steps
1. Create Vite + React + TS project in `frontend/`
2. Extract all CSS into Tailwind classes
3. Convert each UI section into a React component
4. Replace global JS state with Zustand stores
5. Replace `fetch()` calls with TanStack Query hooks
6. Add TypeScript types for all API responses
7. Configure Vite proxy to Flask backend (dev mode)
8. Production: Flask serves the built React bundle from `frontend/dist/`

### Estimated Effort: 3-5 days (for someone who knows React)

---

## Phase 2: Advanced AI Features (Priority: HIGH)
**Goal:** Make the AI search significantly smarter

### 2a. Re-ranking Model
- Add a cross-encoder re-ranker after chunk retrieval
- Use `cross-encoder/ms-marco-MiniLM-L-6-v2` (small, fast, multilingual)
- Re-score top 30 chunks → pick best 12
- Expected improvement: 30-50% better answer relevance

### 2b. Visual Search (CLIP)
- Add CLIP embeddings for images (`openai/clip-vit-base-patch32`)
- Store image embeddings alongside text embeddings
- Enable queries like "find photos of a beach" or "show me screenshots"
- Separate `image_embeddings.npy` per chat

### 2c. Agent Mode
- Multi-step reasoning: "Find all vacation plans and summarize them by year"
- Tool-use pattern: LLM can call search, filter by date, aggregate
- Uses Claude's tool_use or OpenAI function calling

### 2d. Conversation Memory
- Store AI chat history per user session in SQLite
- "Remember" context across sessions
- "Last time I asked about X, what did you find?"

---

## Phase 3: Scale & Performance (Priority: MEDIUM)
**Goal:** Handle 10+ chats with 1M+ messages each

### 3a. Database Upgrade
- **PostgreSQL + pgvector** replaces SQLite + numpy embeddings
- Built-in vector similarity search
- Better concurrent access (multiple users)
- Full-text search with Hebrew dictionary support

### 3b. Vector Database (Alternative to pgvector)
- **Qdrant** or **ChromaDB** for embeddings
- Better performance at scale (1M+ vectors)
- Filtering + hybrid search built-in

### 3c. Search Engine
- **Meilisearch** — instant search, typo-tolerant, great Hebrew support
- Replaces FTS5 trigram for the web UI search
- Sub-millisecond results even on 1M+ messages

### 3d. Task Queue
- **Celery + Redis** for background processing
- Parallel transcription, vision, embedding tasks
- Progress tracking via Redis pub/sub
- Persistent task state (survives server restart)

### 3e. Caching Layer
- **Redis** for API response caching
- Cache stats, search results, analytics
- Invalidate on new data

---

## Phase 4: Distribution & Packaging (Priority: LOW)
**Goal:** Make it installable as a desktop app

### Option A: Tauri (Recommended)
- Rust-based, much smaller than Electron (~5MB vs ~150MB)
- Native OS integration (file dialogs, system tray)
- Auto-update support
- Bundle Python backend with PyInstaller

### Option B: Electron
- More mature ecosystem
- Easier to develop (same JS stack)
- Larger bundle size

### Option C: Docker Compose
```yaml
services:
  backend:
    build: ./backend
    volumes:
      - ./chats:/app/chats
  ollama:
    image: ollama/ollama
    volumes:
      - ollama_data:/root/.ollama
  frontend:
    build: ./frontend
    ports:
      - "5000:80"
```

---

## Phase 5: Future Vision (Priority: EXPLORATORY)

| Feature | Description |
|---------|-------------|
| **WhatsApp live sync** | Auto-import new messages via WhatsApp Web protocol |
| **Multi-user** | Authentication, per-user settings, shared search |
| **Telegram/Signal support** | Parse other chat formats |
| **Knowledge graph** | Entity extraction → relationship mapping between people/topics |
| **Voice search** | Speak a question, get spoken answer (Whisper + TTS) |
| **Mobile app** | React Native or Flutter companion app |
| **Scheduled reports** | "Every Monday, email me a summary of last week's chats" |

---

## Decision Points

### When to start Phase 1 (React migration)?
- **Now** if you plan to add more UI features in the next month
- **Later** if the current UI is "good enough" and focus is on backend/AI

### When to start Phase 3 (PostgreSQL)?
- When you have 5+ chats with 500K+ messages each
- When search feels slow (>2 seconds)
- When you want multi-user support

### Key Question for the Other Session
If the other session is working on a different project with a frontend framework (Next.js?), ask:
1. Can the WhatsArch frontend share components/design system with that project?
2. Should both projects use the same tech stack for consistency?
3. Is there any shared infrastructure (auth, API patterns, deployment) that should be aligned?

---

## Summary for Cross-Session Communication

**To the other session:** WhatsArch just completed a major feature sprint (26 features: multi-provider AI, settings UI, streaming, analytics, gallery, cross-chat search, PWA, export, incremental indexing). The next critical step is migrating the 2800-line single HTML frontend to React + TypeScript + Vite. Before starting, I need to know:

1. What frontend framework/stack is being used in the other project?
2. Are there shared design patterns or component libraries we should align on?
3. Should I start the React migration now, or wait until the other session's work is complete to avoid conflicts?
4. Is there any backend infrastructure (database, task queue, deployment) being set up in the other session that WhatsArch should use too?

Please respond with your recommendations so we can coordinate.
