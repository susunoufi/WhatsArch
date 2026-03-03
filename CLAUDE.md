# WhatsArch - WhatsApp Chat Archive Search Engine

## 1. Project Overview

WhatsArch is a multi-chat WhatsApp export search engine with AI-powered Q&A. It processes exported WhatsApp chat folders (containing `_chat.txt` and media files), transcribes voice messages, describes images/videos using vision AI, extracts PDF text, indexes everything into a searchable database, and serves a web UI for full-text search + AI chat.

**Primary language:** Hebrew (RTL UI, Hebrew NLP, Hebrew stop words/morphology).

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, Flask |
| Database | SQLite + FTS5 (trigram tokenizer) |
| Embeddings | `intfloat/multilingual-e5-large` (1024-dim, 512 token limit) |
| Speech-to-text | faster-whisper (local, Whisper model) |
| Vision AI | Anthropic Claude Sonnet 4 (`claude-sonnet-4-20250514`) |
| RAG LLM | Claude Opus 4.6 (`claude-opus-4-6`) or OpenAI `gpt-4o-mini` fallback |
| PDF extraction | PyMuPDF (`pymupdf`) |
| Video processing | ffmpeg (frame extraction + audio extraction) |
| Frontend | Vanilla HTML/CSS/JS, WhatsApp-inspired dark theme, Font Awesome icons |

### Dependencies (`requirements.txt`)

```
faster-whisper, flask, tqdm, anthropic, openai,
python-dotenv, pymupdf, sentence-transformers
```

---

## 2. Architecture

### File Structure

```
WhatsArch/
  run.py                          # Main entry point - orchestrates everything
  .env                            # ANTHROPIC_API_KEY (loaded via python-dotenv)
  requirements.txt
  chat_search/
    __init__.py                   # Empty package marker
    parser.py                     # WhatsApp _chat.txt parser
    transcribe.py                 # Audio voice message transcription (Whisper)
    vision.py                     # Image/video/PDF understanding (Claude Vision + ffmpeg)
    indexer.py                    # SQLite FTS5 index + semantic embeddings
    chunker.py                   # Conversation-aware chunking (1-on-1 + group thread detection)
    ai_chat.py                   # RAG pipeline + Hebrew NLP + LLM client
    server.py                    # Flask web server + REST API
    process_manager.py           # Background processing orchestrator + progress tracking
    templates/
      index.html                 # Single-page frontend (all-in-one HTML/CSS/JS)
  chats/
    <chat_name>/                 # One folder per WhatsApp export
      _chat.txt                  # WhatsApp export text file
      *.opus, *.jpg, *.mp4, etc. # Media files
      data/
        chat.db                  # SQLite search index (messages + chunks tables)
        chat_chunk_embeddings.npy # Chunk semantic embeddings (numpy, 1024-dim)
        transcriptions.json      # Audio transcription cache
        descriptions.json        # Image + video visual description cache
        video_transcriptions.json # Video audio transcription cache
        pdf_texts.json           # PDF text extraction cache
```

### Module Connections

```
run.py
  -> transcribe.py      (Step 1: audio -> text)
  -> vision.py           (Step 2: images/videos/PDFs -> descriptions/text)
  -> parser.py           (Step 3: _chat.txt + caches -> messages + name mentions)
  -> parser.py           (Step 4: detect_chat_type -> 1on1/group)
  -> indexer.py           (Step 5: messages -> SQLite FTS5 + chat_metadata)
  -> chunker.py          (Step 6: messages -> conversation chunks, thread-aware for groups)
  -> indexer.py           (Step 7: chunks -> DB + chunk embeddings)
  -> server.py           (Step 8: Flask web server)
       -> indexer.py      (search, stats, context, semantic_search_chunks)
       -> ai_chat.py      (RAG pipeline - chunk-based, thread-aware retrieval)
            -> indexer.py  (hybrid chunk retrieval + thread boosting)
```

### Entry Point

`run.py:main()` - CLI with these flags:
- `--skip-transcribe` - Skip Whisper audio transcription
- `--skip-vision` - Skip image/video/PDF processing
- `--skip-embeddings` - Skip semantic embedding generation
- `--skip-chunking` - Skip conversation chunking step
- `--group-mode` - Force treat all chats as group chats (override auto-detection)
- `--1on1-mode` - Force treat all chats as 1-on-1 chats (override auto-detection)
- `--model <size>` - Whisper model: tiny, base, small (default), medium, large-v3
- `--port <port>` - Web server port (default: 5000)
- `--no-browser` - Don't auto-open browser
- `--chat <name>` - Process only a specific chat folder

---

## 3. Message Processing Pipeline

### Step 1: Audio Transcription (`chat_search/transcribe.py`)

- **Input:** `*AUDIO*.opus` files in chat directory
- **Engine:** `faster-whisper` with `WhisperModel(model_size, device="auto", compute_type="auto")`
- **Config:** `beam_size=5`, `vad_filter=True`, auto-detect language (no hardcoded `language=` param)
- **Output:** `{filename: {text, language}}` cached in `data/transcriptions.json`
- **Migration:** Old cache format (plain string values) auto-migrated to `{text, language: "he"}` on load
- **Resumable:** Saves after each file; skips already-cached files on re-run
- **Multi-language:** Whisper auto-detects language per file; detected language stored in cache

### Step 2: Vision Processing (`chat_search/vision.py`)

**Images** (`process_images()`):
- Scans `*.jpg`, `*.jpeg`, `*.png` (skips files with "STICKER" in name)
- Sends each image to Claude Vision (`claude-sonnet-4-20250514`, max_tokens=300)
- Prompt: "תאר את התמונה בעברית בקצרה (2-3 משפטים). אם יש טקסט גלוי בתמונה, ציין אותו במדויק."
- Rate limit: 0.3s delay between API calls
- Cache: `data/descriptions.json`

**Videos** (`process_videos()`):
- Scans `*.mp4`, `*.mov` (skips files starting with "GIF")
- Requires ffmpeg; warns and skips if not installed
- **Frame extraction** (`extract_key_frames()`): Adaptive rate based on duration:
  - <=30s: ~3 frames (interval = duration/4)
  - <=120s: every 15s
  - <=600s: every 30s
  - >600s: every 60s
  - Max 10 frames per video
- **Visual description** (`describe_video_frames()`): All frames sent in single Claude Vision call (max_tokens=500)
- **Audio transcription**: ffmpeg extracts WAV (16kHz mono), then Whisper transcribes (auto-detect language, same as audio step)
- Cache: descriptions in `data/descriptions.json`, audio transcriptions in `data/video_transcriptions.json` (format: `{filename: {text, language}}`)

**PDFs** (`process_pdfs()`):
- Scans `*.pdf`, `*.PDF`
- Uses PyMuPDF: extracts text from first 20 pages, truncates at 5000 chars
- No external API needed
- Cache: `data/pdf_texts.json`

### Step 3: Parsing (`chat_search/parser.py`)

- **Input:** `_chat.txt` + all transcription/description dicts
- **Regex:** `^\u200e?\[(\d{2}/\d{2}/\d{4}), (\d{1,2}:\d{2}:\d{2})\] (.+?): (.*)`
- Handles multi-line messages (continuation lines appended)
- Extracts `<attached: filename>` tags, classifies media type via `detect_media_type()`
- Joins transcription/description data by attachment filename lookup. Handles both old (plain string) and new (`{text, language}` dict) cache formats transparently.
- **Name mention detection** (`add_name_mentions()`): For each message, checks if text contains another sender's name. Adds `mentioned_sender: [list]` field. Used for group chat thread detection.
- **Output:** List of message dicts with keys:
  `date, time, datetime, sender, text, attachment, media_type, transcription, visual_description, video_transcription, pdf_text, mentioned_sender`

### Step 4: Chat Type Detection (`parser.py:detect_chat_type()`)

Auto-detects whether each chat is 1-on-1 or group using two signals:
1. **Sender count:** >2 unique senders → group
2. **System messages:** Scans for WhatsApp group indicators (English + Hebrew): "created group", "added", "joined using", "changed the group", "יצר את הקבוצה", "הוסיף", "הצטרף באמצעות", etc.

If EITHER signal is true → group chat. Can be overridden with `--group-mode` / `--1on1-mode` flags.

Stored in `chat_metadata` table: `{chat_type, unique_senders, total_messages}`.

### Step 5: Indexing (`chat_search/indexer.py`)

- Drops and rebuilds `chat.db` each run (no migration needed)
- All DB connections use `try/finally` for leak-safe cleanup
- `get_stats()` uses a single aggregated SQL query instead of multiple queries
- See Section 4 for full details

---

## 4. RAG & Search Implementation

### 4.1 Database Schema (`indexer.py:build_index()`)

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    datetime TEXT, sender TEXT, text TEXT,
    attachment TEXT, media_type TEXT,
    transcription TEXT, visual_description TEXT,
    video_transcription TEXT, pdf_text TEXT
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    text, transcription, sender,
    visual_description, video_transcription, pdf_text,
    content='messages', content_rowid='id',
    tokenize='trigram'
);
```

FTS5 column indices: text=0, transcription=1, sender=2, visual_description=3, video_transcription=4, pdf_text=5.

Trigram tokenizer: breaks text into overlapping 3-character chunks. Language-agnostic (works for Hebrew without stemming). Minimum query length for FTS5: 3 chars; shorter queries fall back to LIKE.

### 4.2 Chunking Strategy (`chunker.py:segment_into_chunks()`)

Conversation-aware chunking groups individual messages into overlapping conversation chunks for better semantic embeddings. Most WhatsApp messages are 1-5 words with near-meaningless individual embeddings.

#### 1-on-1 Chat Chunking

**Algorithm: Time-Gap + Sliding Window + Bridging**
1. **Session splitting:** Split when gap between consecutive messages > 30 minutes
2. **Reply-after-gap bridging:** If a new session starts with <4 short messages (<50 chars each), they're appended to the previous session AND prepended to the next. Handles "כן בוא נעשה את זה" reply-after-a-day patterns. Tagged `bridging: true`.
3. **Sliding window within sessions:** 15-message windows, 5-message overlap, slide by 10
4. **Long content adjustment:** If >30% of messages are >200 chars, window size reduced to avoid token overflow
5. **Small sessions:** If session < 8 messages, keep as single chunk
6. **Tail handling:** Skip remainder if < overlap messages (covered by previous overlap)

#### Group Chat Chunking (Thread Detection)

Group chats have interleaved parallel conversations. Thread detection runs per time-session:

**Thread detection signals:**
1. **Sender addressing:** Messages containing another sender's name → link to that sender's thread
2. **Participant pair continuity:** A→B→A alternating patterns within 3 minutes → same thread
3. **Semantic similarity:** E5-large embeddings between nearby messages (window=10, threshold=0.6). Same model used for chunk embeddings, no extra loading.
4. **Time proximity per sender pair:** Messages from the same sender pair within 3 minutes in the same thread component get merged

**Thread assembly:** Union-Find data structure merges message indices based on all signals. Messages in components with ≤2 messages from ≤1 sender become "ambient" (general announcements). Ambient messages are duplicated into all thread chunks that overlap their time window.

**Per-thread chunking:** Same sliding window (15 msg / 5 overlap) applied within each thread.

**Chunk format** (`combined_text`):
```
[Sender HH:MM] message text [תמלול: ...] [תיאור חזותי: ...]
```

**Database tables:**
```sql
CREATE TABLE chat_metadata (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    start_message_id INTEGER, end_message_id INTEGER,
    start_datetime TEXT, end_datetime TEXT,
    combined_text TEXT, senders TEXT,
    message_count INTEGER, has_media BOOLEAN,
    chat_type TEXT DEFAULT '1on1',
    thread_id INTEGER, thread_participants TEXT,
    bridging BOOLEAN DEFAULT FALSE
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    combined_text, senders, content='chunks', content_rowid='id', tokenize='trigram'
);
```

### 4.3 Embeddings (`indexer.py:build_chunk_embeddings()`)

- **Model:** `intfloat/multilingual-e5-large`
  - 1024-dimensional vectors
  - 512 token limit (15 Hebrew messages ≈ 152 tokens)
  - Requires `"passage: "` prefix for passages, `"query: "` prefix for queries
  - Multilingual (strong Hebrew support)
- **Encoding:** `batch_size=32`, `normalize_embeddings=True`
- **Storage:** `data/chat_chunk_embeddings.npy` - numpy array of shape `(num_chunks, 1024)`
  - Row index `i` corresponds to chunk `id = i + 1` (1-indexed)
- **Caching:** Module-level `_chunk_embedding_cache` dict; loaded once per `db_path`
- **Model caching:** Singleton in `_model_cache`; loaded lazily on first use

### 4.4 Search Types

**Web UI search** (`indexer.py:search()`):
- FTS5 full-text search with `snippet()` highlighting (`<mark>` tags)
- Supports filters: sender, date_from, date_to, search_type (all/text/transcription/visual/pdf)
- Pagination: 50 results per page
- Fallback: queries < 3 chars use LIKE with manual highlighting
- Operates on individual messages (unchanged)

**RAG retrieval** (`ai_chat.py:retrieve_chunks()`):
5-tier hybrid search scored on **chunk_ids**:

| Tier | Strategy | Weight | Details |
|------|----------|--------|---------|
| 1 | **Semantic search on chunks** (primary) | sim*20 pts | E5-large with "query: " prefix; full question + keyword pairs/triplets (capped at 15 queries); round-robin merge; `top_k=100` |
| 2 | FTS5 on chunks_fts | 4 pts | All keywords in one FTS5 query on chunk text |
| 3 | FTS5 per-keyword on messages_fts | 1-2 pts | Map matched message_ids → chunk_ids via range lookup |
| 4 | LIKE substring on messages | 2 pts | Map matched message_ids → chunk_ids |
| 5 | Intersection boost | +3 or +6 pts | Chunks matching 2+ keyword roots get +3, 3+ get +6 |

**Message-to-chunk mapping:** All chunk ranges `(id, start, end)` loaded and cached in memory (sorted by `start_message_id`). Uses `bisect` binary search for O(log n) lookup instead of linear scan.

**Thread boosting (group chats):** After scoring, if a high-scoring chunk (score >= 8) has a `thread_id`, all other chunks with the same `thread_id` get +2 boost. This pulls in related conversation context from the same thread.

**Deduplication:** Skip chunks whose message range overlaps >30% with any already-selected chunk.

**Group chat ordering:** Selected chunks are sorted by `(thread_id, start_datetime)` so the LLM sees coherent threads grouped together.

**Semantic search details** (`indexer.py:semantic_search_chunks()`):
- Accepts single query or list of queries
- E5-large requires `"query: "` prefix for queries, `"passage: "` prefix for chunk text
- Cosine similarity via matrix multiply: `query_embeddings @ chunk_embeddings.T`
- **Round-robin merge:** Takes rank-1 from query 1, rank-1 from query 2, ..., then rank-2 from each, etc.
- Score threshold: 0.15
- Max 12 chunks returned (each contains ~15 messages of context)

### 4.5 Hebrew NLP (`ai_chat.py`)

- **Stop words:** ~75 common Hebrew words (prepositions, conjunctions, pronouns, auxiliaries, fillers)
- **Prefix stripping** (`strip_hebrew_prefix()`): Removes prefixes like ה, ב, ל, מ, כ, ו, ש and multi-char combinations (וכש, שמ, שה, etc.). Sorted longest-first. Min 2 chars remaining.
- **Suffix stripping** (`strip_hebrew_suffix()`): Removes plural/gender endings (ים, ות, יות, ון, ית). Fixes sofit letters (כ→ך, מ→ם, נ→ן, פ→ף, צ→ץ).
- **Keyword expansion** (`_expand_keywords()`): For each keyword, generates: original, prefix-stripped, suffix-stripped, both-stripped. Picks shortest as "root".

### 4.6 Context Assembly (`ai_chat.py:format_chunks_for_prompt()`)

- Each chunk IS the context (no separate `get_context()` needed — ~15 messages per chunk)
- Deduplication: chunks with >30% message range overlap with already-selected chunks are skipped
- Max 12 result chunks
- **Thread grouping:** For group chats, chunks with the same `thread_id` are grouped under a header showing thread number and participants. Non-threaded chunks appear first.
- Format per message: `[datetime] sender (#id): text [תמלול: ...] [תיאור חזותי: ...] [תמלול וידאו: ...] [טקסט PDF: ...]`
- Text truncation: text=500, transcription=300, visual_description=300, video_transcription=300, pdf_text=500

### 4.7 RAG Pipeline (`ai_chat.py:ask()`)

1. `extract_keywords(question)` - Remove stop words, get meaningful terms
2. `retrieve_chunks(db_path, question, max_results=12)` - 5-tier hybrid chunk search
3. `format_chunks_for_prompt(chunk_groups, chat_name)` - Build context string
4. Append last 4 history items (2 Q&A exchanges)
5. Call LLM via singleton `_get_llm_client()` with system prompt + context + question (max_tokens=2048)
6. Extract source citations from chunk messages
7. Return `{answer, sources, keywords, provider}`

**System prompt** instructs the LLM to:
- Always respond in Hebrew
- Be concise
- Cite message IDs as `[#1234]`
- Acknowledge when info is insufficient
- Note when answers are based on image/video descriptions

---

## 5. API & Routes (`chat_search/server.py`)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Serve `index.html` |
| GET | `/api/chats` | List all chats with ready status and message counts |
| GET | `/api/search?chat=&q=&sender=&from=&to=&type=&page=` | FTS5 search with filters, 50/page |
| GET | `/api/context/<message_id>?chat=&before=5&after=5` | Get surrounding messages |
| GET | `/api/stats?chat=` | Chat statistics (counts, date range, senders, chat_type, chunk_count) |
| GET | `/api/ai/status` | Check AI provider availability |
| POST | `/api/ai/chat` | RAG Q&A. Body: `{chat, question, history}` |
| GET | `/api/vision/status?chat=` | Vision processing progress (total/processed counts) |
| GET | `/api/thumbnail/<chat_name>/<filename>` | On-demand video thumbnail (first frame) |
| GET | `/api/process/status?chat=` | Full processing status with per-file detail |
| POST | `/api/process/start` | Trigger background processing. Body: `{chat, task}` |
| GET | `/api/process/progress?chat=` | Lightweight polling for active task progress |
| POST | `/api/process/stop` | Cancel running task. Body: `{chat}` |
| GET | `/media/<chat_name>/<filename>` | Serve media files (path-traversal protected) |

MIME type registration: `.opus` -> `audio/ogg` for browser playback.

**Security:** Media and thumbnail endpoints sanitize `chat_name` and `filename` with `os.path.basename()` to prevent path traversal attacks.

---

## 6. Frontend (`chat_search/templates/index.html`)

Single-page application, vanilla JS, no framework. WhatsApp-inspired dark theme (#0b141a background, #00a884 accent green, #53bdeb accent blue).

### Key UI Sections

- **Header:** Chat selector dropdown, search bar, filter controls (sender, date range, search type dropdown)
- **Stats banner:** Total messages, date range, per-sender counts, media counts (audio/images/videos/PDFs with description counts)
- **Results area:** Message cards with sender badges (color-coded), timestamps, highlighted text, media players, transcription boxes, visual description boxes (blue border), PDF text boxes (red border)
- **Context viewer:** Expandable ±5 message window around any result
- **AI chat sidebar:** Question input, streaming-style answer display, source citations with links to messages

### Search Type Options

- הכל (All) - text + transcriptions + descriptions + PDFs
- טקסט בלבד (Text only)
- תמלולים (Transcriptions)
- תיאורי תמונות/וידאו (Visual descriptions)
- תוכן PDF (PDF content)

### RTL Support

Full right-to-left layout for Hebrew. `direction: rtl` on body, sender badges and timestamps positioned accordingly.

---

## 7. Config & Environment

### Required Environment Variables

| Variable | Required | Used By | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | For vision + AI chat | `vision.py`, `ai_chat.py` | Claude Vision (image/video descriptions) and RAG chat |
| `OPENAI_API_KEY` | Fallback only | `ai_chat.py` | Alternative LLM if no Anthropic key |

Both loaded from `.env` file via `python-dotenv` (loaded in `run.py` and `ai_chat.py`).

### External Services

| Service | Used For | Required |
|---------|----------|----------|
| Anthropic Claude API | Image/video descriptions (Sonnet 4), RAG chat (Opus 4.6) | For vision + AI features |
| OpenAI API | Fallback RAG chat (gpt-4o-mini) | Only if no Anthropic key |
| ffmpeg | Video frame/audio extraction | For video processing only |
| HuggingFace Hub | sentence-transformers model download (first run) | For semantic search |

### System Requirements

- Python 3.10+
- ffmpeg in PATH (for video processing; `winget install ffmpeg` on Windows)
- ~500MB for Whisper "small" model (downloaded on first run)
- ~2GB for intfloat/multilingual-e5-large embedding model (downloaded on first run)

---

## 8. Current Limitations / TODOs

### Architecture Limitations
- **No incremental indexing:** `build_index()` drops and recreates the entire database each run. Adding new messages requires full reprocessing.
- **No streaming responses:** AI chat endpoint returns full response (no SSE/WebSocket streaming).
- **Single-threaded processing:** Images/videos processed sequentially with 0.3s delay. No parallel API calls.
- **Memory:** Embeddings loaded fully into memory (`numpy.load()`). For very large chats (100k+ messages), this could be significant.

### Search Limitations
- FTS5 trigram requires minimum 3-character queries. Shorter queries fall back to slower LIKE search.
- No re-ranking model after retrieval (relies on score-based sorting from hybrid search).
- Semantic search score threshold hardcoded at 0.15.

### Media Handling
- `.webp` images (stickers) are intentionally skipped by vision processing.
- GIF-converted `.mp4` files are skipped (filename starts with "GIF").
- Video audio extraction may fail silently if ffmpeg encounters unsupported codecs.
- PDF text extraction capped at 20 pages and 5000 characters.

### Frontend
- No offline/PWA support.
- No message threading or conversation view (flat list only).
- Search results sorted by datetime DESC only (no relevance sorting in web UI search).

### Multi-Chat
- Each chat is fully independent (separate DB, separate embeddings). No cross-chat search.
