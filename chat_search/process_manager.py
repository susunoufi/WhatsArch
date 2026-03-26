"""Background processing manager for WhatsArch.

Handles file scanning, processing status tracking, background task execution,
and video thumbnail generation.
"""

import glob
import json
import os
import sqlite3
import subprocess
import shutil
import threading
import time

# ---------------------------------------------------------------------------
# In-memory processing state (thread-safe)
# ---------------------------------------------------------------------------

_processing_state = {}  # chat_name -> {task, status, current_file, processed, total, error}
_processing_lock = threading.Lock()
_cancel_events = {}  # chat_name -> threading.Event (set = cancel requested)


def _get_api_key_for_provider(provider: str) -> str:
    """Get the appropriate API key for a given provider."""
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    env_var = key_map.get(provider)
    if env_var:
        key = os.environ.get(env_var, "")
        if not key and provider != "ollama":
            raise RuntimeError(f"{env_var} not set — required for {provider}")
        return key
    return ""  # Ollama doesn't need a key


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def scan_chat_files(chat_dir: str) -> dict:
    """Scan a chat directory and return counts/lists of all media file types.

    Uses the same filtering rules as vision.py and transcribe.py.
    """
    audio_files = []
    image_files = []
    video_files = []
    pdf_files = []

    # Audio: *AUDIO*.opus pattern
    audio_files = sorted(glob.glob(os.path.join(chat_dir, "*AUDIO*.opus")))
    audio_files = [os.path.basename(f) for f in audio_files]

    # Scan directory for images, videos, PDFs
    image_exts = ('.jpg', '.jpeg', '.png')
    video_exts = ('.mp4', '.mov')

    for f in os.listdir(chat_dir):
        fl = f.lower()
        full_path = os.path.join(chat_dir, f)
        if not os.path.isfile(full_path):
            continue

        # Images (skip stickers)
        if any(fl.endswith(e) for e in image_exts) and 'sticker' not in fl.upper():
            image_files.append(f)
        # Videos (skip GIF-converted)
        elif any(fl.endswith(e) for e in video_exts) and not fl.upper().startswith('GIF'):
            video_files.append(f)
        # PDFs
        elif fl.endswith('.pdf'):
            pdf_files.append(f)

    return {
        "audio": {"total": len(audio_files), "files": sorted(audio_files)},
        "images": {"total": len(image_files), "files": sorted(image_files)},
        "videos": {"total": len(video_files), "files": sorted(video_files)},
        "pdfs": {"total": len(pdf_files), "files": sorted(pdf_files)},
    }


def _load_json_cache(path: str) -> dict:
    """Load a JSON cache file, returning empty dict if missing."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def get_processing_status(chat_dir: str) -> dict:
    """Get detailed processing status for a chat directory.

    Compares files on disk against JSON cache files to compute progress.
    Returns per-file detail for each processing step.
    """
    data_dir = os.path.join(chat_dir, "data")
    scan = scan_chat_files(chat_dir)

    # Load all caches
    transcriptions = _load_json_cache(os.path.join(data_dir, "transcriptions.json"))
    descriptions = _load_json_cache(os.path.join(data_dir, "descriptions.json"))
    video_trans = _load_json_cache(os.path.join(data_dir, "video_transcriptions.json"))
    pdf_texts = _load_json_cache(os.path.join(data_dir, "pdf_texts.json"))

    # Audio transcription status
    audio_files_status = []
    audio_processed = 0
    for fname in scan["audio"]["files"]:
        done = fname in transcriptions
        if done:
            audio_processed += 1
        audio_files_status.append({"name": fname, "done": done})

    # Image description status
    image_files_status = []
    images_processed = 0
    for fname in scan["images"]["files"]:
        done = fname in descriptions
        if done:
            images_processed += 1
        image_files_status.append({"name": fname, "done": done})

    # Video status (both visual description and audio transcription)
    video_files_status = []
    videos_described = 0
    videos_transcribed = 0
    for fname in scan["videos"]["files"]:
        desc_done = fname in descriptions
        trans_done = fname in video_trans
        if desc_done:
            videos_described += 1
        if trans_done:
            videos_transcribed += 1
        video_files_status.append({
            "name": fname,
            "described": desc_done,
            "transcribed": trans_done,
            "done": desc_done,  # primary indicator
        })

    # PDF extraction status
    pdf_files_status = []
    pdfs_processed = 0
    for fname in scan["pdfs"]["files"]:
        done = fname in pdf_texts
        if done:
            pdfs_processed += 1
        pdf_files_status.append({"name": fname, "done": done})

    # Index status
    db_path = os.path.join(data_dir, "chat.db")
    index_exists = os.path.exists(db_path)
    message_count = 0
    if index_exists:
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM messages")
            message_count = c.fetchone()[0]
            conn.close()
        except Exception:
            pass

    # Embeddings status (check for chunk embeddings, including partial progress)
    chunk_embeddings_path = os.path.join(data_dir, "chat_chunk_embeddings.npy")
    legacy_embeddings_path = os.path.join(data_dir, "chat_embeddings.npy")
    embeddings_exist = os.path.exists(chunk_embeddings_path) or os.path.exists(legacy_embeddings_path)
    embeddings_done = 0
    embeddings_total = 0
    if os.path.exists(chunk_embeddings_path):
        try:
            import numpy as np
            emb = np.load(chunk_embeddings_path)
            embeddings_done = emb.shape[0]
        except Exception:
            pass
    # Get total chunk count from DB
    if index_exists:
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM chunks")
            embeddings_total = c.fetchone()[0]
            conn.close()
        except Exception:
            pass

    chat_name = os.path.basename(chat_dir)

    return {
        "audio": {
            "total": scan["audio"]["total"],
            "processed": audio_processed,
            "files": audio_files_status,
        },
        "images": {
            "total": scan["images"]["total"],
            "processed": images_processed,
            "files": image_files_status,
        },
        "videos": {
            "total": scan["videos"]["total"],
            "described": videos_described,
            "transcribed": videos_transcribed,
            "files": video_files_status,
        },
        "pdfs": {
            "total": scan["pdfs"]["total"],
            "processed": pdfs_processed,
            "files": pdf_files_status,
        },
        "index": {
            "exists": index_exists,
            "message_count": message_count,
        },
        "embeddings": {
            "exists": embeddings_exist,
            "done": embeddings_done,
            "total": embeddings_total,
        },
        "active_task": get_active_task(chat_name),
    }


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def get_active_task(chat_name: str) -> dict | None:
    """Return the current processing state for a chat, or None if idle."""
    with _processing_lock:
        state = _processing_state.get(chat_name)
        if state and state.get("status") == "running":
            return dict(state)  # return a copy
    return None


def get_task_status(chat_name: str) -> dict | None:
    """Return the current processing state including cancelled/error.

    Used by the progress polling endpoint. Returns None only if no state exists.
    Consumes 'cancelled' status (returns it once, then clears to idle).
    """
    with _processing_lock:
        state = _processing_state.get(chat_name)
        if not state:
            return None
        result = dict(state)
        # Consume cancelled status so it doesn't persist
        if state.get("status") == "cancelled":
            state["status"] = "idle"
        return result


def start_processing(chat_name: str, task: str, chats_dir: str, model_size: str = "small") -> bool:
    """Launch a background thread for a processing task.

    Returns False if a task is already running for this chat.
    """
    with _processing_lock:
        current = _processing_state.get(chat_name)
        if current and current.get("status") == "running":
            return False

        cancel_event = threading.Event()
        _cancel_events[chat_name] = cancel_event

        _processing_state[chat_name] = {
            "task": task,
            "status": "running",
            "current_file": "",
            "processed": 0,
            "total": 0,
            "error": None,
            "start_time": time.time(),
            "eta_seconds": None,
        }

    thread = threading.Thread(
        target=_run_task,
        args=(chat_name, task, chats_dir, model_size),
        daemon=True,
    )
    thread.start()
    return True


def stop_processing(chat_name: str) -> bool:
    """Request cancellation of the running task for a chat.

    Returns True if a running task was found and cancel was signalled.
    """
    with _processing_lock:
        state = _processing_state.get(chat_name)
        if not state or state.get("status") != "running":
            return False
        cancel_event = _cancel_events.get(chat_name)
        if cancel_event:
            cancel_event.set()
        return True


def _make_progress_callback(chat_name: str):
    """Create a progress callback that updates _processing_state."""
    def callback(filename, processed, total):
        with _processing_lock:
            state = _processing_state.get(chat_name)
            if state:
                state["current_file"] = filename
                state["processed"] = processed
                state["total"] = total
                elapsed = time.time() - state.get("start_time", time.time())
                if processed > 0 and total > 0:
                    rate = elapsed / processed  # seconds per item
                    remaining = (total - processed) * rate
                    state["eta_seconds"] = round(remaining)
                else:
                    state["eta_seconds"] = None
    return callback


class ProcessCancelled(Exception):
    """Raised when a processing task is cancelled by the user."""
    pass


def _run_task(chat_name: str, task: str, chats_dir: str, model_size: str):
    """Execute a processing task in a background thread."""
    chat_dir = os.path.join(chats_dir, chat_name)
    data_dir = os.path.join(chat_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    callback = _make_progress_callback(chat_name)
    cancel_event = _cancel_events.get(chat_name)

    try:
        if task == "transcribe":
            from .transcribe import transcribe_audio_files
            cache_path = os.path.join(data_dir, "transcriptions.json")
            transcribe_audio_files(chat_dir, cache_path, model_size=model_size, progress_callback=callback, cancel_event=cancel_event)

        elif task == "images":
            from .vision import process_images
            from . import config
            project_root = os.path.dirname(chats_dir)
            settings = config.load_settings(project_root)
            provider = settings.get("vision_provider", "anthropic")
            model = settings.get("vision_model")
            api_key = _get_api_key_for_provider(provider)
            ollama_url = settings.get("ollama_base_url")
            cache_path = os.path.join(data_dir, "descriptions.json")
            process_images(chat_dir, cache_path, provider=provider, model=model, api_key=api_key, ollama_url=ollama_url, progress_callback=callback, cancel_event=cancel_event)

        elif task == "videos":
            from .vision import process_videos
            from . import config
            project_root = os.path.dirname(chats_dir)
            settings = config.load_settings(project_root)
            provider = settings.get("video_provider", "anthropic")
            model = settings.get("video_model")
            api_key = _get_api_key_for_provider(provider)
            ollama_url = settings.get("ollama_base_url")
            desc_path = os.path.join(data_dir, "descriptions.json")
            vtrans_path = os.path.join(data_dir, "video_transcriptions.json")
            process_videos(chat_dir, desc_path, vtrans_path, provider=provider, model=model, api_key=api_key, ollama_url=ollama_url, model_size=model_size, progress_callback=callback, cancel_event=cancel_event)

        elif task == "pdfs":
            from .vision import process_pdfs
            cache_path = os.path.join(data_dir, "pdf_texts.json")
            process_pdfs(chat_dir, cache_path, progress_callback=callback, cancel_event=cancel_event)

        elif task == "index":
            _run_index_task(chat_name, chat_dir, data_dir, callback, cancel_event)

        elif task == "embeddings":
            _run_embeddings_task(chat_name, chat_dir, data_dir, callback, cancel_event)

        # Check if we were cancelled (for tasks that break from loop instead of raising)
        if cancel_event and cancel_event.is_set():
            raise ProcessCancelled()

        with _processing_lock:
            _processing_state[chat_name] = {
                "task": task, "status": "idle",
                "current_file": "", "processed": 0, "total": 0, "error": None,
            }

    except ProcessCancelled:
        with _processing_lock:
            _processing_state[chat_name] = {
                "task": task, "status": "cancelled",
                "current_file": "", "processed": 0, "total": 0, "error": None,
            }

    except Exception as e:
        # Treat EmbeddingCancelled as cancellation too
        from .indexer import EmbeddingCancelled
        if isinstance(e, EmbeddingCancelled):
            with _processing_lock:
                _processing_state[chat_name] = {
                    "task": task, "status": "cancelled",
                    "current_file": "", "processed": 0, "total": 0, "error": None,
                }
            return
        with _processing_lock:
            state = _processing_state.get(chat_name, {})
            state["status"] = "error"
            state["error"] = str(e)
            _processing_state[chat_name] = state

    finally:
        with _processing_lock:
            _cancel_events.pop(chat_name, None)


def _check_cancel(cancel_event):
    """Raise ProcessCancelled if cancellation has been requested."""
    if cancel_event and cancel_event.is_set():
        raise ProcessCancelled()


def _run_index_task(chat_name, chat_dir, data_dir, callback, cancel_event=None):
    """Run the parsing + indexing + chunking pipeline."""
    from .vision import load_cache as load_vision_cache
    from .transcribe import load_cache
    from .parser import parse_chat, detect_chat_type
    from .indexer import build_index, build_chunks, save_chat_metadata
    from .chunker import segment_into_chunks

    chat_file = os.path.join(chat_dir, "_chat.txt")
    db_path = os.path.join(data_dir, "chat.db")

    _check_cancel(cancel_event)
    callback("Loading caches...", 0, 5)
    transcriptions = load_cache(os.path.join(data_dir, "transcriptions.json"))
    descriptions = load_vision_cache(os.path.join(data_dir, "descriptions.json"))
    video_trans = load_vision_cache(os.path.join(data_dir, "video_transcriptions.json"))
    pdf_texts = load_vision_cache(os.path.join(data_dir, "pdf_texts.json"))

    _check_cancel(cancel_event)
    callback("Parsing messages...", 1, 5)
    # Load sender aliases from settings
    from . import config
    project_root = os.path.dirname(os.path.dirname(data_dir))
    settings = config.load_settings(project_root)
    chat_name_for_aliases = os.path.basename(os.path.dirname(data_dir))
    sender_aliases = settings.get("sender_aliases", {}).get(chat_name_for_aliases, {})
    messages = parse_chat(chat_file, transcriptions, descriptions, video_trans, pdf_texts, sender_aliases=sender_aliases)

    _check_cancel(cancel_event)
    callback("Building index...", 2, 5)
    build_index(messages, db_path)

    _check_cancel(cancel_event)
    callback("Detecting chat type...", 3, 5)
    chat_info = detect_chat_type(chat_file, messages)
    chat_type = chat_info["chat_type"]
    save_chat_metadata(db_path, {
        "chat_type": chat_type,
        "unique_senders": chat_info["unique_senders"],
        "total_messages": len(messages),
    })

    _check_cancel(cancel_event)
    callback("Building chunks...", 4, 5)
    chunks = segment_into_chunks(messages, chat_type=chat_type)
    build_chunks(chunks, db_path)

    callback("Done", 5, 5)


def _run_embeddings_task(chat_name, chat_dir, data_dir, callback, cancel_event=None):
    """Run the chunk embeddings generation pipeline.

    If chunks already exist in the DB, reuses them to allow resume of partial
    embeddings without rebuilding. Only rebuilds chunks if they don't exist.
    """
    from .indexer import build_chunks, build_chunk_embeddings, save_chat_metadata, load_chunks_from_db

    db_path = os.path.join(data_dir, "chat.db")

    _check_cancel(cancel_event)
    callback("Checking existing chunks...", 0, 3)

    # Try to load existing chunks from DB (avoids rebuild, preserves partial embeddings)
    chunks = load_chunks_from_db(db_path)

    if chunks:
        callback("Using existing chunks...", 1, 3)
        print(f"  Loaded {len(chunks)} existing chunks from DB, skipping rebuild.")
    else:
        # No chunks yet — need to build them from scratch
        from .vision import load_cache as load_vision_cache
        from .transcribe import load_cache
        from .parser import parse_chat, detect_chat_type
        from .chunker import segment_into_chunks

        chat_file = os.path.join(chat_dir, "_chat.txt")

        _check_cancel(cancel_event)
        callback("Loading caches...", 0, 5)
        transcriptions = load_cache(os.path.join(data_dir, "transcriptions.json"))
        descriptions = load_vision_cache(os.path.join(data_dir, "descriptions.json"))
        video_trans = load_vision_cache(os.path.join(data_dir, "video_transcriptions.json"))
        pdf_texts = load_vision_cache(os.path.join(data_dir, "pdf_texts.json"))

        _check_cancel(cancel_event)
        callback("Parsing messages...", 1, 5)
        messages = parse_chat(chat_file, transcriptions, descriptions, video_trans, pdf_texts)

        _check_cancel(cancel_event)
        callback("Detecting chat type...", 2, 5)
        chat_info = detect_chat_type(chat_file, messages)
        chat_type = chat_info["chat_type"]
        save_chat_metadata(db_path, {
            "chat_type": chat_type,
            "unique_senders": chat_info["unique_senders"],
            "total_messages": len(messages),
        })

        _check_cancel(cancel_event)
        callback("Building chunks...", 3, 5)
        chunks = segment_into_chunks(messages, chat_type=chat_type)
        build_chunks(chunks, db_path)

    _check_cancel(cancel_event)
    callback("Building chunk embeddings...", 0, len(chunks))
    build_chunk_embeddings(chunks, db_path, cancel_event=cancel_event, progress_callback=callback)

    callback("Done", len(chunks), len(chunks))


# ---------------------------------------------------------------------------
# Video thumbnail generation
# ---------------------------------------------------------------------------

def generate_video_thumbnail(video_path: str, output_path: str) -> bool:
    """Extract the first frame of a video as a JPEG thumbnail using ffmpeg.

    Returns True on success, False on failure.
    """
    if not shutil.which("ffmpeg"):
        return False

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vframes", "1", "-q:v", "2", output_path],
            capture_output=True, timeout=30,
        )
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False
