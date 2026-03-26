#!/usr/bin/env python3
"""WhatsApp Chat Search Tool - Multi-chat entry point.

Usage:
    python run.py                     # Process all chats and serve
    python run.py --skip-transcribe   # Skip transcription, just index and serve
    python run.py --port 8080         # Use a different port
    python run.py --model large-v3    # Use a larger Whisper model
    python run.py --chat "אבא"        # Process only a specific chat
"""

import argparse
import os
import shutil
import sys
import webbrowser

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHATS_DIR = os.path.join(SCRIPT_DIR, "chats")

# Extensions that belong to WhatsApp media exports
MEDIA_EXTENSIONS = {
    ".opus", ".mp3", ".m4a",  # audio
    ".jpg", ".jpeg", ".png", ".webp",  # images
    ".mp4", ".mov",  # video
    ".vcf",  # contacts
    ".pdf", ".xlsx", ".docx", ".pptx",  # documents
}


def migrate_legacy_layout():
    """Migrate from single-chat layout (files in root) to multi-chat layout (chats/ subfolder)."""
    chat_file = os.path.join(SCRIPT_DIR, "_chat.txt")
    if not os.path.exists(chat_file):
        return  # Nothing to migrate

    if os.path.exists(CHATS_DIR):
        return  # Already migrated

    print("=" * 60)
    print("Migrating to multi-chat structure...")
    print("=" * 60)

    target = os.path.join(CHATS_DIR, "\u05d0\u05d1\u05d0")  # אבא
    os.makedirs(target, exist_ok=True)

    # Move _chat.txt
    shutil.move(chat_file, os.path.join(target, "_chat.txt"))
    print("  Moved _chat.txt")

    # Move data/ directory (contains chat.db and transcriptions.json)
    data_dir = os.path.join(SCRIPT_DIR, "data")
    if os.path.isdir(data_dir):
        shutil.move(data_dir, os.path.join(target, "data"))
        print("  Moved data/")

    # Move all media files
    moved = 0
    for f in os.listdir(SCRIPT_DIR):
        full = os.path.join(SCRIPT_DIR, f)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext in MEDIA_EXTENSIONS:
            shutil.move(full, os.path.join(target, f))
            moved += 1

    print(f"  Moved {moved} media files")
    print(f"  Chat migrated to: chats/\u05d0\u05d1\u05d0/")
    print()


def discover_chats():
    """Find all chat folders inside chats/ that contain _chat.txt or result.json (Telegram)."""
    if not os.path.isdir(CHATS_DIR):
        return []
    chats = []
    for name in sorted(os.listdir(CHATS_DIR)):
        chat_dir = os.path.join(CHATS_DIR, name)
        if not os.path.isdir(chat_dir):
            continue
        # WhatsApp or Telegram
        chat_file = os.path.join(chat_dir, "_chat.txt")
        telegram_file = os.path.join(chat_dir, "result.json")
        if os.path.exists(chat_file) or os.path.exists(telegram_file):
            chats.append(name)
    return chats


def process_chat(
    chat_name,
    skip_transcribe=False,
    skip_vision=False,
    skip_embeddings=False,
    skip_chunking=False,
    model_size="small",
    force_chat_type=None,
):
    """Run transcription + vision + parsing + indexing + chunking for a single chat."""
    chat_dir = os.path.join(CHATS_DIR, chat_name)
    chat_file = os.path.join(chat_dir, "_chat.txt")
    data_dir = os.path.join(chat_dir, "data")
    db_path = os.path.join(data_dir, "chat.db")
    audio_cache_path = os.path.join(data_dir, "transcriptions.json")
    desc_cache_path = os.path.join(data_dir, "descriptions.json")
    video_trans_cache_path = os.path.join(data_dir, "video_transcriptions.json")
    pdf_cache_path = os.path.join(data_dir, "pdf_texts.json")

    os.makedirs(data_dir, exist_ok=True)

    print(f"\n  [{chat_name}] Processing...")

    # Step 1: Transcribe audio voice messages
    transcriptions = {}
    if not skip_transcribe:
        from chat_search.transcribe import transcribe_audio_files, load_cache
        transcriptions = transcribe_audio_files(chat_dir, audio_cache_path, model_size=model_size)
    else:
        from chat_search.transcribe import load_cache
        transcriptions = load_cache(audio_cache_path)
        print(f"  [{chat_name}] Loaded {len(transcriptions)} cached transcriptions")

    # Step 2: Vision processing (images, videos, PDFs)
    visual_descriptions = {}
    video_transcriptions = {}
    pdf_texts = {}

    if not skip_vision:
        from chat_search.vision import process_images, process_videos, process_pdfs, load_cache as load_vision_cache

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            print(f"  [{chat_name}] Processing images...")
            visual_descriptions = process_images(chat_dir, desc_cache_path, api_key)

            print(f"  [{chat_name}] Processing videos...")
            vid_descs, video_transcriptions = process_videos(
                chat_dir, desc_cache_path, video_trans_cache_path,
                api_key, model_size=model_size,
            )
            visual_descriptions.update(vid_descs)
        else:
            print(f"  [{chat_name}] Skipping image/video vision (no ANTHROPIC_API_KEY)")
            visual_descriptions = load_vision_cache(desc_cache_path)
            video_transcriptions = load_vision_cache(video_trans_cache_path)

        print(f"  [{chat_name}] Processing PDFs...")
        pdf_texts = process_pdfs(chat_dir, pdf_cache_path)
    else:
        from chat_search.vision import load_cache as load_vision_cache
        visual_descriptions = load_vision_cache(desc_cache_path)
        video_transcriptions = load_vision_cache(video_trans_cache_path)
        pdf_texts = load_vision_cache(pdf_cache_path)
        cached_total = len(visual_descriptions) + len(video_transcriptions) + len(pdf_texts)
        if cached_total > 0:
            print(f"  [{chat_name}] Loaded {cached_total} cached vision/PDF results")

    # Step 3: Parse and index (WhatsApp or Telegram)
    from chat_search.parser import parse_chat, parse_telegram, detect_chat_type, detect_platform, detect_chat_language
    from chat_search.indexer import build_index, save_chat_metadata

    platform = detect_platform(chat_dir)
    if platform == "telegram":
        messages = parse_telegram(chat_dir, transcriptions, visual_descriptions, video_transcriptions, pdf_texts)
        print(f"  [{chat_name}] Parsed {len(messages)} Telegram messages")
    else:
        messages = parse_chat(chat_file, transcriptions, visual_descriptions, video_transcriptions, pdf_texts)
        print(f"  [{chat_name}] Parsed {len(messages)} WhatsApp messages")

    # Detect chat language
    chat_language = detect_chat_language(messages)
    print(f"  [{chat_name}] Detected language: {chat_language}")
    build_index(messages, db_path)

    # Step 4: Detect chat type
    if force_chat_type:
        chat_type = force_chat_type
        print(f"  [{chat_name}] Chat type forced: {chat_type}")
    elif platform == "telegram":
        # Telegram: detect by sender count only
        unique = len({m["sender"] for m in messages})
        chat_type = "group" if unique > 2 else "1on1"
        print(f"  [{chat_name}] Telegram chat type: {chat_type} ({unique} senders)")
    else:
        chat_info = detect_chat_type(chat_file, messages)
        chat_type = chat_info["chat_type"]
        print(f"  [{chat_name}] Detected chat type: {chat_type} "
              f"({chat_info['unique_senders']} senders"
              f"{', has group indicators' if chat_info['has_system_indicators'] else ''})")

    save_chat_metadata(db_path, {
        "chat_type": chat_type,
        "platform": platform,
        "language": chat_language,
        "unique_senders": len({m["sender"] for m in messages}),
        "total_messages": len(messages),
    })

    # Step 5: Segment into conversation chunks
    if not skip_chunking:
        from chat_search.chunker import segment_into_chunks
        from chat_search.indexer import build_chunks

        embedding_model = None
        if chat_type == "group":
            # Load embedding model for semantic thread detection in group chats
            try:
                from chat_search.indexer import _get_embedding_model
                embedding_model = _get_embedding_model()
            except Exception:
                print(f"  [{chat_name}] Warning: could not load embedding model for thread detection")

        chunks = segment_into_chunks(messages, chat_type=chat_type, embedding_model=embedding_model)
        print(f"  [{chat_name}] Segmented {len(messages)} messages into {len(chunks)} chunks")

        build_chunks(chunks, db_path)

        # Step 6: Build chunk embeddings
        if not skip_embeddings:
            from chat_search.indexer import build_chunk_embeddings
            print(f"  [{chat_name}] Building chunk embeddings...")
            build_chunk_embeddings(chunks, db_path)
        else:
            chunk_embeddings_path = db_path.replace(".db", "_chunk_embeddings.npy")
            if os.path.exists(chunk_embeddings_path):
                print(f"  [{chat_name}] Using cached chunk embeddings")
            else:
                print(f"  [{chat_name}] Skipped chunk embeddings (no cache found)")
    else:
        print(f"  [{chat_name}] Skipped chunking")


def main():
    parser = argparse.ArgumentParser(description="WhatsApp Chat Search Tool (Multi-Chat)")
    parser.add_argument(
        "--skip-transcribe",
        action="store_true",
        help="Skip voice message transcription",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Web server port (default: 5000)",
    )
    parser.add_argument(
        "--model",
        default="small",
        help="Whisper model size: tiny, base, small, medium, large-v3 (default: small)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open browser",
    )
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Skip image/video/PDF vision processing (use cached results)",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip semantic embedding generation (use cached results)",
    )
    parser.add_argument(
        "--chat",
        default="",
        help="Process only a specific chat folder name",
    )
    parser.add_argument(
        "--skip-chunking",
        action="store_true",
        help="Skip the conversation chunking step",
    )
    parser.add_argument(
        "--group-mode",
        action="store_true",
        help="Force treat all chats as group chats (override auto-detection)",
    )
    parser.add_argument(
        "--1on1-mode",
        dest="one_on_one_mode",
        action="store_true",
        help="Force treat all chats as 1-on-1 chats (override auto-detection)",
    )
    args = parser.parse_args()

    # Determine forced chat type
    force_chat_type = None
    if args.group_mode:
        force_chat_type = "group"
        print("  Forcing group chat mode for all chats")
    elif args.one_on_one_mode:
        force_chat_type = "1on1"
        print("  Forcing 1-on-1 chat mode for all chats")

    # Step 0: Migrate legacy single-chat layout if needed
    migrate_legacy_layout()

    # Step 1: Discover chats
    chats = discover_chats()
    if not chats:
        print(f"Error: No chat folders found in {CHATS_DIR}")
        print("  Place WhatsApp export folders (containing _chat.txt) inside chats/")
        sys.exit(1)

    print("=" * 60)
    print(f"Found {len(chats)} chat(s): {', '.join(chats)}")
    print("=" * 60)

    # Step 2: Process chats
    if args.chat:
        if args.chat not in chats:
            print(f"Error: Chat '{args.chat}' not found. Available: {', '.join(chats)}")
            sys.exit(1)
        process_chat(args.chat, skip_transcribe=args.skip_transcribe,
                     skip_vision=args.skip_vision, skip_embeddings=args.skip_embeddings,
                     skip_chunking=args.skip_chunking,
                     model_size=args.model, force_chat_type=force_chat_type)
    else:
        for chat_name in chats:
            process_chat(chat_name, skip_transcribe=args.skip_transcribe,
                         skip_vision=args.skip_vision, skip_embeddings=args.skip_embeddings,
                         skip_chunking=args.skip_chunking,
                         model_size=args.model, force_chat_type=force_chat_type)

    # Step 3: Launch web server
    print()
    print("=" * 60)
    print("Launching web server")
    print("=" * 60)
    from chat_search.server import create_app

    app = create_app(CHATS_DIR)
    url = f"http://localhost:{args.port}"
    print(f"  Server running at {url}")
    print("  Press Ctrl+C to stop")

    if not args.no_browser:
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host="0.0.0.0", port=args.port, debug=False)


def create_web_app():
    """Create Flask app for production deployment (Railway/gunicorn)."""
    from chat_search.server import create_app
    os.makedirs(CHATS_DIR, exist_ok=True)
    return create_app(CHATS_DIR)


if __name__ == "__main__":
    main()
