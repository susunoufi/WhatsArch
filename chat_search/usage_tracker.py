"""
Usage tracking module for WhatsArch.
Thread-safe JSON log with cost estimation and reporting.
"""

import json
import os
import threading
from datetime import datetime, timezone

MAX_ENTRIES = 10000
RECENT_LOG_LIMIT = 100

_lock = threading.Lock()

# Cost per 1K tokens (input, output) by provider+model prefix
COST_TABLE = {
    # Gemini
    ("gemini", "gemini-2.5-flash"): (0.000075, 0.0003),
    ("gemini", "gemini-2.5-flash"): (0.000075, 0.0003),
    ("gemini", "gemini-1.5-flash"): (0.000075, 0.0003),
    # OpenAI
    ("openai", "gpt-4o-mini"): (0.00015, 0.0006),
    ("openai", "gpt-4o"): (0.0025, 0.01),
    # Anthropic
    ("anthropic", "claude-3-5-haiku"): (0.001, 0.005),
    ("anthropic", "claude-3-5-sonnet"): (0.003, 0.015),
    ("anthropic", "claude-3-haiku"): (0.00025, 0.00125),
    ("anthropic", "claude-3-sonnet"): (0.003, 0.015),
    ("anthropic", "claude-sonnet"): (0.003, 0.015),
    ("anthropic", "claude-opus-4"): (0.015, 0.075),
}

# Flat per-item costs for vision
VISION_COST = {
    "gemini": 0.00004,       # per image
    "openai": 0.00015,
    "anthropic": 0.0004,
    "ollama": 0.0,
}

VIDEO_COST_PER_MIN = {
    "gemini": 0.0002,
    "openai": 0.001,
    "anthropic": 0.002,
    "ollama": 0.0,
}

# Local/free operations
FREE_TYPES = {"transcription", "pdf", "index", "embeddings"}


def _log_path(project_root: str) -> str:
    return os.path.join(project_root, "usage_log.json")


def _read_log(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _write_log(path: str, entries: list):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)


def estimate_cost(event: dict) -> float:
    """Estimate cost for a single event based on type, provider, model, and usage."""
    evt_type = event.get("type", "")
    provider = (event.get("provider") or "").lower()
    model = (event.get("model") or "").lower()

    # Free local operations
    if evt_type in FREE_TYPES or provider == "ollama":
        return 0.0

    # Vision: flat per-image cost
    if evt_type == "vision":
        return VISION_COST.get(provider, 0.0)

    # Video vision: per-minute cost
    if evt_type == "video_vision":
        mins = (event.get("video_duration_sec") or 0) / 60.0
        per_min = VIDEO_COST_PER_MIN.get(provider, 0.0)
        return per_min * max(mins, 0.1)  # minimum 0.1 min

    # Video transcription uses Whisper locally
    if evt_type == "video_transcription":
        return 0.0

    # Token-based cost (RAG and others with tokens)
    input_tokens = event.get("input_tokens") or 0
    output_tokens = event.get("output_tokens") or 0

    if input_tokens == 0 and output_tokens == 0:
        return 0.0

    # Find matching cost entry (try exact, then prefix match)
    cost_rates = None
    for (p, m), rates in COST_TABLE.items():
        if p == provider and model.startswith(m):
            cost_rates = rates
            break

    if cost_rates is None:
        # Fallback: use a generic mid-range estimate
        cost_rates = (0.001, 0.004)

    input_cost = (input_tokens / 1000.0) * cost_rates[0]
    output_cost = (output_tokens / 1000.0) * cost_rates[1]
    return input_cost + output_cost


def log_event(event: dict, project_root: str):
    """
    Append a usage event to the log file. Thread-safe.

    Expected event keys (all optional except type):
        type, chat_name, provider, model, file, user,
        input_tokens, output_tokens, cost_estimate,
        duration_sec, audio_duration_sec, video_duration_sec, pages
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event.get("type", "unknown"),
        "chat_name": event.get("chat_name"),
        "provider": event.get("provider"),
        "model": event.get("model"),
        "file": event.get("file"),
        "user": event.get("user"),
        "input_tokens": event.get("input_tokens"),
        "output_tokens": event.get("output_tokens"),
        "duration_sec": event.get("duration_sec"),
        "audio_duration_sec": event.get("audio_duration_sec"),
        "video_duration_sec": event.get("video_duration_sec"),
        "pages": event.get("pages"),
    }

    # Auto-estimate cost if not provided
    if event.get("cost_estimate") is not None:
        entry["cost_estimate"] = event["cost_estimate"]
    else:
        entry["cost_estimate"] = estimate_cost(event)

    path = _log_path(project_root)

    with _lock:
        entries = _read_log(path)
        entries.append(entry)
        # Rotate: keep only the most recent MAX_ENTRIES
        if len(entries) > MAX_ENTRIES:
            entries = entries[-MAX_ENTRIES:]
        _write_log(path, entries)


def _build_summary(entries: list) -> dict:
    """Build a usage summary from a list of log entries."""
    total_cost = 0.0
    by_model_map = {}  # (provider, model) -> aggregates
    by_type_map = {}   # type -> {count, cost}
    images_processed = 0
    videos_processed = 0
    total_video_dur = 0.0
    audios_transcribed = 0
    total_audio_dur = 0.0
    pdfs_extracted = 0
    pdfs_pages_total = 0

    for e in entries:
        cost = e.get("cost_estimate") or 0.0
        total_cost += cost
        evt_type = e.get("type", "unknown")

        # By model
        key = (e.get("provider") or "unknown", e.get("model") or "unknown")
        if key not in by_model_map:
            by_model_map[key] = {
                "provider": key[0],
                "model": key[1],
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
            }
        bm = by_model_map[key]
        bm["calls"] += 1
        bm["input_tokens"] += e.get("input_tokens") or 0
        bm["output_tokens"] += e.get("output_tokens") or 0
        bm["cost"] += cost

        # By type
        if evt_type not in by_type_map:
            by_type_map[evt_type] = {"count": 0, "cost": 0.0}
        by_type_map[evt_type]["count"] += 1
        by_type_map[evt_type]["cost"] += cost

        # Media stats
        if evt_type == "vision":
            images_processed += 1
        elif evt_type in ("video_vision", "video_transcription"):
            videos_processed += 1
            total_video_dur += e.get("video_duration_sec") or 0.0
        elif evt_type == "transcription":
            audios_transcribed += 1
            total_audio_dur += e.get("audio_duration_sec") or 0.0
        elif evt_type == "pdf":
            pdfs_extracted += 1
            pdfs_pages_total += e.get("pages") or 0

    # Round costs
    total_cost = round(total_cost, 6)
    by_model_list = sorted(by_model_map.values(), key=lambda x: x["cost"], reverse=True)
    for bm in by_model_list:
        bm["cost"] = round(bm["cost"], 6)
    for bt in by_type_map.values():
        bt["cost"] = round(bt["cost"], 6)

    return {
        "total_cost": total_cost,
        "by_model": by_model_list,
        "by_type": by_type_map,
        "media_stats": {
            "images_processed": images_processed,
            "videos_processed": videos_processed,
            "total_video_duration_sec": round(total_video_dur, 1),
            "audios_transcribed": audios_transcribed,
            "total_audio_duration_sec": round(total_audio_dur, 1),
            "pdfs_extracted": pdfs_extracted,
            "pdfs_pages_total": pdfs_pages_total,
        },
    }


def get_usage_report(project_root: str, chat_name=None, user: str = None) -> dict:
    """
    Return a usage summary and recent log entries.

    chat_name can be a single string, a list of strings, or None (all chats).
    Filter by user if provided.
    """
    path = _log_path(project_root)

    with _lock:
        all_entries = _read_log(path)

    # Normalize chat_name to a set for filtering
    chat_names = None
    if chat_name:
        if isinstance(chat_name, str):
            chat_names = {chat_name}
        else:
            chat_names = set(chat_name)

    entries = all_entries
    if chat_names:
        entries = [e for e in entries if e.get("chat_name") in chat_names]
    if user:
        entries = [e for e in entries if e.get("user") == user]

    # Collect unique users for filter dropdown
    all_users = sorted(set(e.get("user") or "unknown" for e in all_entries if e.get("user")))

    # Build aggregate summary
    summary = _build_summary(entries)

    # Build per-chat breakdown when multiple chats requested
    per_chat = {}
    if chat_names and len(chat_names) > 1:
        chat_entries = {}
        for e in entries:
            cn = e.get("chat_name") or "unknown"
            chat_entries.setdefault(cn, []).append(e)
        for cn, ce in chat_entries.items():
            per_chat[cn] = _build_summary(ce)
    elif chat_names and len(chat_names) == 1:
        cn = next(iter(chat_names))
        per_chat[cn] = summary

    # Recent log (newest first)
    recent = list(reversed(entries[-RECENT_LOG_LIMIT:]))

    return {
        "users": all_users,
        "summary": summary,
        "per_chat": per_chat,
        "log": recent,
    }
