"""Voice message transcription using faster-whisper (local) or cloud APIs (Gemini, OpenAI)."""

import glob
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm


def load_cache(cache_path: str) -> dict:
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path: str, cache: dict):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    # Filter out error entries before persisting — they should be retried
    filtered = {
        k: v for k, v in cache.items()
        if not (isinstance(v, str) and v.startswith("[transcription error:"))
        and not (isinstance(v, dict) and isinstance(v.get("text"), str) and v["text"].startswith("[transcription error:"))
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Cloud transcription: Gemini
# ---------------------------------------------------------------------------

def _transcribe_gemini(audio_path: str, api_key: str, model: str = "gemini-2.5-flash") -> dict:
    """Transcribe a single audio file using Gemini's audio understanding."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    ext = os.path.splitext(audio_path)[1].lower()
    mime_map = {".opus": "audio/ogg", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
                ".wav": "audio/wav", ".m4a": "audio/mp4", ".flac": "audio/flac"}
    mime_type = mime_map.get(ext, "audio/ogg")

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=audio_data, mime_type=mime_type),
            "Transcribe this audio message exactly as spoken. "
            "Output ONLY the transcription text, nothing else. "
            "If the audio is in Hebrew, transcribe in Hebrew. "
            "If it's in another language, transcribe in that language. "
            "If the audio is silent or unintelligible, output: [silent]",
        ],
        config=types.GenerateContentConfig(max_output_tokens=500),
    )
    text = response.text.strip()
    # Gemini sometimes wraps in quotes
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return {"text": text, "language": ""}


# ---------------------------------------------------------------------------
# Cloud transcription: OpenAI Whisper API
# ---------------------------------------------------------------------------

def _transcribe_openai(audio_path: str, api_key: str, model: str = "whisper-1") -> dict:
    """Transcribe a single audio file using OpenAI's Whisper API."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=model,
            file=f,
        )
    return {"text": response.text.strip(), "language": ""}


# ---------------------------------------------------------------------------
# Local transcription: faster-whisper (optimized)
# ---------------------------------------------------------------------------

def _transcribe_local_batch(
    audio_files: list,
    cache: dict,
    cache_path: str,
    model_size: str = "base",
    progress_callback=None,
    cancel_event=None,
    total_audio_count: int = 0,
) -> dict:
    """Transcribe files locally using faster-whisper with optimized settings."""
    from faster_whisper import WhisperModel

    print(f"Loading Whisper model '{model_size}'...")
    model = WhisperModel(model_size, device="auto", compute_type="auto")

    save_interval = 10
    unsaved_count = 0

    bar = tqdm(audio_files, desc="Transcribing (local)", unit="file")
    for filepath in bar:
        if cancel_event and cancel_event.is_set():
            print("Transcription cancelled by user.")
            break

        filename = os.path.basename(filepath)
        bar.set_postfix_str(filename[:40])

        if filename in cache:
            continue

        try:
            segments, info = model.transcribe(
                filepath,
                beam_size=1,        # Greedy decoding — much faster than beam_size=5
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments)
            detected_lang = info.language if info and hasattr(info, "language") else ""
            cache[filename] = {"text": text, "language": detected_lang}
        except Exception as e:
            cache[filename] = {"text": f"[transcription error: {e}]", "language": ""}

        unsaved_count += 1
        if unsaved_count >= save_interval:
            save_cache(cache_path, cache)
            unsaved_count = 0

        # Log usage
        try:
            from . import usage_tracker
            audio_dur = info.duration if info and hasattr(info, "duration") else 0
            usage_tracker.log_event({
                "type": "transcription", "chat_name": os.path.basename(os.path.dirname(cache_path)),
                "provider": "whisper", "model": f"faster-whisper ({model_size})",
                "file": filename, "audio_duration_sec": audio_dur,
            }, os.path.dirname(os.path.dirname(cache_path)))
        except Exception:
            pass

        if progress_callback:
            done = sum(1 for f in audio_files if os.path.basename(f) in cache)
            progress_callback(filename, done, total_audio_count or len(audio_files))

    # Final save
    if unsaved_count > 0:
        save_cache(cache_path, cache)

    return cache


# ---------------------------------------------------------------------------
# Cloud transcription batch (parallel)
# ---------------------------------------------------------------------------

def _transcribe_cloud_batch(
    audio_files: list,
    cache: dict,
    cache_path: str,
    provider: str,
    api_key: str,
    model: str = None,
    progress_callback=None,
    cancel_event=None,
    max_workers: int = 5,
    total_audio_count: int = 0,
) -> dict:
    """Transcribe files in parallel using cloud API (Gemini or OpenAI)."""
    to_process = [f for f in audio_files if os.path.basename(f) not in cache]
    if not to_process:
        return cache

    processed_count = (total_audio_count - len(to_process)) if total_audio_count else 0
    lock = threading.Lock()
    error_count = 0
    first_error = ""
    save_interval = 5
    unsaved_count = 0

    def process_one(filepath):
        if cancel_event and cancel_event.is_set():
            return None, None
        try:
            if provider == "gemini":
                return os.path.basename(filepath), _transcribe_gemini(filepath, api_key, model or "gemini-2.5-flash")
            elif provider == "openai":
                return os.path.basename(filepath), _transcribe_openai(filepath, api_key, model or "whisper-1")
            else:
                return os.path.basename(filepath), {"text": f"[unsupported provider: {provider}]", "language": ""}
        except Exception as e:
            return os.path.basename(filepath), {"text": f"[transcription error: {e}]", "language": ""}

    provider_name = "Gemini" if provider == "gemini" else "OpenAI"
    bar = tqdm(total=len(to_process), desc=f"Transcribing ({provider_name})", unit="file")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_one, f): f for f in to_process}

        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                print("  Transcription cancelled by user.")
                break

            filename, result = future.result()
            if filename is None:
                continue

            with lock:
                # Don't cache errors — leave for retry
                if result and result.get("text") and not result["text"].startswith("[transcription error:"):
                    cache[filename] = result
                else:
                    error_count += 1
                    if error_count == 1:
                        first_error = result.get("text", "") if result else ""

                unsaved_count += 1
                if unsaved_count >= save_interval:
                    save_cache(cache_path, cache)
                    unsaved_count = 0

                processed_count += 1
                bar.update(1)
                try:
                    bar.set_postfix_str(filename[:35])
                except UnicodeEncodeError:
                    bar.set_postfix_str("...")

            # Log usage
            if result and result.get("text") and not result["text"].startswith("["):
                try:
                    from . import usage_tracker
                    usage_tracker.log_event({
                        "type": "transcription",
                        "chat_name": os.path.basename(os.path.dirname(os.path.dirname(cache_path))),
                        "provider": provider, "model": model or "default",
                        "file": filename,
                    }, os.path.dirname(os.path.dirname(cache_path)))
                except Exception:
                    pass

            if progress_callback:
                progress_callback(filename, processed_count, total_audio_count or len(audio_files))

    bar.close()

    # Final save
    if unsaved_count > 0:
        save_cache(cache_path, cache)

    if error_count > 0:
        print(f"  {error_count} transcription errors. First: {first_error}")
    if error_count > 0 and processed_count - (total_audio_count - len(to_process)) == 0:
        raise RuntimeError(f"All {error_count} transcriptions failed. First error: {first_error}")

    return cache


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def transcribe_audio_files(
    chat_dir: str,
    cache_path: str,
    model_size: str = "base",
    provider: str = "local",
    api_key: str = "",
    cloud_model: str = None,
    progress_callback=None,
    cancel_event=None,
) -> dict:
    """Transcribe all AUDIO .opus files in chat_dir.

    Supports local (faster-whisper) and cloud (Gemini, OpenAI) transcription.
    Returns dict mapping filename -> {text, language}.
    Caches results in cache_path (JSON) so interrupted runs can resume.
    """
    audio_files = sorted(glob.glob(os.path.join(chat_dir, "*AUDIO*.opus")))
    if not audio_files:
        print("No audio files found.")
        return {}

    cache = load_cache(cache_path)

    # Migrate old format entries (plain strings -> {text, language})
    migrated = False
    for key, val in cache.items():
        if isinstance(val, str):
            cache[key] = {"text": val, "language": "he"}
            migrated = True
    if migrated:
        save_cache(cache_path, cache)

    already = sum(1 for f in audio_files if os.path.basename(f) in cache)
    remaining = len(audio_files) - already

    if remaining == 0:
        print(f"All {len(audio_files)} audio files already transcribed.")
        return cache

    print(f"Found {len(audio_files)} audio files ({already} cached, {remaining} to transcribe)")

    to_process = [f for f in audio_files if os.path.basename(f) not in cache]

    if provider in ("gemini", "openai"):
        print(f"Using cloud transcription: {provider}")
        cache = _transcribe_cloud_batch(
            to_process, cache, cache_path,
            provider=provider, api_key=api_key, model=cloud_model,
            progress_callback=progress_callback, cancel_event=cancel_event,
            max_workers=5, total_audio_count=len(audio_files),
        )
    else:
        # Local faster-whisper
        print(f"Using local transcription: faster-whisper ({model_size}), beam_size=1")
        cache = _transcribe_local_batch(
            audio_files, cache, cache_path,
            model_size=model_size,
            progress_callback=progress_callback, cancel_event=cancel_event,
            total_audio_count=len(audio_files),
        )

    print(f"Transcription complete. {len(cache)} files total.")
    return cache
