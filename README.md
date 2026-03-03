<p align="center">
  <img src="logo/whatsarch-logo-full.svg" alt="WhatsArch" width="400"/>
</p>

<p align="center">
  <strong>WhatsApp Chat Archive Search Engine with AI-Powered Q&A</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a> •
  <a href="#tech-stack">Tech Stack</a>
</p>

---

WhatsArch turns your exported WhatsApp chats into a fully searchable, AI-powered archive. It transcribes voice messages, describes images and videos, extracts PDF text, and indexes everything into a local search engine with a WhatsApp-inspired web UI.

## Features

- **Full-Text Search** — Trigram-based search across messages, transcriptions, image descriptions, and PDF text
- **AI Chat (RAG)** — Ask natural language questions about your conversations and get sourced answers
- **Voice Message Transcription** — Local Whisper-based speech-to-text (no cloud upload)
- **Image & Video Understanding** — Claude Vision describes photos and videos in Hebrew
- **PDF Text Extraction** — Searchable text from shared PDF documents
- **Multi-Chat Support** — Search across multiple WhatsApp exports independently
- **Group Chat Thread Detection** — Automatically detects conversation threads in group chats
- **Hebrew-First** — RTL UI, Hebrew NLP, Hebrew stop words and morphology

## How It Works

```
BUILD (once):     WhatsApp Export → Transcribe/Describe → Parse
                  → Database → Cut into Chunks → Embed Chunks

SEARCH (each Q):  Question → Find best chunks → Feed to AI → Answer
```

1. **Parse** your WhatsApp `_chat.txt` export files
2. **Transcribe** voice messages locally using Whisper
3. **Describe** images and videos using Claude Vision AI
4. **Extract** text from PDFs
5. **Index** everything into SQLite with FTS5 trigram search
6. **Chunk** conversations into overlapping ~15-message windows for semantic search
7. **Embed** chunks using multilingual E5-large (1024-dim vectors)
8. **Search** with 5-tier hybrid retrieval (semantic + full-text + keyword + substring + intersection boost)
9. **Answer** questions using RAG with Claude Opus

See [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for a visual walkthrough.

## Installation

### Windows (Quick Start)

1. Download or clone this repository
2. Double-click **`setup.bat`** — it installs Python, ffmpeg, and all dependencies
3. Export your WhatsApp chats and place the folders in the `chats/` directory
4. Double-click **`WhatsArch.bat`** to start

### Manual Setup

**Requirements:**
- Python 3.10+
- ffmpeg (for video processing)

```bash
# Clone the repo
git clone https://github.com/susunoufi/WhatsArch.git
cd WhatsArch

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Create .env file with your API key
echo "ANTHROPIC_API_KEY=your-key-here" > .env

# Create chats directory
mkdir chats
```

### Exporting WhatsApp Chats

1. Open a WhatsApp chat
2. Tap **⋮** → **More** → **Export chat** → **Include media**
3. Save the zip file and extract it into `chats/YourChatName/`

The folder should contain `_chat.txt` and any media files (`.opus`, `.jpg`, `.mp4`, etc.).

## Usage

```bash
# Process all chats and start the web server
python run.py

# Skip slow steps if already processed
python run.py --skip-transcribe --skip-vision

# Use a specific Whisper model (tiny/base/small/medium/large-v3)
python run.py --model large-v3

# Process only one chat
python run.py --chat "ChatName"

# Custom port
python run.py --port 8080

# Force chat type detection
python run.py --group-mode    # Treat all as group chats
python run.py --1on1-mode     # Treat all as 1-on-1 chats
```

Then open `http://localhost:5000` in your browser.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, Flask |
| Database | SQLite + FTS5 (trigram tokenizer) |
| Embeddings | `intfloat/multilingual-e5-large` (1024-dim) |
| Speech-to-Text | faster-whisper (local) |
| Vision AI | Claude Sonnet 4 |
| RAG LLM | Claude Opus 4.6 |
| PDF Extraction | PyMuPDF |
| Video Processing | ffmpeg |
| Frontend | Vanilla HTML/CSS/JS |

## Configuration

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | For AI features | Image/video descriptions + AI chat |
| `OPENAI_API_KEY` | Optional fallback | Alternative LLM for AI chat |

API keys are stored in a `.env` file in the project root.

**Note:** AI models (~2.5 GB) are downloaded automatically on first run. Voice transcription and semantic search work fully offline after that.

## Project Structure

```
WhatsArch/
  run.py                  # Main entry point
  setup.bat               # Windows installer
  WhatsArch.bat           # Windows launcher
  requirements.txt
  chat_search/
    parser.py             # WhatsApp chat parser
    transcribe.py         # Whisper voice transcription
    vision.py             # Claude Vision (images/videos/PDFs)
    indexer.py            # SQLite FTS5 + semantic embeddings
    chunker.py            # Conversation-aware chunking
    ai_chat.py            # RAG pipeline + Hebrew NLP
    server.py             # Flask web server + API
    process_manager.py    # Background processing
    templates/
      index.html          # Web UI (single-page app)
  chats/                  # Your WhatsApp exports go here
    <chat_name>/
      _chat.txt
      *.opus, *.jpg, ...
      data/               # Generated index & caches
```

## License

This project is for personal use. All chat data stays local on your machine.
