# WhatsArch - Multi-Platform Chat Archive Search Engine

## 1. Project Overview

WhatsArch is a multi-platform (WhatsApp + Telegram) chat archive search engine with AI-powered Q&A. It processes exported chat folders, transcribes voice messages, describes images/videos using multi-provider vision AI, extracts PDF text, indexes everything into a searchable database, and serves a web UI for full-text search + AI chat.

**Supports:** WhatsApp exports (`_chat.txt` + media) and Telegram exports (`result.json` + media).
**Multilingual:** Auto-detects chat language. UI supports Hebrew (RTL) and English (LTR). AI prompts in 9 languages.
**Admin:** `susunoufi@gmail.com` is the admin/owner.

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, Flask, Gunicorn (production) |
| Database | SQLite + FTS5 (trigram tokenizer) per chat |
| Cloud DB | Supabase PostgreSQL (user accounts, chat metadata) |
| Auth | Supabase Auth (Google OAuth + email/password) |
| Embeddings | `intfloat/multilingual-e5-large` (1024-dim, 512 token limit) |
| Speech-to-text | faster-whisper (local, Whisper model) |
| Vision AI | Multi-provider: Anthropic, OpenAI, Google Gemini, Ollama |
| RAG LLM | Multi-provider: Anthropic, OpenAI, Google Gemini, Ollama |
| PDF extraction | PyMuPDF (`pymupdf`) |
| Video processing | ffmpeg (frame extraction + audio extraction) |
| Frontend (current) | Vanilla HTML/CSS/JS, dark theme, Font Awesome icons |
| Frontend (planned) | React + TypeScript + Vite + Tailwind (in `frontend/`) |
| Desktop | Electron wrapper with embedded Python/ffmpeg/Ollama |
| Local Agent | Lightweight Flask service on localhost:11470 (in `agent/`) |
| Deployment | Railway (backend), Supabase (DB + auth + storage) |
| PWA | Maskable icons, Share Target, service worker |
| Logo | DALL-E 3 generated (teal/indigo glass-morphism) |

### Dependencies (`requirements.txt`)

```
faster-whisper, flask, tqdm, anthropic, openai, google-genai,
python-dotenv, pymupdf, sentence-transformers
```

---

## 2. Architecture

### File Structure

```
WhatsArch/
  run.py                          # Main entry point + create_web_app() for Railway
  wsgi.py                         # Gunicorn entry (auto-generated in Docker)
  Dockerfile                      # Railway deployment
  .env                            # API keys + Supabase + Railway credentials
  settings.json                   # Provider/model preferences + user plans (auto-created)
  requirements.txt
  UPGRADE_PLAN.md                 # Architecture upgrade roadmap
  chat_search/
    __init__.py                   # Empty package marker
    config.py                     # Settings, hardware detection, presets, cost calculator, provider models
    parser.py                     # WhatsApp + Telegram parser, platform/language detection, sender aliases
    transcribe.py                 # Audio transcription (Whisper)
    vision.py                     # Multi-provider image/video/PDF processing (parallel)
    indexer.py                    # SQLite FTS5 index + semantic embeddings + incremental indexing
    chunker.py                    # Conversation-aware chunking (1-on-1 + group thread detection)
    ai_chat.py                    # Multi-provider RAG pipeline + multilingual NLP + SSE streaming
    server.py                     # Flask web server + REST API + Auth + Upload + Admin
    process_manager.py            # Background processing orchestrator + progress tracking + ETA
    templates/
      landing.html                # Landing page (dark theme, animated, bilingual)
      index.html                  # Main SPA (search, management, settings, gallery, analytics, admin)
      auth.html                   # Login/signup page (email + Google OAuth)
    static/
      manifest.json               # PWA manifest with maskable icons + share_target
      sw.js                       # Service worker (network-first caching)
      logo_final2.png             # Main logo (DALL-E 3, 1024x1024)
      icon-192.png                # PWA icon (maskable, rounded)
      icon-512.png                # PWA icon (maskable, rounded)
      apple-touch-icon.png        # iOS home screen icon (180x180)
      favicon.png                 # Browser tab icon (32x32)
      icon-48.png                 # Header logo (48x48)
  frontend/                       # React + TypeScript + Vite (planned replacement for vanilla HTML)
    package.json                  # React 19, Zustand, TanStack Query, Tailwind, Recharts
    vite.config.ts                # Vite dev proxy to Flask :5000
    src/
      App.tsx                     # Main app with tab routing
      api/client.ts               # Typed API client for all endpoints
      types/index.ts              # TypeScript interfaces for all API responses
      stores/                     # Zustand: chatStore, searchStore, settingsStore, authStore
      components/                 # Search, AIChat, Management, Settings, Gallery, Analytics
      utils/                      # i18n (he/en), formatters
  agent/                          # Local agent for background compute
    agent.py                      # Flask on localhost:11470 (Whisper, Ollama, file upload)
    install.bat                   # Windows installer (startup + dependencies)
    requirements.txt
  desktop/
    main.js                       # Electron main process
    start.js                      # Dev launcher
    setup.html                    # First-run setup UI
    package.json                  # Electron + electron-builder config
  chats/
    <chat_name>/                  # One folder per exported chat
      _chat.txt                   # WhatsApp export (or result.json for Telegram)
      *.opus, *.jpg, *.mp4, etc.  # Media files
      data/
        chat.db                   # SQLite search index
        chat_chunk_embeddings.npy # Chunk semantic embeddings
        transcriptions.json       # Audio transcription cache
        descriptions.json         # Image + video description cache
        video_transcriptions.json # Video audio transcription cache
        pdf_texts.json            # PDF text extraction cache
```

### Module Connections

```
run.py
  -> parser.py:detect_platform()  (Step 0: WhatsApp or Telegram?)
  -> transcribe.py                (Step 1: audio -> text)
  -> vision.py                    (Step 2: images/videos/PDFs -> descriptions, multi-provider)
  -> parser.py:parse_chat/parse_telegram  (Step 3: export + caches -> messages + sender aliases)
  -> parser.py:detect_chat_language       (Step 3b: auto-detect language)
  -> parser.py:detect_chat_type           (Step 4: 1on1/group)
  -> indexer.py:build_index               (Step 5: messages -> SQLite FTS5 + incremental)
  -> chunker.py                           (Step 6: messages -> conversation chunks)
  -> indexer.py:build_chunks + build_chunk_embeddings  (Step 7: chunks -> DB + embeddings)
  -> server.py                            (Step 8: Flask web server)
       -> indexer.py      (search, stats, context, semantic_search, analytics, export)
       -> ai_chat.py      (RAG pipeline with multilingual support + SSE streaming)
       -> config.py       (settings, hardware detection, presets, provider models)
       -> Supabase        (auth, user data, cloud storage)
```

### Entry Points

**Landing page:** `/` → `landing.html` (unauthenticated users in web mode)
**Main app:** `/app` → `index.html` (authenticated users or local mode)
**Login:** `/login` → `auth.html` (web mode only)
**Local CLI:** `run.py:main()` - processes chats then starts Flask server
**Production:** `run.py:create_web_app()` - Flask app factory for Gunicorn/Railway
**Desktop:** `desktop/main.js` - Electron wrapper, spawns Flask as subprocess

---

## 3. Multi-Provider AI System

### Configuration (`chat_search/config.py`)

Settings stored in `settings.json` (project root):
```json
{
  "vision_provider": "gemini",
  "vision_model": "gemini-2.0-flash",
  "video_provider": "gemini",
  "video_model": "gemini-2.0-flash",
  "rag_provider": "gemini",
  "rag_model": "gemini-2.0-flash",
  "ollama_base_url": "http://localhost:11434",
  "ollama_vision_model": "llama3.2-vision",
  "ollama_rag_model": "qwen2.5:14b",
  "sender_aliases": {},
  "user_plans": {}
}
```

Default provider is **Gemini Flash** (cheapest option).

### Preset Packages

| Preset | Vision | RAG | Use Case |
|--------|--------|-----|----------|
| **Budget** (default) | Gemini Flash | Gemini Flash | Cheapest, good quality |
| **Balanced** | Gemini Flash | GPT-4o-mini | Fast + better Hebrew |
| **Premium** | Claude Sonnet | Claude Opus | Best quality, most expensive |
| **Local** | Ollama llava | Ollama qwen2.5 | Free, requires GPU |

`config.recommend_preset()` auto-recommends based on chat size + hardware.
`config.estimate_preset_cost()` calculates cost per chat.

### Supported Providers

| Provider | Vision | RAG | Cost | Local? |
|----------|--------|-----|------|--------|
| **Anthropic** | Claude Haiku/Sonnet | Claude Haiku/Sonnet/Opus | $$ | No |
| **OpenAI** | GPT-4o-mini, GPT-4.1-nano | GPT-4o-mini, GPT-4.1-nano | $ | No |
| **Google Gemini** | Gemini 2.0 Flash | Gemini 2.0 Flash | $ (cheapest) | No |
| **Ollama** | llama3.2-vision | qwen2.5:7b/14b | Free | Yes |

### Hardware Detection

`config.detect_hardware()` returns CPU, RAM, GPU info.
`config.estimate_ollama_performance(hw)` rates device for local AI feasibility.

---

## 4. Multilingual Support

### Language Detection (`parser.py:detect_chat_language()`)

Samples message text, detects by character ranges (Hebrew, Arabic, Cyrillic, CJK) and common word patterns (Spanish, French, German, Portuguese). Returns ISO 639-1 code.

Stored in `chat_metadata.language`. Passed to RAG and Vision.

### UI Internationalization (i18n)

Frontend supports Hebrew (RTL) and English (LTR) with language toggle button.
Translation system: `TRANSLATIONS` dict with ~150 keys, `t(key)` function, `applyLanguage()` for DOM updates.
Language preference saved in `localStorage('whatsarch_lang')`.

### Dynamic AI Prompts

- **Vision** (`vision.py`): `IMAGE_PROMPTS` dict with prompts in 9 languages
- **RAG** (`ai_chat.py`): `get_system_prompt(chat_name, language)` returns language-appropriate system prompt
- **Stop words** (`ai_chat.py`): `STOP_WORDS` dict with sets for he, en, es, fr, de, ru, pt
- **Keyword extraction**: `extract_keywords(question, language)` uses language-specific stop words

---

## 5. Platform Support

### WhatsApp (`parser.py:parse_chat()`)
- Parses `_chat.txt` with regex: `[DD/MM/YYYY, HH:MM:SS] Sender: Message`
- Handles multi-line messages, `<attached: filename>` tags
- Name mention detection for group thread linking
- **Sender aliases**: optional name mapping applied at parse time

### Telegram (`parser.py:parse_telegram()`)
- Parses `result.json` (Telegram Desktop export)
- Handles text segments (plain + formatted), media type mapping
- Media in subdirectories (`photos/`, `voice_messages/`, etc.)
- **Sender aliases**: same support as WhatsApp

### Auto-Detection (`parser.py:detect_platform()`)
- `_chat.txt` present → WhatsApp
- `result.json` present → Telegram
- Falls back to scanning JSON files for Telegram structure

---

## 6. API Routes (`chat_search/server.py`)

### Core Search
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Landing page (unauth) or main app (local mode) |
| GET | `/app` | Main SPA (authenticated) |
| GET | `/api/chats` | List all chats with platform, language |
| GET | `/api/search?chat=&q=&sender=&from=&to=&type=&page=` | FTS5 search, 50/page |
| GET | `/api/search/all?q=&...` | Cross-chat search |
| GET | `/api/context/<id>?chat=` | Surrounding messages |
| GET | `/api/stats?chat=` | Chat statistics |

### AI Chat
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/ai/status` | Provider availability + current settings |
| POST | `/api/ai/chat` | RAG Q&A (blocking) with debug info |
| POST | `/api/ai/chat/stream` | RAG Q&A (SSE streaming) |

### Processing
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/process/status?chat=` | Processing status with per-file detail |
| POST | `/api/process/start` | Start background task |
| GET | `/api/process/progress?chat=` | Poll progress with ETA |
| POST | `/api/process/stop` | Cancel running task |

### Media & Content
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/media/<chat>/<file>` | Serve media files (path-traversal protected) |
| GET | `/api/thumbnail/<chat>/<file>` | Video thumbnail (first frame) |
| GET | `/api/media/list?chat=&type=&page=` | Media gallery listing |
| GET | `/api/analytics?chat=` | Sender stats, activity heatmap, hourly distribution |
| GET | `/api/export?chat=&q=&format=csv|json` | Export search results |

### Settings & Config
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/settings` | Current settings + API key status |
| POST | `/api/settings` | Update settings and/or API keys |
| GET | `/api/hardware` | Hardware detection + Ollama performance |
| GET | `/api/models` | Available provider models catalog |
| GET | `/api/presets?chat=` | Preset packages with cost estimates |
| GET | `/api/aliases?chat=` | Sender name aliases |
| POST | `/api/aliases` | Update sender aliases |

### Auth (Web mode only)
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/auth/signup` | Email + password signup |
| POST | `/api/auth/login` | Email + password login |
| GET | `/api/auth/google` | Google OAuth redirect URL |
| POST | `/api/auth/refresh` | Refresh access token |
| GET | `/api/auth/me` | Current user profile |
| GET | `/login` | Login/signup page |
| GET | `/auth/callback` | OAuth callback → redirects to `/app` |

### Admin (susunoufi@gmail.com only)
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/admin/users` | List all users with plans |
| POST | `/api/admin/users` | Set user preset `{email, preset}` |
| DELETE | `/api/admin/users` | Reset user to default plan |
| GET | `/api/user/plan` | Current user's assigned plan |

### Upload
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/upload` | Upload ZIP (max 500MB) |
| POST | `/api/upload/url` | Download from Google Drive/Dropbox/OneDrive link |
| POST | `/api/upload/share` | PWA Share Target handler |

### Auth Middleware

- `@require_auth` — enforced only in web mode (when `SUPABASE_URL` is set)
- `@require_admin` — requires `susunoufi@gmail.com`
- `get_user_preset()` — returns user's allowed preset
- `enforce_user_preset()` — overrides settings based on user plan
- New users default to **budget** preset

---

## 7. Frontend

### Landing Page (`templates/landing.html`)
Dark theme with animated gradient mesh background. Sections: hero (logo + CTAs), how it works (3 steps), features grid (6), online vs local comparison. Mobile: hides "download" option.

### Main App (`templates/index.html`)
Dark theme SPA, ~3200 lines. 6 tabs:

1. **Search** - Full-text search with filters, result type badges, context viewer, export (CSV/JSON)
2. **Management** - Per-chat processing, action buttons, progress bars with ETA, file upload (ZIP + URL link)
3. **Settings** - Preset packages, model selection cards, API key management, hardware info, cost calculator
4. **Gallery** - Media grid with type filter, lightbox, pagination
5. **Analytics** - Sender bar charts, hourly heatmap, busiest days, media breakdown
6. **Admin** - User management table (admin only), preset assignment

**AI Chat Panel** - Floating sidebar with SSE streaming, source citation modal, RAG debug ("why this answer?").

**i18n** - Hebrew/English toggle, RTL/LTR switching, ~150 translated strings.

**Icons** - Font Awesome 6.5 (no emojis).

**Theme:** Dark (#0a0f1a), glass-morphism cards, teal/indigo gradients.

### React Frontend (`frontend/`)
Planned replacement. Built with Vite + React 19 + TypeScript + Tailwind + Zustand + TanStack Query. Builds successfully (259KB JS). Not yet deployed — vanilla HTML is the active frontend.

---

## 8. Config & Environment

### Environment Variables (`.env`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | For Anthropic provider | Vision + RAG |
| `OPENAI_API_KEY` | For OpenAI provider | Vision + RAG + DALL-E logo |
| `GOOGLE_API_KEY` | For Gemini provider | Vision + RAG |
| `SUPABASE_URL` | For web mode | Enables auth + cloud features |
| `SUPABASE_ANON_KEY` | For web mode | Client-side Supabase access |
| `SUPABASE_SERVICE_ROLE_KEY` | For web mode | Server-side Supabase access |
| `GOOGLE_OAUTH_CLIENT_ID` | For Google login | OAuth provider |
| `GOOGLE_OAUTH_CLIENT_SECRET` | For Google login | OAuth provider |

### Supabase Schema

```sql
profiles (id, email, display_name, plan, storage_used_mb, max_storage_mb)
user_chats (id, user_id, chat_name, platform, language, chat_type, status, ...)
processing_jobs (id, user_id, chat_id, task, status, progress, ...)
```

All tables have Row Level Security: each user sees only their own data.

---

## 9. Key Features Added in Recent Sessions

### Session 1 (this codebase session):
- Multi-provider AI (Ollama, Gemini, OpenAI, Anthropic) for RAG + Vision
- Settings page with model selection, hardware detection, cost calculator
- Preset packages (budget/balanced/premium/local) with smart recommendation
- Admin user management (plan assignment per user)
- Sender name aliases (applied at parse time, before indexing)
- SSE streaming for AI chat responses
- RAG transparency ("why this answer?" debug view)
- Cross-chat search
- Media gallery tab
- Analytics dashboard (charts, heatmaps)
- Incremental indexing (skip rebuild when no changes)
- Parallel Vision API processing (ThreadPoolExecutor)
- ETA for processing tasks
- CSV/JSON export
- i18n (Hebrew/English with RTL/LTR)
- PWA with maskable icons
- Dark theme UI overhaul
- DALL-E 3 generated logo
- Font Awesome icons (replaced all emojis)
- URL-based upload (Google Drive/Dropbox/OneDrive links)
- Upload button always visible in management tab
- Landing page with dark animated design
- Local agent service (agent/)
- Login redirect fix (OAuth → /app)

### Session 2 (parallel session):
- Telegram support (parser + auto-detection)
- 9-language multilingual support
- Supabase Auth (Google OAuth + email/password)
- Railway deployment + Dockerfile
- Electron desktop wrapper
- ZIP upload endpoint + PWA Share Target
- OAuth PKCE callback fix

---

## 10. Current Status & Known Issues

### Working
- Multi-provider Vision + RAG (4 providers, configurable per user)
- WhatsApp + Telegram parsing with auto-detection
- 9-language support (auto-detect + dynamic prompts)
- SSE streaming for AI chat
- Cross-chat search + export
- Background processing with ETA and cancellation
- Upload ZIP + URL link (Google Drive/Dropbox/OneDrive)
- Supabase Auth (Google OAuth + email/password)
- Admin user management with preset enforcement
- Railway deployment (live, auto-deploy on push)
- Dark theme UI with DALL-E logo
- i18n (Hebrew RTL / English LTR)
- PWA with maskable icons
- Incremental indexing
- Sender name aliases

### Known Limitations
- Frontend is vanilla HTML/JS (~3200 lines) — React migration in `frontend/` is built but not deployed
- FTS5 trigram requires 3+ char queries
- Embeddings loaded fully into memory
- No re-ranking model after retrieval
- Desktop build not yet produced
- Local agent not yet connected to web UI
- Upload via URL may timeout for very large files on Railway
- Mobile UI needs more polish
