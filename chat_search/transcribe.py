"""Voice message transcription using faster-whisper."""

import json
import glob
import os

from tqdm import tqdm


def load_cache(cache_path: str) -> dict:
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path: str, cache: dict):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def transcribe_audio_files(
    chat_dir: str,
    cache_path: str,
    model_size: str = "small",
    progress_callback=None,
    cancel_event=None,
) -> dict:
    """Transcribe all AUDIO .opus files in chat_dir.

    Returns dict mapping filename -> {text, language}.
    Caches results in cache_path (JSON) so interrupted runs can resume.
    Migrates old format (plain string) entries on read.
    """
    from faster_whisper import WhisperModel

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
    print(f"Loading Whisper model '{model_size}'...")

    model = WhisperModel(model_size, device="auto", compute_type="auto")

    bar = tqdm(audio_files, desc="Transcribing", unit="file")
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
                beam_size=5,
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments)
            detected_lang = info.language if info and hasattr(info, "language") else ""
            cache[filename] = {"text": text, "language": detected_lang}
        except Exception as e:
            cache[filename] = {"text": f"[transcription error: {e}]", "language": ""}

        # Save after each file so we can resume
        save_cache(cache_path, cache)

        # Log usage
        try:
            from . import usage_tracker
            audio_dur = info.duration if info and hasattr(info, "duration") else 0
            usage_tracker.log_event({
                "type": "transcription", "chat_name": os.path.basename(chat_dir),
                "provider": "whisper", "model": f"faster-whisper ({model_size})",
                "file": filename, "audio_duration_sec": audio_dur,
            }, os.path.dirname(os.path.dirname(cache_path)))
        except Exception:
            pass

        if progress_callback:
            done = sum(1 for f in audio_files if os.path.basename(f) in cache)
            progress_callback(filename, done, len(audio_files))

    print(f"Transcription complete. {len(cache)} files total.")
    return cache
