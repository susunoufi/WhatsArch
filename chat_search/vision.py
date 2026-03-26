"""Image, video, and PDF understanding using vision AI providers and ffmpeg."""

import base64
import glob
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from . import config


# ---------------------------------------------------------------------------
# Cache helpers (same pattern as transcribe.py)
# ---------------------------------------------------------------------------

def load_cache(cache_path: str) -> dict:
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path: str, cache: dict):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 60.0  # default fallback


def extract_audio_from_video(video_path: str, output_dir: str) -> str | None:
    """Extract audio track from video as WAV for Whisper."""
    output_path = os.path.join(output_dir, "audio.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn",
             "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", output_path],
            capture_output=True, timeout=120,
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
    except Exception:
        pass
    return None


def extract_key_frames(video_path: str, output_dir: str) -> list[str]:
    """Extract key frames from video using ffmpeg.

    Adaptive frame rate based on duration, capped at 10 frames.
    """
    duration = get_video_duration(video_path)

    if duration <= 30:
        # Short video: 3 frames (start, middle, end)
        interval = max(duration / 4, 1)
    elif duration <= 120:
        interval = 15
    elif duration <= 600:
        interval = 30
    else:
        interval = 60

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vf", f"fps=1/{interval}", "-q:v", "2",
             "-frames:v", "10",
             os.path.join(output_dir, "frame_%04d.jpg")],
            capture_output=True, timeout=120,
        )
    except Exception:
        return []

    frames = sorted(glob.glob(os.path.join(output_dir, "frame_*.jpg")))
    return frames[:10]


# ---------------------------------------------------------------------------
# Vision AI providers
# ---------------------------------------------------------------------------

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

IMAGE_PROMPTS = {
    "he": "תאר את התמונה בעברית בקצרה (2-3 משפטים). אם יש טקסט גלוי בתמונה, ציין אותו במדויק.",
    "ar": "صف الصورة بالعربية باختصار (2-3 جمل). إذا كان هناك نص مرئي في الصورة، اذكره بدقة.",
    "en": "Describe the image briefly (2-3 sentences). If there is visible text in the image, quote it exactly.",
    "es": "Describe la imagen brevemente (2-3 oraciones). Si hay texto visible en la imagen, cítalo exactamente.",
    "fr": "Décrivez l'image brièvement (2-3 phrases). S'il y a du texte visible dans l'image, citez-le exactement.",
    "de": "Beschreiben Sie das Bild kurz (2-3 Sätze). Wenn sichtbarer Text im Bild ist, zitieren Sie ihn genau.",
    "ru": "Опишите изображение кратко (2-3 предложения). Если на изображении есть видимый текст, укажите его точно.",
    "pt": "Descreva a imagem brevemente (2-3 frases). Se houver texto visível na imagem, cite-o exatamente.",
    "zh": "简要描述图片（2-3句话）。如果图片中有可见文字，请准确引用。",
}

IMAGE_PROMPT = IMAGE_PROMPTS["he"]  # Default for backward compatibility


def get_image_prompt(language: str = "he") -> str:
    """Get the image description prompt for the given language."""
    return IMAGE_PROMPTS.get(language, IMAGE_PROMPTS["en"])

VIDEO_PROMPT = (
    "אלה פריימים מסרטון וידאו. תאר בעברית בקצרה (3-5 משפטים) "
    "מה קורה בסרטון. אם יש טקסט גלוי, ציין אותו."
)


def _image_to_base64_block(image_path: str) -> dict:
    """Read an image file and return a Claude Vision content block."""
    ext = os.path.splitext(image_path)[1].lower()
    media_type = MEDIA_TYPES.get(ext, "image/jpeg")

    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _read_image_as_data_url(image_path: str) -> str:
    """Read an image file and return a data URL string."""
    ext = os.path.splitext(image_path)[1].lower()
    media_type = MEDIA_TYPES.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return f"data:{media_type};base64,{data}"


# ---------------------------------------------------------------------------
# Image description: dispatcher + per-provider implementations
# ---------------------------------------------------------------------------

def describe_image(image_path: str, provider: str = "anthropic", model: str = None, api_key: str = None, ollama_url: str = None, proxy_url: str = None, proxy_token: str = None) -> str:
    """Send a single image to AI and get a Hebrew description + OCR."""
    if provider == "proxy" and proxy_url:
        return _describe_image_proxy(image_path, proxy_url, proxy_token)
    elif provider == "anthropic":
        return _describe_image_anthropic(image_path, api_key, model or "claude-sonnet-4-20250514")
    elif provider == "openai":
        return _describe_image_openai(image_path, api_key, model or "gpt-4o-mini")
    elif provider == "gemini":
        return _describe_image_gemini(image_path, api_key, model or "gemini-2.5-flash")
    elif provider == "ollama":
        return _describe_image_ollama(image_path, ollama_url or "http://localhost:11434", model or "llama3.2-vision")
    else:
        return f"[unsupported provider: {provider}]"


def _describe_image_proxy(image_path: str, proxy_url: str, proxy_token: str = None) -> str:
    """Send image to Railway proxy for description using admin's API keys."""
    import urllib.request
    try:
        ext = os.path.splitext(image_path)[1].lower()
        media_type = MEDIA_TYPES.get(ext, "image/jpeg")
        with open(image_path, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

        payload = json.dumps({
            "image_base64": image_b64,
            "media_type": media_type,
        }).encode()

        headers = {"Content-Type": "application/json"}
        if proxy_token:
            headers["Authorization"] = f"Bearer {proxy_token}"

        req = urllib.request.Request(
            f"{proxy_url.rstrip('/')}/api/proxy/vision",
            data=payload,
            headers=headers,
        )
        res = urllib.request.urlopen(req, timeout=60)
        result = json.loads(res.read())
        return result.get("description", "[proxy: no description returned]")
    except Exception as e:
        return f"[proxy error: {str(e)}]"


def describe_image_from_base64(image_b64: str, media_type: str, provider: str = "gemini",
                                model: str = None, api_key: str = None,
                                ollama_url: str = None, language: str = "he") -> str:
    """Describe an image from base64 data (used by proxy endpoint, no file I/O)."""
    prompt = get_image_prompt(language)

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model or "claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return response.content[0].text

    elif provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        data_url = f"data:{media_type};base64,{image_b64}"
        response = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return response.choices[0].message.content

    elif provider == "gemini":
        from google import genai
        client = genai.Client(api_key=api_key)
        image_bytes = base64.standard_b64decode(image_b64)
        response = client.models.generate_content(
            model=model or "gemini-2.5-flash",
            contents=[
                genai.types.Part.from_bytes(data=image_bytes, mime_type=media_type),
                prompt,
            ],
        )
        return response.text

    elif provider == "ollama":
        from openai import OpenAI
        client = OpenAI(base_url=(ollama_url or "http://localhost:11434").rstrip("/") + "/v1", api_key="ollama")
        data_url = f"data:{media_type};base64,{image_b64}"
        response = client.chat.completions.create(
            model=model or "llama3.2-vision",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return response.choices[0].message.content

    else:
        return f"[unsupported provider: {provider}]"


def _describe_image_anthropic(image_path: str, api_key: str, model: str) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        block = _image_to_base64_block(image_path)
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    block,
                    {"type": "text", "text": IMAGE_PROMPT},
                ],
            }],
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"[vision error: {e}]"


def _describe_image_openai(image_path: str, api_key: str, model: str) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        data_url = _read_image_as_data_url(image_path)

        response = client.chat.completions.create(
            model=model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": IMAGE_PROMPT},
                ],
            }],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[vision error: {e}]"


def _describe_image_gemini(image_path: str, api_key: str, model: str) -> str:
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)

        with open(image_path, "rb") as f:
            image_data = f.read()

        ext = os.path.splitext(image_path)[1].lower()
        media_type = MEDIA_TYPES.get(ext, "image/jpeg")

        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=image_data, mime_type=media_type),
                IMAGE_PROMPT,
            ],
            config=types.GenerateContentConfig(max_output_tokens=300),
        )
        return response.text.strip()
    except Exception as e:
        return f"[vision error: {e}]"


def _describe_image_ollama(image_path: str, ollama_url: str, model: str) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(base_url=ollama_url.rstrip("/") + "/v1", api_key="ollama")

        data_url = _read_image_as_data_url(image_path)

        response = client.chat.completions.create(
            model=model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": IMAGE_PROMPT},
                ],
            }],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[vision error: {e}]"


# ---------------------------------------------------------------------------
# Video frame description: dispatcher + per-provider implementations
# ---------------------------------------------------------------------------

def describe_video_frames(frame_paths: list[str], provider: str = "anthropic", model: str = None, api_key: str = None, ollama_url: str = None) -> str:
    """Send multiple video frames to AI in a single call and get a Hebrew description."""
    if not frame_paths:
        return ""
    if provider == "anthropic":
        return _describe_video_anthropic(frame_paths, api_key, model or "claude-sonnet-4-20250514")
    elif provider == "openai":
        return _describe_video_openai(frame_paths, api_key, model or "gpt-4o-mini")
    elif provider == "gemini":
        return _describe_video_gemini(frame_paths, api_key, model or "gemini-2.5-flash")
    elif provider == "ollama":
        return _describe_video_ollama(frame_paths, ollama_url or "http://localhost:11434", model or "llama3.2-vision")
    else:
        return f"[unsupported provider: {provider}]"


def _describe_video_anthropic(frame_paths: list[str], api_key: str, model: str) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        content = []
        for fp in frame_paths:
            content.append(_image_to_base64_block(fp))
        content.append({"type": "text", "text": VIDEO_PROMPT})

        response = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"[vision error: {e}]"


def _describe_video_openai(frame_paths: list[str], api_key: str, model: str) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        content = []
        for fp in frame_paths:
            data_url = _read_image_as_data_url(fp)
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        content.append({"type": "text", "text": VIDEO_PROMPT})

        response = client.chat.completions.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": content}],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[vision error: {e}]"


def _describe_video_gemini(frame_paths: list[str], api_key: str, model: str) -> str:
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)

        contents = []
        for fp in frame_paths:
            with open(fp, "rb") as f:
                image_data = f.read()
            ext = os.path.splitext(fp)[1].lower()
            media_type = MEDIA_TYPES.get(ext, "image/jpeg")
            contents.append(types.Part.from_bytes(data=image_data, mime_type=media_type))
        contents.append(VIDEO_PROMPT)

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(max_output_tokens=500),
        )
        return response.text.strip()
    except Exception as e:
        return f"[vision error: {e}]"


def _describe_video_ollama(frame_paths: list[str], ollama_url: str, model: str) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(base_url=ollama_url.rstrip("/") + "/v1", api_key="ollama")

        content = []
        for fp in frame_paths:
            data_url = _read_image_as_data_url(fp)
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        content.append({"type": "text", "text": VIDEO_PROMPT})

        response = client.chat.completions.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": content}],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[vision error: {e}]"


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from a PDF file using pymupdf."""
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        pages_text = []
        for page_num in range(min(len(doc), 20)):  # Cap at 20 pages
            page = doc[page_num]
            text = page.get_text().strip()
            if text:
                pages_text.append(text)
        doc.close()
        full_text = "\n".join(pages_text)
        # Truncate very long PDFs
        if len(full_text) > 5000:
            full_text = full_text[:5000] + "..."
        return full_text
    except Exception as e:
        return f"[pdf error: {e}]"


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

def _should_skip_image(filename: str) -> bool:
    """Skip sticker files (small emoji-like images)."""
    return "STICKER" in filename.upper()


def _should_skip_video(filename: str) -> bool:
    """Skip GIF-converted videos (usually trivial)."""
    return filename.upper().startswith("GIF")


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def process_images(chat_dir: str, cache_path: str, provider: str = "anthropic", model: str = None, api_key: str = None, ollama_url: str = None, progress_callback=None, cancel_event=None, max_workers: int = 3, proxy_url: str = None, proxy_token: str = None) -> dict:
    """Batch-process all image files with parallel API calls. Returns cache dict {filename: description}."""
    extensions = ("*.jpg", "*.jpeg", "*.png")
    image_files = []
    for ext in extensions:
        image_files.extend(glob.glob(os.path.join(chat_dir, ext)))
    image_files = sorted(image_files)

    # Filter out stickers
    image_files = [f for f in image_files if not _should_skip_image(os.path.basename(f))]

    if not image_files:
        print("  No image files found.")
        return load_cache(cache_path)

    cache = load_cache(cache_path)

    # Collect files that need processing
    to_process = [f for f in image_files if os.path.basename(f) not in cache]

    if not to_process:
        print(f"  All {len(image_files)} images already described.")
        return cache

    print(f"  Found {len(image_files)} images ({len(image_files) - len(to_process)} cached, {len(to_process)} to describe)")

    # Determine max workers based on provider
    # Ollama: 1 (local, can't parallelize well)
    # API providers: max_workers (default 3)
    workers = 1 if provider == "ollama" else max_workers

    processed_count = len(image_files) - len(to_process)
    error_count = 0
    first_error = ""
    lock = threading.Lock()

    def process_one(filepath):
        if cancel_event and cancel_event.is_set():
            return None, None
        filename = os.path.basename(filepath)
        desc = describe_image(filepath, provider=provider, model=model, api_key=api_key, ollama_url=ollama_url, proxy_url=proxy_url, proxy_token=proxy_token)
        return filename, desc

    bar = tqdm(total=len(to_process), desc="  Describing images", unit="file", initial=0)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_one, f): f for f in to_process}

        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                print("  Image processing cancelled by user.")
                break

            filename, desc = future.result()
            if filename is None:
                continue

            with lock:
                # Don't cache error responses — leave them for retry
                if desc and not desc.startswith("[vision error:") and not desc.startswith("[proxy error:"):
                    cache[filename] = desc
                    save_cache(cache_path, cache)
                    # Log usage
                    try:
                        from . import usage_tracker
                        usage_tracker.log_event({
                            "type": "vision", "chat_name": os.path.basename(chat_dir),
                            "provider": provider, "model": model, "file": filename,
                        }, os.path.dirname(os.path.dirname(cache_path)))
                    except Exception:
                        pass
                else:
                    error_count += 1
                    if error_count == 1:
                        first_error = desc  # Save first error for reporting
                processed_count += 1
                bar.update(1)
                try:
                    bar.set_postfix_str(filename[:35])
                except UnicodeEncodeError:
                    bar.set_postfix_str("...")

            if progress_callback:
                progress_callback(filename, processed_count, len(image_files))

    bar.close()
    success_count = len(cache) - (len(image_files) - len(to_process))  # newly described
    print(f"  Image descriptions complete. {success_count} new, {error_count} errors, {len(cache)} total cached.")
    if error_count > 0 and success_count == 0:
        raise RuntimeError(f"All {error_count} image descriptions failed. First error: {first_error}")
    return cache


def process_videos(
    chat_dir: str,
    descriptions_cache_path: str,
    video_trans_cache_path: str,
    provider: str = "anthropic",
    model: str = None,
    api_key: str = None,
    ollama_url: str = None,
    model_size: str = "small",
    progress_callback=None,
    cancel_event=None,
) -> tuple[dict, dict]:
    """Batch-process all video files.

    Returns (visual_descriptions_cache, video_transcriptions_cache).
    """
    if not _has_ffmpeg():
        print("  WARNING: ffmpeg not found. Skipping video processing.")
        print("  Install with: winget install ffmpeg")
        return load_cache(descriptions_cache_path), load_cache(video_trans_cache_path)

    video_files = sorted(glob.glob(os.path.join(chat_dir, "*.mp4")))
    video_files += sorted(glob.glob(os.path.join(chat_dir, "*.mov")))

    # Filter out GIF videos
    video_files = [f for f in video_files if not _should_skip_video(os.path.basename(f))]

    if not video_files:
        print("  No video files found.")
        return load_cache(descriptions_cache_path), load_cache(video_trans_cache_path)

    desc_cache = load_cache(descriptions_cache_path)
    trans_cache = load_cache(video_trans_cache_path)

    # Migrate old format entries (plain strings -> {text, language})
    migrated = False
    for key, val in trans_cache.items():
        if isinstance(val, str):
            trans_cache[key] = {"text": val, "language": "he"}
            migrated = True
    if migrated:
        save_cache(video_trans_cache_path, trans_cache)

    already = sum(1 for f in video_files if os.path.basename(f) in desc_cache)
    remaining = len(video_files) - already

    if remaining == 0:
        print(f"  All {len(video_files)} videos already processed.")
        return desc_cache, trans_cache

    print(f"  Found {len(video_files)} videos ({already} cached, {remaining} to process)")

    # Lazy-load Whisper only if we have videos to process
    whisper_model = None

    bar = tqdm(video_files, desc="  Processing videos", unit="file")
    for filepath in bar:
        if cancel_event and cancel_event.is_set():
            print("  Video processing cancelled by user.")
            break

        filename = os.path.basename(filepath)
        bar.set_postfix_str(filename[:35])

        if filename in desc_cache:
            continue

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. Extract and describe frames
            frames = extract_key_frames(filepath, tmp_dir)
            if frames:
                description = describe_video_frames(frames, provider=provider, model=model, api_key=api_key, ollama_url=ollama_url)
                # Don't cache error responses — leave for retry
                if description and not description.startswith("[vision error:"):
                    desc_cache[filename] = description
                else:
                    desc_cache[filename] = ""
            else:
                desc_cache[filename] = ""

            # 2. Extract and transcribe audio
            if filename not in trans_cache:
                audio_path = extract_audio_from_video(filepath, tmp_dir)
                if audio_path:
                    if whisper_model is None:
                        from faster_whisper import WhisperModel
                        print(f"\n  Loading Whisper model '{model_size}' for video audio...")
                        whisper_model = WhisperModel(model_size, device="auto", compute_type="auto")

                    try:
                        segments, info = whisper_model.transcribe(
                            audio_path, beam_size=5, vad_filter=True,
                        )
                        text = " ".join(seg.text.strip() for seg in segments)
                        detected_lang = info.language if info and hasattr(info, "language") else ""
                        trans_cache[filename] = {"text": text, "language": detected_lang}
                    except Exception as e:
                        trans_cache[filename] = {"text": f"[transcription error: {e}]", "language": ""}
                else:
                    trans_cache[filename] = {"text": "", "language": ""}

        save_cache(descriptions_cache_path, desc_cache)
        save_cache(video_trans_cache_path, trans_cache)

        # Log usage
        try:
            from . import usage_tracker
            project_root = os.path.dirname(os.path.dirname(descriptions_cache_path))
            vid_dur = get_video_duration(filepath)
            if desc_cache.get(filename):
                usage_tracker.log_event({
                    "type": "video_vision", "chat_name": os.path.basename(chat_dir),
                    "provider": provider, "model": model, "file": filename,
                    "video_duration_sec": vid_dur,
                }, project_root)
            t_entry = trans_cache.get(filename, {})
            t_text = t_entry.get("text", "") if isinstance(t_entry, dict) else t_entry
            if t_text and not t_text.startswith("["):
                usage_tracker.log_event({
                    "type": "video_transcription", "chat_name": os.path.basename(chat_dir),
                    "provider": "whisper", "model": f"faster-whisper ({model_size})",
                    "file": filename, "video_duration_sec": vid_dur,
                }, project_root)
        except Exception:
            pass

        if progress_callback:
            done = sum(1 for f in video_files if os.path.basename(f) in desc_cache)
            progress_callback(filename, done, len(video_files))

    print(f"  Video processing complete. {len(desc_cache)} descriptions, {len(trans_cache)} transcriptions.")
    return desc_cache, trans_cache


def process_pdfs(chat_dir: str, cache_path: str, progress_callback=None, cancel_event=None) -> dict:
    """Batch-process all PDF files. Returns cache dict {filename: extracted_text}."""
    pdf_files = sorted(glob.glob(os.path.join(chat_dir, "*.pdf")))
    pdf_files += sorted(glob.glob(os.path.join(chat_dir, "*.PDF")))

    if not pdf_files:
        print("  No PDF files found.")
        return load_cache(cache_path)

    cache = load_cache(cache_path)
    already = sum(1 for f in pdf_files if os.path.basename(f) in cache)
    remaining = len(pdf_files) - already

    if remaining == 0:
        print(f"  All {len(pdf_files)} PDFs already extracted.")
        return cache

    print(f"  Found {len(pdf_files)} PDFs ({already} cached, {remaining} to extract)")

    bar = tqdm(pdf_files, desc="  Extracting PDF text", unit="file")
    for filepath in bar:
        if cancel_event and cancel_event.is_set():
            print("  PDF extraction cancelled by user.")
            break

        filename = os.path.basename(filepath)
        try:
            bar.set_postfix_str(filename[:35])
        except UnicodeEncodeError:
            bar.set_postfix_str("...")

        if filename in cache:
            if progress_callback:
                done = sum(1 for f in pdf_files if os.path.basename(f) in cache)
                progress_callback(filename, done, len(pdf_files))
            continue

        text = extract_pdf_text(filepath)
        cache[filename] = text
        save_cache(cache_path, cache)

        # Log usage
        try:
            from . import usage_tracker
            page_count = text.count('\n\n') + 1 if text and not text.startswith('[') else 0
            usage_tracker.log_event({
                "type": "pdf", "chat_name": os.path.basename(chat_dir),
                "provider": "pymupdf", "model": "PyMuPDF", "file": filename,
                "pages": page_count,
            }, os.path.dirname(os.path.dirname(cache_path)))
        except Exception:
            pass

        if progress_callback:
            done = sum(1 for f in pdf_files if os.path.basename(f) in cache)
            progress_callback(filename, done, len(pdf_files))

    print(f"  PDF extraction complete. {len(cache)} files total.")
    return cache
