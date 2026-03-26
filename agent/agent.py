"""WhatsArch Local Agent — runs on user's machine for local compute.

The web UI (whatsarch.com) communicates with this agent for:
- Processing large chat exports directly from the local filesystem
- Running Whisper transcription locally (CPU is fine)
- Running Vision/RAG via proxy through Railway (admin's API keys)
  or directly with user's own API keys
- Serving search, media, and AI chat for locally-processed chats

The agent runs on http://localhost:11470
"""

import base64
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# Add project root to sys.path so we can import chat_search modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify, send_from_directory, abort, Response
from flask_cors import CORS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB — local, no limit
CORS(app, origins=["https://whatsarch-production.up.railway.app", "http://localhost:*", "https://*.railway.app"])

# Agent version
VERSION = "2.0.0"

# Data directory
DATA_DIR = Path.home() / "Documents" / "WhatsArch"
CHATS_DIR = DATA_DIR / "chats"
CHATS_DIR.mkdir(parents=True, exist_ok=True)

# Agent settings file
SETTINGS_PATH = DATA_DIR / "settings.json"


def _load_agent_settings() -> dict:
    """Load agent-specific settings (proxy URL, user API keys, etc.)."""
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_agent_settings(settings: dict):
    """Save agent settings."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Project-root shim for chat_search modules
# ---------------------------------------------------------------------------
# chat_search.config.load_settings(project_root) expects settings.json at project_root.
# For the agent, project_root is DATA_DIR (parent of chats/).
# We ensure a settings.json exists there.

def _get_project_root() -> str:
    """Return the project root for chat_search modules (parent of chats dir)."""
    return str(DATA_DIR)


def _ensure_settings_file():
    """Ensure a settings.json exists for chat_search modules."""
    settings_path = DATA_DIR / "settings.json"
    if not settings_path.exists():
        from chat_search.config import DEFAULT_SETTINGS
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, ensure_ascii=False, indent=2)


_ensure_settings_file()


# ===========================================================================
# Health & Info Endpoints
# ===========================================================================

@app.route("/status")
def status():
    """Health check — web app pings this to detect if agent is running."""
    chats = []
    for d in sorted(CHATS_DIR.iterdir()):
        if d.is_dir():
            has_wa = (d / "_chat.txt").exists()
            has_tg = (d / "result.json").exists()
            if has_wa or has_tg:
                chats.append(d.name)
    return jsonify({
        "status": "running",
        "version": VERSION,
        "platform": platform.system(),
        "data_dir": str(DATA_DIR),
        "chats_dir": str(CHATS_DIR),
        "chats": chats,
    })


@app.route("/hardware")
def hardware():
    """Return local hardware info."""
    info = {
        "cpu": platform.processor() or "Unknown",
        "ram_gb": 0,
        "gpu": "Unknown",
        "gpu_dedicated": False,
        "ollama_installed": shutil.which("ollama") is not None,
        "ffmpeg_installed": shutil.which("ffmpeg") is not None,
        "whisper_available": False,
    }

    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
    except ImportError:
        pass

    # GPU (Windows)
    try:
        result = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "Name,AdapterRAM"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip() and "Name" not in l]
        if lines:
            info["gpu"] = lines[0].split("  ")[0].strip()
            info["gpu_dedicated"] = "nvidia" in info["gpu"].lower() or "radeon" in info["gpu"].lower()
    except Exception:
        pass

    try:
        from faster_whisper import WhisperModel
        info["whisper_available"] = True
    except ImportError:
        pass

    return jsonify(info)


@app.route("/ollama/status")
def ollama_status():
    """Check if Ollama is running and what models are available."""
    try:
        import urllib.request
        res = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(res.read())
        models = [m["name"] for m in data.get("models", [])]
        return jsonify({"running": True, "models": models})
    except Exception:
        return jsonify({"running": False, "models": []})


# ===========================================================================
# Chat Listing
# ===========================================================================

@app.route("/api/chats")
def api_chats():
    """List all local chats with their status."""
    from chat_search import indexer

    result = []
    for name in sorted(os.listdir(str(CHATS_DIR))):
        chat_dir = os.path.join(str(CHATS_DIR), name)
        if not os.path.isdir(chat_dir):
            continue
        has_whatsapp = os.path.exists(os.path.join(chat_dir, "_chat.txt"))
        has_telegram = os.path.exists(os.path.join(chat_dir, "result.json"))
        if not has_whatsapp and not has_telegram:
            continue
        db_path = os.path.join(chat_dir, "data", "chat.db")
        ready = os.path.exists(db_path)
        plat = "telegram" if has_telegram else "whatsapp"
        info = {"name": name, "ready": ready, "platform": plat, "source": "local"}
        if ready:
            try:
                stats = indexer.get_stats(db_path)
                info["total_messages"] = stats["total_messages"]
                metadata = indexer.get_chat_metadata(db_path)
                info["language"] = metadata.get("language", "he")
            except Exception:
                info["total_messages"] = 0
                info["language"] = "he"
        result.append(info)
    return jsonify(result)


# ===========================================================================
# Upload / Load Local Files
# ===========================================================================

@app.route("/browse/folder", methods=["POST"])
def browse_folder():
    """Open a native folder picker dialog and return the selected path."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        def pick():
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            folder = filedialog.askdirectory(title="Select chat folder")
            root.destroy()
            return folder

        # tkinter must run on main thread on macOS, but Flask runs in threads.
        # On Windows it works fine from any thread.
        folder = pick()
        if not folder:
            return jsonify({"error": "cancelled"}), 400

        return jsonify({"path": folder})
    except Exception as e:
        return jsonify({"error": f"Folder picker failed: {e}"}), 500


@app.route("/upload/local", methods=["POST"])
def upload_local():
    """Load a chat from a local file path (no network transfer needed)."""
    data = request.get_json()
    if not data or not data.get("path"):
        return jsonify({"error": "Missing path"}), 400

    source_path = Path(data["path"])
    if not source_path.exists():
        return jsonify({"error": f"File not found: {source_path}"}), 404

    import zipfile

    if source_path.suffix.lower() == ".zip":
        chat_name = source_path.stem
        dest = CHATS_DIR / chat_name
        if dest.exists():
            chat_name = chat_name + "_" + str(int(source_path.stat().st_mtime))
            dest = CHATS_DIR / chat_name

        with zipfile.ZipFile(source_path, "r") as zf:
            zf.extractall(dest)

        # Check if extracted to a subdirectory
        entries = list(dest.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            sub = entries[0]
            for item in sub.iterdir():
                shutil.move(str(item), str(dest / item.name))
            sub.rmdir()

        return jsonify({"status": "ok", "chat_name": chat_name, "path": str(dest)})

    elif source_path.is_dir():
        chat_name = source_path.name
        dest = CHATS_DIR / chat_name
        if dest.exists():
            chat_name = chat_name + "_new"
            dest = CHATS_DIR / chat_name
        # Try junction (no admin needed on Windows), then symlink, then copy
        linked = False
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(dest), str(source_path)],
                    capture_output=True, timeout=10
                )
                if dest.exists():
                    linked = True
            except Exception:
                pass
        if not linked:
            try:
                os.symlink(str(source_path), str(dest), target_is_directory=True)
                linked = True
            except OSError:
                pass
        if not linked:
            # Last resort: copy the entire folder
            shutil.copytree(str(source_path), str(dest))
        return jsonify({"status": "ok", "chat_name": chat_name, "path": str(dest)})

    else:
        return jsonify({"error": "Path must be a ZIP file or directory"}), 400


@app.route("/upload/zip", methods=["POST"])
def upload_zip():
    """Upload a ZIP file directly from the browser (multipart form upload).

    Unlike /upload/local (which takes a path), this accepts a file upload.
    Used when the browser sends a ZIP to the local agent instead of Railway.
    """
    import zipfile
    import tempfile

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    temp_dir = tempfile.mkdtemp()
    temp_zip = os.path.join(temp_dir, "upload.zip")
    try:
        file.save(temp_zip)

        if not zipfile.is_zipfile(temp_zip):
            return jsonify({"error": "Not a valid ZIP file"}), 400

        chat_name = Path(file.filename).stem
        dest = CHATS_DIR / chat_name
        if dest.exists():
            chat_name = chat_name + "_" + str(int(time.time()))
            dest = CHATS_DIR / chat_name

        with zipfile.ZipFile(temp_zip, "r") as zf:
            zf.extractall(dest)

        # Flatten if single subdirectory
        entries = list(dest.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            sub = entries[0]
            for item in sub.iterdir():
                shutil.move(str(item), str(dest / item.name))
            sub.rmdir()

        # Update agent chats set
        return jsonify({"status": "ok", "chat_name": chat_name, "path": str(dest)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# Processing Pipeline
# ===========================================================================

@app.route("/api/process/start", methods=["POST"])
def api_process_start():
    """Trigger a background processing step for a local chat."""
    data = request.get_json()
    if not data:
        abort(400, "Missing JSON body")

    chat_name = data.get("chat", "").strip()
    task = data.get("task", "").strip()

    if not chat_name or not task:
        abort(400, "Missing chat or task")

    valid_tasks = {"transcribe", "images", "videos", "pdfs", "index", "embeddings"}
    if task not in valid_tasks:
        abort(400, f"Invalid task. Must be one of: {', '.join(valid_tasks)}")

    chat_dir = os.path.join(str(CHATS_DIR), chat_name)
    if not os.path.isdir(chat_dir):
        abort(404, f"Chat '{chat_name}' not found")

    from chat_search import process_manager
    started = process_manager.start_processing(chat_name, task, str(CHATS_DIR))
    if not started:
        return jsonify({"error": "Task already running for this chat"}), 409

    return jsonify({"status": "started", "task": task})


@app.route("/api/process/progress")
def api_process_progress():
    """Lightweight polling endpoint for active task progress."""
    chat_name = request.args.get("chat", "").strip()
    if not chat_name:
        abort(400, "Missing chat parameter")

    from chat_search import process_manager
    task_info = process_manager.get_task_status(chat_name)
    return jsonify(task_info or {"status": "idle"})


@app.route("/api/process/stop", methods=["POST"])
def api_process_stop():
    """Request cancellation of a running processing task."""
    data = request.get_json()
    if not data:
        abort(400, "Missing JSON body")

    chat_name = data.get("chat", "").strip()
    if not chat_name:
        abort(400, "Missing chat parameter")

    from chat_search import process_manager
    stopped = process_manager.stop_processing(chat_name)
    if not stopped:
        return jsonify({"error": "No running task for this chat"}), 404

    return jsonify({"status": "stopping", "chat": chat_name})


@app.route("/api/process/status")
def api_process_status():
    """Get full processing status for a chat (file counts, progress, per-file detail)."""
    chat_name = request.args.get("chat", "").strip()
    if not chat_name:
        abort(400, "Missing chat parameter")

    chat_dir = os.path.join(str(CHATS_DIR), chat_name)
    if not os.path.isdir(chat_dir):
        abort(404, f"Chat '{chat_name}' not found")

    from chat_search import process_manager
    status_data = process_manager.get_processing_status(chat_dir)
    return jsonify(status_data)


# ===========================================================================
# Search
# ===========================================================================

def _get_chat_paths(chat_name):
    """Return (chat_dir, db_path) for a given chat name, or abort 404."""
    chat_dir = os.path.join(str(CHATS_DIR), chat_name)
    db_path = os.path.join(chat_dir, "data", "chat.db")
    if not os.path.isdir(chat_dir) or not os.path.exists(db_path):
        abort(404, f"Chat '{chat_name}' not found or not indexed")
    return chat_dir, db_path


@app.route("/api/search")
def api_search():
    """Full-text search within a local chat."""
    from chat_search import indexer

    chat_name = request.args.get("chat", "").strip()
    if not chat_name:
        return jsonify({"results": [], "total": 0, "page": 1})

    _, db_path = _get_chat_paths(chat_name)

    q = request.args.get("q", "").strip()
    sender = request.args.get("sender", "").strip()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    search_type = request.args.get("type", "all").strip()
    page = int(request.args.get("page", 1))

    if not q:
        return jsonify({"results": [], "total": 0, "page": 1})

    results, total = indexer.search(
        db_path, q, sender=sender, date_from=date_from, date_to=date_to,
        page=page, search_type=search_type
    )

    return jsonify({
        "results": results,
        "total": total,
        "page": page,
        "per_page": 50,
    })


@app.route("/api/search/all")
def api_search_all():
    """Search across all local chats."""
    from chat_search import indexer

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": [], "total": 0})

    sender = request.args.get("sender", "").strip()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    search_type = request.args.get("type", "all").strip()
    page = int(request.args.get("page", 1))

    all_results = []
    for name in sorted(os.listdir(str(CHATS_DIR))):
        chat_dir = os.path.join(str(CHATS_DIR), name)
        db_path = os.path.join(chat_dir, "data", "chat.db")
        if not os.path.isdir(chat_dir) or not os.path.exists(db_path):
            continue
        try:
            results, total = indexer.search(
                db_path, q, sender=sender, date_from=date_from, date_to=date_to,
                page=1, per_page=20, search_type=search_type
            )
            for r in results:
                r["chat_name"] = name
            all_results.extend(results)
        except Exception:
            continue

    all_results.sort(key=lambda r: r.get("relevance_score", 0))

    per_page = 50
    start = (page - 1) * per_page
    end = start + per_page

    return jsonify({
        "results": all_results[start:end],
        "total": len(all_results),
        "page": page,
        "per_page": per_page,
    })


@app.route("/api/context/<int:message_id>")
def api_context(message_id):
    """Get surrounding messages for context."""
    from chat_search import indexer

    chat_name = request.args.get("chat", "").strip()
    if not chat_name:
        abort(400, "Missing chat parameter")

    _, db_path = _get_chat_paths(chat_name)

    before = int(request.args.get("before", 5))
    after = int(request.args.get("after", 5))
    messages = indexer.get_context(db_path, message_id, before, after)
    return jsonify({"messages": messages, "focus_id": message_id})


@app.route("/api/stats")
def api_stats():
    """Get chat statistics."""
    from chat_search import indexer

    chat_name = request.args.get("chat", "").strip()
    if not chat_name:
        abort(400, "Missing chat parameter")

    _, db_path = _get_chat_paths(chat_name)
    stats = indexer.get_stats(db_path)

    metadata = indexer.get_chat_metadata(db_path)
    stats["chat_type"] = metadata.get("chat_type", "1on1")

    # Chunk count
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM chunks")
        stats["chunk_count"] = c.fetchone()[0]
        conn.close()
    except Exception:
        stats["chunk_count"] = 0

    return jsonify(stats)


# ===========================================================================
# AI Chat (RAG)
# ===========================================================================

@app.route("/api/ai/status")
def api_ai_status():
    """Check AI provider availability."""
    from chat_search import ai_chat, config

    info = ai_chat.LLMClient.get_provider_info()
    settings = config.load_settings(_get_project_root())
    info["current_rag_provider"] = settings.get("rag_provider")
    info["current_rag_model"] = settings.get("rag_model")

    # Check if proxy is configured
    agent_settings = _load_agent_settings()
    if agent_settings.get("use_proxy_rag"):
        info["proxy_configured"] = True
        info["providers"].append("proxy")

    return jsonify(info)


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """AI chat endpoint — RAG pipeline (uses local or proxy)."""
    from chat_search import ai_chat, indexer

    data = request.get_json()
    if not data:
        abort(400, "Missing JSON body")

    chat_name = data.get("chat", "").strip()
    question = data.get("question", "").strip()
    history = data.get("history", [])

    if not chat_name or not question:
        abort(400, "Missing chat or question")

    _, db_path = _get_chat_paths(chat_name)

    try:
        metadata = indexer.get_chat_metadata(db_path)
        language = metadata.get("language", "he")
        result = ai_chat.ask(db_path, question, chat_name, history,
                             project_root=_get_project_root(), language=language)
        return jsonify(result)
    except RuntimeError as e:
        # Try proxy fallback
        return _proxy_rag_fallback(db_path, chat_name, question, history, str(e))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/chat/stream", methods=["POST"])
def api_ai_chat_stream():
    """Streaming AI chat endpoint using Server-Sent Events."""
    from chat_search import ai_chat, indexer

    data = request.get_json()
    if not data:
        abort(400, "Missing JSON body")

    chat_name = data.get("chat", "").strip()
    question = data.get("question", "").strip()
    history = data.get("history", [])

    if not chat_name or not question:
        abort(400, "Missing chat or question")

    _, db_path = _get_chat_paths(chat_name)

    def generate():
        try:
            metadata = indexer.get_chat_metadata(db_path)
            language = metadata.get("language", "he")
            for chunk in ai_chat.ask_stream(db_path, question, chat_name, history,
                                            project_root=_get_project_root(), language=language):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _proxy_rag_fallback(db_path, chat_name, question, history, original_error):
    """If local AI fails, try proxying through Railway."""
    agent_settings = _load_agent_settings()
    railway_url = agent_settings.get("railway_url", "").rstrip("/")
    auth_token = agent_settings.get("auth_token", "")

    if not railway_url:
        return jsonify({"error": f"Local AI not available: {original_error}. Configure Railway proxy or API keys."}), 503

    try:
        import urllib.request
        # Retrieve chunks locally, send context to proxy
        from chat_search import ai_chat, indexer

        metadata = indexer.get_chat_metadata(db_path)
        language = metadata.get("language", "he")
        chunk_groups = ai_chat.retrieve_chunks(db_path, question, max_results=12)
        context_text = ai_chat.format_chunks_for_prompt(chunk_groups, chat_name)

        payload = json.dumps({
            "question": question,
            "context": context_text,
            "chat_name": chat_name,
            "language": language,
            "history": history[-4:] if history else [],
        }).encode()

        req = urllib.request.Request(
            f"{railway_url}/api/proxy/rag",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_token}" if auth_token else "",
            },
        )
        res = urllib.request.urlopen(req, timeout=120)
        result = json.loads(res.read())
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Both local and proxy RAG failed. Local: {original_error}. Proxy: {str(e)}"}), 503


# ===========================================================================
# Media Serving
# ===========================================================================

@app.route("/media/<path:chat_and_file>")
def serve_media(chat_and_file):
    """Serve media files from local chats."""
    parts = chat_and_file.split("/", 1)
    if len(parts) != 2:
        abort(400, "Invalid media path")
    chat_name, filename = parts
    chat_name = os.path.basename(chat_name)
    filename = os.path.basename(filename)
    chat_dir = os.path.join(str(CHATS_DIR), chat_name)
    if not os.path.isdir(chat_dir):
        abort(404)
    return send_from_directory(chat_dir, filename)


@app.route("/api/thumbnail/<path:chat_and_file>")
def api_thumbnail(chat_and_file):
    """Serve a video thumbnail (first frame)."""
    parts = chat_and_file.split("/", 1)
    if len(parts) != 2:
        abort(400, "Invalid path")
    chat_name, filename = parts
    chat_name = os.path.basename(chat_name)
    filename = os.path.basename(filename)
    chat_dir = os.path.join(str(CHATS_DIR), chat_name)
    if not os.path.isdir(chat_dir):
        abort(404)

    thumb_dir = os.path.join(chat_dir, "data", "thumbnails")
    thumb_filename = os.path.splitext(filename)[0] + ".jpg"
    thumb_path = os.path.join(thumb_dir, thumb_filename)

    if not os.path.exists(thumb_path):
        video_path = os.path.join(chat_dir, filename)
        if not os.path.exists(video_path):
            abort(404)
        from chat_search import process_manager
        success = process_manager.generate_video_thumbnail(video_path, thumb_path)
        if not success:
            abort(500, "Failed to generate thumbnail")

    return send_from_directory(thumb_dir, thumb_filename)


@app.route("/api/media/list")
def api_media_list():
    """List all media files for a chat with metadata."""
    from chat_search import vision

    chat_name = request.args.get("chat", "").strip()
    media_type_filter = request.args.get("type", "all").strip()
    page = int(request.args.get("page", 1))
    per_page = 50

    if not chat_name:
        abort(400, "Missing chat parameter")

    chat_dir = os.path.join(str(CHATS_DIR), chat_name)
    if not os.path.isdir(chat_dir):
        abort(404)

    desc_cache = vision.load_cache(os.path.join(chat_dir, "data", "descriptions.json"))

    media_files = []
    image_exts = ('.jpg', '.jpeg', '.png')
    video_exts = ('.mp4', '.mov')

    for f in sorted(os.listdir(chat_dir)):
        fl = f.lower()
        full_path = os.path.join(chat_dir, f)
        if not os.path.isfile(full_path):
            continue

        is_image = any(fl.endswith(e) for e in image_exts) and 'sticker' not in fl.upper()
        is_video = any(fl.endswith(e) for e in video_exts) and not fl.upper().startswith('GIF')

        if not is_image and not is_video:
            continue
        if media_type_filter == 'image' and not is_image:
            continue
        if media_type_filter == 'video' and not is_video:
            continue

        item = {
            "filename": f,
            "type": "image" if is_image else "video",
            "description": desc_cache.get(f, ""),
            "url": f"/media/{chat_name}/{f}",
        }
        if is_video:
            item["thumbnail_url"] = f"/api/thumbnail/{chat_name}/{f}"

        media_files.append(item)

    total = len(media_files)
    start = (page - 1) * per_page
    end = start + per_page

    return jsonify({
        "files": media_files[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


# ===========================================================================
# Analytics
# ===========================================================================

@app.route("/api/analytics")
def api_analytics():
    """Get analytics data for a local chat."""
    import sqlite3

    chat_name = request.args.get("chat", "").strip()
    if not chat_name:
        abort(400, "Missing chat parameter")

    _, db_path = _get_chat_paths(chat_name)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()

        c.execute("SELECT sender, COUNT(*) as count FROM messages GROUP BY sender ORDER BY count DESC LIMIT 20")
        senders = [{"sender": row["sender"], "count": row["count"]} for row in c.fetchall()]

        c.execute("""
            SELECT substr(datetime, 7, 4) || '-' || substr(datetime, 4, 2) as month,
                   COUNT(*) as count
            FROM messages WHERE datetime IS NOT NULL AND length(datetime) >= 10
            GROUP BY month ORDER BY month
        """)
        activity = [{"month": row["month"], "count": row["count"]} for row in c.fetchall()]

        c.execute("""
            SELECT CAST(substr(datetime, 12, 2) AS INTEGER) as hour, COUNT(*) as count
            FROM messages WHERE datetime IS NOT NULL AND length(datetime) >= 14
            GROUP BY hour ORDER BY hour
        """)
        hourly = [{"hour": row["hour"], "count": row["count"]} for row in c.fetchall()]

        c.execute("SELECT media_type, COUNT(*) as count FROM messages WHERE media_type IS NOT NULL AND media_type != '' GROUP BY media_type")
        media = [{"type": row["media_type"], "count": row["count"]} for row in c.fetchall()]

        c.execute("SELECT sender, ROUND(AVG(length(text)), 0) as avg_len FROM messages WHERE text IS NOT NULL AND text != '' GROUP BY sender ORDER BY avg_len DESC LIMIT 10")
        msg_lengths = [{"sender": row["sender"], "avg_length": row["avg_len"]} for row in c.fetchall()]

        c.execute("""
            SELECT substr(datetime, 1, 10) as date, COUNT(*) as count
            FROM messages WHERE datetime IS NOT NULL
            GROUP BY date ORDER BY count DESC LIMIT 10
        """)
        busiest_days = [{"date": row["date"], "count": row["count"]} for row in c.fetchall()]

        return jsonify({
            "senders": senders,
            "activity": activity,
            "hourly": hourly,
            "media": media,
            "msg_lengths": msg_lengths,
            "busiest_days": busiest_days,
        })
    finally:
        conn.close()


# ===========================================================================
# Export
# ===========================================================================

@app.route("/api/export")
def api_export():
    """Export search results as CSV or JSON."""
    from chat_search import indexer

    chat_name = request.args.get("chat", "").strip()
    q = request.args.get("q", "").strip()
    fmt = request.args.get("format", "csv").strip()
    sender = request.args.get("sender", "").strip()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    search_type = request.args.get("type", "all").strip()

    if not q:
        abort(400, "Missing query")

    if chat_name == '__all__' or not chat_name:
        all_results = []
        for name in sorted(os.listdir(str(CHATS_DIR))):
            chat_dir_path = os.path.join(str(CHATS_DIR), name)
            db_path = os.path.join(chat_dir_path, "data", "chat.db")
            if not os.path.isdir(chat_dir_path) or not os.path.exists(db_path):
                continue
            try:
                results, _ = indexer.search(db_path, q, sender=sender, date_from=date_from, date_to=date_to, page=1, per_page=1000, search_type=search_type)
                for r in results:
                    r["chat_name"] = name
                all_results.extend(results)
            except Exception:
                continue
        results = all_results
    else:
        _, db_path = _get_chat_paths(chat_name)
        results, _ = indexer.search(db_path, q, sender=sender, date_from=date_from, date_to=date_to, page=1, per_page=5000, search_type=search_type)

    if fmt == 'json':
        output = json.dumps(results, ensure_ascii=False, indent=2)
        return Response(output, mimetype='application/json',
                       headers={'Content-Disposition': 'attachment; filename=whatsarch_export.json'})
    else:
        import csv, io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id', 'datetime', 'sender', 'text', 'transcription', 'visual_description', 'media_type', 'chat_name'])
        for r in results:
            writer.writerow([
                r.get('id', ''), r.get('datetime', ''), r.get('sender', ''),
                r.get('text', ''), r.get('transcription', ''), r.get('visual_description', ''),
                r.get('media_type', ''), r.get('chat_name', chat_name or ''),
            ])
        csv_content = output.getvalue()
        return Response('\ufeff' + csv_content, mimetype='text/csv; charset=utf-8',
                       headers={'Content-Disposition': 'attachment; filename=whatsarch_export.csv'})


# ===========================================================================
# Settings
# ===========================================================================

@app.route("/api/settings")
def api_settings():
    """Get current settings."""
    from chat_search import config

    settings = config.load_settings(_get_project_root())
    agent_settings = _load_agent_settings()

    keys = config.get_api_keys()
    return jsonify({
        "settings": settings,
        "agent_settings": {
            "railway_url": agent_settings.get("railway_url", ""),
            "use_proxy_vision": agent_settings.get("use_proxy_vision", True),
            "use_proxy_rag": agent_settings.get("use_proxy_rag", True),
        },
        "api_keys": {
            "anthropic_configured": bool(keys["anthropic_key"]),
            "openai_configured": bool(keys["openai_key"]),
            "gemini_configured": bool(keys["gemini_key"]),
        },
    })


@app.route("/api/settings", methods=["POST"])
def api_settings_update():
    """Update settings and/or API keys."""
    from chat_search import config, ai_chat

    data = request.get_json()
    if not data:
        abort(400, "Missing JSON body")

    project_root = _get_project_root()

    # Update API keys
    key_fields = {}
    for field in ("anthropic_key", "openai_key", "gemini_key"):
        if field in data:
            key_fields[field] = data[field]
    if key_fields:
        config.save_api_keys(project_root, key_fields)

    # Update model/provider settings
    setting_fields = {}
    valid_keys = set(config.DEFAULT_SETTINGS.keys())
    for k, v in data.items():
        if k in valid_keys:
            setting_fields[k] = v
    if setting_fields:
        config.update_settings(project_root, setting_fields)

    # Update agent-specific settings
    agent_keys = {"railway_url", "auth_token", "use_proxy_vision", "use_proxy_rag"}
    agent_updates = {k: v for k, v in data.items() if k in agent_keys}
    if agent_updates:
        agent_settings = _load_agent_settings()
        agent_settings.update(agent_updates)
        _save_agent_settings(agent_settings)

    # Reset AI client
    ai_chat._llm_client = None

    return jsonify({"status": "ok"})


@app.route("/api/models")
def api_models():
    """Get available model options."""
    from chat_search import config
    return jsonify(config.PROVIDER_MODELS)


@app.route("/api/presets")
def api_presets():
    """Get preset packages with cost estimates."""
    from chat_search import config, process_manager

    chat_name = request.args.get("chat", "").strip()
    image_count = 0
    video_count = 0

    if chat_name:
        chat_dir = os.path.join(str(CHATS_DIR), chat_name)
        if os.path.isdir(chat_dir):
            try:
                scan = process_manager.scan_chat_files(chat_dir)
                image_count = len(scan.get("images", {}).get("files", []))
                video_count = len(scan.get("videos", {}).get("files", []))
            except Exception:
                pass

    hw = config.detect_hardware()
    recommended = config.recommend_preset(image_count, video_count, hw)

    presets = []
    for key, preset in config.PRESETS.items():
        costs = config.estimate_preset_cost(key, image_count, video_count)
        presets.append({
            "key": key,
            "icon": preset["icon"],
            "name_he": preset["name_he"],
            "name_en": preset["name_en"],
            "description_he": preset["description_he"],
            "description_en": preset["description_en"],
            "costs": costs,
            "recommended": key == recommended,
        })

    return jsonify({
        "presets": presets,
        "chat_name": chat_name,
        "image_count": image_count,
        "video_count": video_count,
        "recommended": recommended,
    })


@app.route("/api/aliases")
def api_aliases():
    """Get sender aliases for a chat."""
    from chat_search import config, indexer

    chat_name = request.args.get("chat", "").strip()
    if not chat_name:
        abort(400, "Missing chat parameter")

    settings = config.load_settings(_get_project_root())
    aliases = settings.get("sender_aliases", {}).get(chat_name, {})

    _, db_path = _get_chat_paths(chat_name)
    stats = indexer.get_stats(db_path)
    senders = list(stats.get("senders", {}).keys())

    return jsonify({"chat": chat_name, "senders": senders, "aliases": aliases})


@app.route("/api/aliases", methods=["POST"])
def api_aliases_update():
    """Update sender aliases for a chat."""
    from chat_search import config

    data = request.get_json()
    if not data:
        abort(400, "Missing JSON body")

    chat_name = data.get("chat", "").strip()
    aliases = data.get("aliases", {})

    if not chat_name:
        abort(400, "Missing chat name")

    settings = config.load_settings(_get_project_root())
    if "sender_aliases" not in settings:
        settings["sender_aliases"] = {}
    settings["sender_aliases"][chat_name] = aliases
    config.save_settings(_get_project_root(), settings)

    return jsonify({"status": "ok", "note": "Aliases saved. Run 'Update Search' to apply."})


# ===========================================================================
# Whisper Transcription (direct file upload)
# ===========================================================================

@app.route("/transcribe", methods=["POST"])
def transcribe():
    """Transcribe an audio file using local Whisper."""
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    file = request.files["file"]
    import tempfile
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, file.filename)
    file.save(temp_path)

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="auto", compute_type="auto")
        segments, info = model.transcribe(temp_path, beam_size=5, vad_filter=True)
        text = " ".join(seg.text.strip() for seg in segments)
        language = info.language if info and hasattr(info, "language") else ""
        return jsonify({"text": text, "language": language})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# Ollama Chat (direct)
# ===========================================================================

@app.route("/chat", methods=["POST"])
def chat_ollama():
    """Chat with local Ollama model."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing body"}), 400

    model = data.get("model", "qwen2.5:7b")
    messages = data.get("messages", [])

    try:
        import urllib.request
        req_data = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=req_data,
            headers={"Content-Type": "application/json"},
        )
        res = urllib.request.urlopen(req, timeout=300)
        result = json.loads(res.read())
        return jsonify({"response": result.get("message", {}).get("content", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    print(f"WhatsArch Local Agent v{VERSION}")
    print(f"Data: {DATA_DIR}")
    print(f"Chats: {CHATS_DIR}")
    print(f"Listening on http://localhost:11470")
    app.run(host="127.0.0.1", port=11470, debug=False)
