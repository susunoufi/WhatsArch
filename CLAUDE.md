# WhatsArch - Multi-Platform Chat Archive Search Engine

## 1. Project Overview

WhatsArch is a multi-platform (WhatsApp + Telegram) chat archive search engine with AI-powered Q&A. It processes exported chat folders, transcribes voice messages, describes images/videos using multi-provider vision AI, extracts PDF text, indexes everything into a searchable database, and serves a web UI for full-text search + AI chat.

**Supports:** WhatsApp exports (`_chat.txt` + media) and Telegram exports (`result.json` + media).
**Multilingual:** Auto-detects chat language. UI in Hebrew (RTL), AI prompts in 9 languages.

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
| Frontend | Vanilla HTML/CSS/JS (React migration planned) |
| Desktop | Electron wrapper with embedded Python/ffmpeg/Ollama |
| Deployment | Railway (backend), Supabase (DB + auth + storage) |
| PWA | Share Target for direct sharing from WhatsApp on mobile |

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
  settings.json                   # Provider/model preferences (auto-created)
  requirements.txt
  chat_search/
    __init__.py                   # Empty package marker
    config.py                     # Settings system, hardware detection, cost calculator, provider models
    parser.py                     # WhatsApp + Telegram parser, platform detection, language detection
    transcribe.py                 # Audio transcription (Whisper)
    vision.py                     # Multi-provider image/video/PDF processing
    indexer.py                    # SQLite FTS5 index + semantic embeddings + analytics
    chunker.py                    # Conversation-aware chunking (1-on-1 + group thread detection)
    ai_chat.py                    # Multi-provider RAG pipeline + multilingual NLP + streaming
    server.py                     # Flask web server + REST API + Auth + Upload
    process_manager.py            # Background processing orchestrator + progress tracking
    templates/
      index.html                  # Main SPA (search, management, settings, gallery, analytics)
      auth.html                   # Login/signup page (email + Google OAuth)
    static/
      manifest.json               # PWA manifest with share_target
      sw.js                       # Service worker
      icon-192.svg, icon-512.svg  # PWA icons
  desktop/
    main.js                       # Electron main process (Flask subprocess, model download, Ollama)
    start.js                      # Dev launcher (solves npm electron module conflict)
    setup.html                    # First-run setup UI (4 steps: Whisper, E5, Ollama, AI model)
    setup-preload.js              # IPC bridge for setup window
    preload.js                    # Main window preload
    package.json                  # Electron + electron-builder config
    scripts/
      setup-vendor.js             # Build: downloads Python + ffmpeg + Ollama
  chats/
    <chat_name>/                  # One folder per exported chat
      _chat.txt                   # WhatsApp export (or result.json for Telegram)
      *.opus, *.jpg, *.mp4, etc.  # Media files
      data/
        chat.db                   # SQLite search index (messages + chunks + metadata)
        chat_chunk_embeddings.npy # Chunk semantic embeddings (numpy, 1024-dim)
        transcriptions.json       # Audio transcription cache
        descriptions.json         # Image + video visual description cache
        video_transcriptions.json # Video audio transcription cache
        pdf_texts.json            # PDF text extraction cache
```

### Module Connections

```
run.py
  -> parser.py:detect_platform()  (Step 0: WhatsApp or Telegram?)
  -> transcribe.py                (Step 1: audio -> text)
  -> vision.py                    (Step 2: images/videos/PDFs -> descriptions, multi-provider)
  -> parser.py:parse_chat/parse_telegram  (Step 3: export + caches -> messages)
  -> parser.py:detect_chat_language       (Step 3b: auto-detect language)
  -> parser.py:detect_chat_type           (Step 4: 1on1/group)
  -> indexer.py:build_index               (Step 5: messages -> SQLite FTS5 + metadata)
  -> chunker.py                           (Step 6: messages -> conversation chunks)
  -> indexer.py:build_chunks + build_chunk_embeddings  (Step 7: chunks -> DB + embeddings)
  -> server.py                            (Step 8: Flask web server)
       -> indexer.py      (search, stats, context, semantic_search, analytics)
       -> ai_chat.py      (RAG pipeline with multilingual support + streaming)
       -> config.py       (settings, hardware detection, provider models)
       -> Supabase        (auth, user data, cloud storage)
```

### Entry Points

**Local CLI:** `run.py:main()` - processes chats then starts Flask server
**Production:** `run.py:create_web_app()` - Flask app factory for Gunicorn/Railway
**Desktop:** `desktop/main.js` - Electron wrapper, spawns Flask as subprocess

---

## 3. Multi-Provider AI System

### Configuration (`chat_search/config.py`)

Settings stored in `settings.json` (project root):
```json
{
  "vision_provider": "anthropic",    // anthropic, openai, gemini, ollama
  "vision_model": "claude-sonnet-4-20250514",
  "video_provider": "anthropic",
  "video_model": "claude-sonnet-4-20250514",
  "rag_provider": "anthropic",
  "rag_model": "claude-opus-4-6",
  "ollama_base_url": "http://localhost:11434",
  "ollama_vision_model": "llama3.2-vision",
  "ollama_rag_model": "qwen2.5:14b"
}
```

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

### Dynamic Prompts

- **Vision** (`vision.py`): `IMAGE_PROMPTS` dict with prompts in 9 languages (he, ar, en, es, fr, de, ru, pt, zh)
- **RAG** (`ai_chat.py`): `get_system_prompt(chat_name, language)` returns language-appropriate system prompt
- **Stop words** (`ai_chat.py`): `STOP_WORDS` dict with sets for he, en, es, fr, de, ru, pt
- **Keyword extraction**: `extract_keywords(question, language)` uses language-specific stop words

---

## 5. Platform Support

### WhatsApp (`parser.py:parse_chat()`)
- Parses `_chat.txt` with regex: `[DD/MM/YYYY, HH:MM:SS] Sender: Message`
- Handles multi-line messages, `<attached: filename>` tags
- Name mention detection for group thread linking

### Telegram (`parser.py:parse_telegram()`)
- Parses `result.json` (Telegram Desktop export)
- Handles text segments (plain + formatted), media type mapping
- Media in subdirectories (`photos/`, `voice_messages/`, etc.)

### Auto-Detection (`parser.py:detect_platform()`)
- `_chat.txt` present → WhatsApp
- `result.json` present → Telegram
- Falls back to scanning JSON files for Telegram structure

---

## 6. API Routes (`chat_search/server.py`)

### Core Search
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Serve main SPA |
| GET | `/api/chats` | List all chats (WhatsApp + Telegram) with platform, language |
| GET | `/api/search?chat=&q=&sender=&from=&to=&type=&page=` | FTS5 search, 50/page |
| GET | `/api/search/all?q=&...` | Cross-chat search (all chats) |
| GET | `/api/context/<id>?chat=` | Surrounding messages |
| GET | `/api/stats?chat=` | Chat statistics |

### AI Chat
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/ai/status` | Provider availability |
| POST | `/api/ai/chat` | RAG Q&A (blocking) |
| POST | `/api/ai/chat/stream` | RAG Q&A (SSE streaming) |

### Processing
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/process/status?chat=` | Processing status with per-file detail |
| POST | `/api/process/start` | Start background task |
| GET | `/api/process/progress?chat=` | Poll active task progress |
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

### Auth (Web mode only)
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/auth/signup` | Email + password signup |
| POST | `/api/auth/login` | Email + password login |
| GET | `/api/auth/google` | Google OAuth redirect URL |
| POST | `/api/auth/refresh` | Refresh access token |
| GET | `/api/auth/me` | Current user profile |
| GET | `/login` | Login/signup page |
| GET | `/auth/callback` | OAuth callback handler |

### Upload
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/upload` | Upload ZIP (WhatsApp/Telegram export) |
| POST | `/api/upload/share` | PWA Share Target handler |

### Auth Middleware

`@require_auth` decorator: enforced only in web mode (when `SUPABASE_URL` is set). Local/desktop mode skips auth entirely. Uses Supabase JWT verification via `Authorization: Bearer <token>` header.

---

## 7. Frontend (`chat_search/templates/index.html`)

Single-page application, ~2800 lines, vanilla JS. 5 tabs:

1. **Search** - Full-text search with filters (sender, date, type), message cards, context viewer
2. **Management** - Per-chat processing status, action buttons, progress bars, file upload
3. **Settings** - Provider/model selection cards, API key management, hardware info, cost calculator
4. **Gallery** - Media browser with type filter and pagination
5. **Analytics** - Sender stats, monthly activity, hourly heatmap, media breakdown

**AI Chat Panel** - Floating sidebar with SSE streaming, source citations, thread context.

**Theme:** Light theme (#FAFAF8 background), teal/indigo gradient header, RTL Hebrew.

---

## 8. Config & Environment

### Environment Variables (`.env`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | For Anthropic provider | Vision + RAG |
| `OPENAI_API_KEY` | For OpenAI provider | Vision + RAG |
| `GOOGLE_API_KEY` | For Gemini provider | Vision + RAG |
| `SUPABASE_URL` | For web mode | Enables auth + cloud features |
| `SUPABASE_ANON_KEY` | For web mode | Client-side Supabase access |
| `SUPABASE_SERVICE_ROLE_KEY` | For web mode | Server-side Supabase access |
| `SUPABASE_DB_PASSWORD` | For DB management | Direct PostgreSQL access |
| `GOOGLE_OAUTH_CLIENT_ID` | For Google login | OAuth provider |
| `GOOGLE_OAUTH_CLIENT_SECRET` | For Google login | OAuth provider |
| `RAILWAY_TOKEN` | For deployment | Railway CLI access |

### Supabase Schema

```sql
profiles (id, email, display_name, plan, storage_used_mb, max_storage_mb)
user_chats (id, user_id, chat_name, platform, language, chat_type, status, ...)
processing_jobs (id, user_id, chat_id, task, status, progress, ...)
```

All tables have Row Level Security: each user sees only their own data.

---

## 9. Desktop App (`desktop/`)

Electron wrapper for Windows. Bundles Python + ffmpeg + Ollama.

**First-run setup** (4 steps with progress bars):
1. Download Whisper model (~500MB)
2. Download E5-large model (~2GB)
3. Install Ollama (silent)
4. Pull AI model (qwen2.5:7b)

**Key detail:** `start.js` solves a critical issue where `node_modules/electron` shadows the built-in Electron module. It temporarily moves the npm package and unsets `ELECTRON_RUN_AS_NODE` before launching `electron.exe`.

**User data:** `Documents/WhatsArch/chats/`, `AppData/WhatsArch/models/`, symlinked to app via junctions.

**Build:** `cd desktop && npm run setup-vendor && npm run build:win` → produces NSIS installer.

---

## 10. Deployment

### Railway (Production Web)

- **URL:** `https://whatsarch-production.up.railway.app`
- **Docker:** Python 3.11-slim + ffmpeg + CPU-only PyTorch
- **Entry:** Gunicorn with 2 workers, 4 threads
- **Auto-deploy:** Triggered on every GitHub push to main

### Local Development

```bash
python run.py                      # Process all chats + serve on :5000
python run.py --skip-transcribe    # Skip Whisper, use cached
python run.py --port 8080          # Different port
cd desktop && npm start            # Electron desktop app
```

---

## 11. Current Status & Known Issues

### Working
- Multi-provider Vision + RAG (Anthropic, OpenAI, Gemini, Ollama)
- WhatsApp + Telegram parsing with auto-detection
- 9-language support (auto-detect + dynamic prompts)
- SSE streaming for AI chat
- Cross-chat search
- Background processing with cancellation
- Upload ZIP via API + PWA Share Target
- Supabase Auth (Google OAuth + email/password)
- Railway deployment (live)
- Electron desktop wrapper (tested on Windows)

### Known Limitations
- Frontend is vanilla HTML/JS (~2800 lines) — React migration planned
- FTS5 trigram requires 3+ char queries
- Embeddings loaded fully into memory
- No re-ranking model after retrieval
- Desktop build not yet produced (setup-vendor + build:win needed)
- PWA Share Target needs testing on actual mobile device
