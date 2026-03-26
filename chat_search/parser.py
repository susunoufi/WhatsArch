"""WhatsApp and Telegram chat export parser."""

import json
import re
import os
from datetime import datetime


# Matches: [DD/MM/YYYY, H:MM:SS] or [DD/MM/YYYY, HH:MM:SS] Sender: Message
MSG_RE = re.compile(
    r"^\u200e?\[(\d{2}/\d{2}/\d{4}), (\d{1,2}:\d{2}:\d{2})\] (.+?): (.*)"
)

# System message line (no sender colon): [DD/MM/YYYY, H:MM:SS] system text
SYSTEM_MSG_RE = re.compile(
    r"^\u200e?\[(\d{2}/\d{2}/\d{4}), (\d{1,2}:\d{2}:\d{2})\] (.+)$"
)

# Matches: <attached: filename>
ATTACHMENT_RE = re.compile(r"<attached: (.+?)>")

# WhatsApp system messages that indicate a group chat
GROUP_INDICATORS_EN = [
    "created group", "added", "joined using", "changed the group",
    "changed this group", "removed", "left", "changed the subject",
    "changed the description",
]
GROUP_INDICATORS_HE = [
    "יצר את הקבוצה", "יצרה את הקבוצה",
    "הוסיף", "הוסיפה",
    "הצטרף באמצעות", "הצטרפה באמצעות",
    "שינה את", "שינתה את",
    "הוסר", "הוסרה", "עזב", "עזבה",
    "שינה את הנושא", "שינתה את הנושא",
]


def detect_media_type(filename: str) -> str:
    if not filename:
        return ""
    name = filename.upper()
    if "AUDIO" in name or name.endswith(".OPUS") or name.endswith(".MP3"):
        return "audio"
    if "PHOTO" in name or name.endswith(".JPG") or name.endswith(".PNG"):
        return "image"
    if "VIDEO" in name or name.endswith(".MP4"):
        return "video"
    if name.endswith(".VCF"):
        return "contact"
    if name.endswith(".PDF"):
        return "pdf"
    return "file"


def detect_chat_type(chat_path: str, messages: list = None) -> dict:
    """Detect whether a chat is 1-on-1 or a group chat.

    Uses two signals:
    1. Sender count: >2 unique senders → group
    2. System messages: WhatsApp group-related system messages

    Returns dict with 'chat_type' ('1on1' or 'group'), 'unique_senders' count,
    and 'has_system_indicators' bool.
    """
    unique_senders = set()
    has_system_indicators = False

    if messages:
        unique_senders = {m["sender"] for m in messages}

    # Scan raw file for system messages (lines that don't match sender pattern)
    with open(chat_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            # Skip normal messages (have sender: pattern)
            if MSG_RE.match(line):
                continue

            # Check for system message line
            sys_m = SYSTEM_MSG_RE.match(line)
            if sys_m:
                sys_text = sys_m.group(3).lower()
                for indicator in GROUP_INDICATORS_EN:
                    if indicator in sys_text:
                        has_system_indicators = True
                        break
                if not has_system_indicators:
                    for indicator in GROUP_INDICATORS_HE:
                        if indicator in sys_m.group(3):  # Hebrew is case-sensitive
                            has_system_indicators = True
                            break
            if has_system_indicators:
                break  # One indicator is enough

    is_group = len(unique_senders) > 2 or has_system_indicators

    return {
        "chat_type": "group" if is_group else "1on1",
        "unique_senders": len(unique_senders),
        "has_system_indicators": has_system_indicators,
    }


def add_name_mentions(messages: list) -> list:
    """Add 'mentioned_sender' field to each message.

    Checks if a message text contains another sender's name, which helps
    with group chat thread detection.
    """
    all_senders = {m["sender"] for m in messages}

    for msg in messages:
        mentioned = []
        text = msg.get("text", "") or ""
        sender = msg["sender"]
        for other_sender in all_senders:
            if other_sender == sender:
                continue
            if other_sender in text:
                mentioned.append(other_sender)
        msg["mentioned_sender"] = mentioned

    return messages


def parse_chat(
    chat_path: str,
    transcriptions: dict = None,
    visual_descriptions: dict = None,
    video_transcriptions: dict = None,
    pdf_texts: dict = None,
) -> list:
    """Parse _chat.txt and return list of message dicts.

    Each message dict has:
      date, time, datetime, sender, text, attachment, media_type,
      transcription, visual_description, video_transcription, pdf_text,
      mentioned_sender
    """
    if transcriptions is None:
        transcriptions = {}
    if visual_descriptions is None:
        visual_descriptions = {}
    if video_transcriptions is None:
        video_transcriptions = {}
    if pdf_texts is None:
        pdf_texts = {}

    messages = []
    current = None

    with open(chat_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            m = MSG_RE.match(line)

            if m:
                # Save previous message
                if current:
                    messages.append(current)

                date_str, time_str, sender, text = m.groups()

                # Extract attachment if present
                att_match = ATTACHMENT_RE.search(text)
                attachment = att_match.group(1) if att_match else ""
                media_type = detect_media_type(attachment)

                # Get transcription for audio attachments
                # Handles both old format (plain string) and new format ({text, language})
                transcription = ""
                if media_type == "audio" and attachment:
                    entry = transcriptions.get(attachment, "")
                    transcription = entry.get("text", "") if isinstance(entry, dict) else entry

                # Clean up text - remove the attachment tag for display, keep the rest
                display_text = text
                if att_match:
                    # Keep any text before the attachment tag
                    pre = text[: att_match.start()].strip()
                    # Remove various prefixes WhatsApp adds
                    pre = re.sub(r"^[\u200e\u200f]*", "", pre).strip()
                    # Remove file metadata like "filename.pdf * 1 page"
                    pre = re.sub(r".*?[•·].*?$", "", pre).strip()
                    display_text = pre if pre else ""

                dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M:%S")

                # Get visual description for image/video attachments
                visual_description = ""
                if media_type in ("image", "video") and attachment:
                    visual_description = visual_descriptions.get(attachment, "")

                # Get video audio transcription
                # Handles both old format (plain string) and new format ({text, language})
                video_transcription = ""
                if media_type == "video" and attachment:
                    entry = video_transcriptions.get(attachment, "")
                    video_transcription = entry.get("text", "") if isinstance(entry, dict) else entry

                # Get PDF text
                pdf_text = ""
                if media_type == "pdf" and attachment:
                    pdf_text = pdf_texts.get(attachment, "")

                current = {
                    "date": date_str,
                    "time": time_str,
                    "datetime": dt.isoformat(),
                    "sender": sender,
                    "text": display_text,
                    "attachment": attachment,
                    "media_type": media_type,
                    "transcription": transcription,
                    "visual_description": visual_description,
                    "video_transcription": video_transcription,
                    "pdf_text": pdf_text,
                    "mentioned_sender": [],
                }
            else:
                # Continuation line - append to current message
                if current:
                    current["text"] += "\n" + line

    # Don't forget the last message
    if current:
        messages.append(current)

    # Add name mention detection
    messages = add_name_mentions(messages)

    return messages


# ===================================================================
# Telegram export parser
# ===================================================================

def detect_telegram_media_type(media_type_field: str, file_path: str) -> str:
    """Map Telegram's media_type field to our internal type."""
    if not media_type_field and not file_path:
        return ""
    mt = (media_type_field or "").lower()
    if mt in ("voice_message", "audio_file"):
        return "audio"
    if mt in ("video_message", "video_file"):
        return "video"
    if mt in ("sticker", "animation"):
        return ""  # skip stickers/GIFs
    # Check file extension for photo or document
    if file_path:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            return "image"
        if ext in (".mp4", ".mov"):
            return "video"
        if ext in (".opus", ".ogg", ".mp3", ".m4a"):
            return "audio"
        if ext == ".pdf":
            return "pdf"
        if ext in (".vcf",):
            return "contact"
        return "file"
    if mt == "photo":
        return "image"
    return ""


def parse_telegram(
    export_path: str,
    transcriptions: dict = None,
    visual_descriptions: dict = None,
    video_transcriptions: dict = None,
    pdf_texts: dict = None,
) -> list:
    """Parse Telegram Desktop export (result.json) and return list of message dicts.

    Telegram exports contain a result.json with a 'messages' array.
    Media files are in subdirectories (photos/, video_files/, voice_messages/, etc).

    Returns same format as parse_chat() for full compatibility.
    """
    if transcriptions is None:
        transcriptions = {}
    if visual_descriptions is None:
        visual_descriptions = {}
    if video_transcriptions is None:
        video_transcriptions = {}
    if pdf_texts is None:
        pdf_texts = {}

    # Find the JSON file
    json_path = None
    if os.path.isfile(export_path) and export_path.endswith(".json"):
        json_path = export_path
    else:
        # Look for result.json in directory
        candidate = os.path.join(export_path, "result.json")
        if os.path.exists(candidate):
            json_path = candidate

    if not json_path:
        return []

    chat_dir = os.path.dirname(json_path)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_messages = data.get("messages", [])
    messages = []

    for msg in raw_messages:
        # Skip service messages (joins, leaves, etc)
        if msg.get("type") != "message":
            continue

        # Parse sender
        sender = msg.get("from", "") or msg.get("actor", "") or "Unknown"

        # Parse date
        date_str = msg.get("date", "")
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str)
        except (ValueError, TypeError):
            continue

        # Parse text - Telegram text can be string or list of segments
        raw_text = msg.get("text", "")
        if isinstance(raw_text, list):
            # List of text segments: [{"type": "plain", "text": "hello"}, ...]
            text_parts = []
            for part in raw_text:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    text_parts.append(part.get("text", ""))
            text = "".join(text_parts)
        else:
            text = str(raw_text)

        # Parse media
        file_path = msg.get("file", "") or msg.get("photo", "") or ""
        media_type_field = msg.get("media_type", "")

        # If it's a photo (no media_type field but has photo field)
        if msg.get("photo") and not media_type_field:
            media_type_field = "photo"

        media_type = detect_telegram_media_type(media_type_field, file_path)

        # Get the filename (basename) for cache lookups
        attachment = os.path.basename(file_path) if file_path else ""

        # Get transcription for audio
        transcription = ""
        if media_type == "audio" and attachment:
            entry = transcriptions.get(attachment, "")
            transcription = entry.get("text", "") if isinstance(entry, dict) else entry

        # Get visual description
        visual_description = ""
        if media_type in ("image", "video") and attachment:
            visual_description = visual_descriptions.get(attachment, "")

        # Get video transcription
        video_transcription = ""
        if media_type == "video" and attachment:
            entry = video_transcriptions.get(attachment, "")
            video_transcription = entry.get("text", "") if isinstance(entry, dict) else entry

        # Get PDF text
        pdf_text = ""
        if media_type == "pdf" and attachment:
            pdf_text = pdf_texts.get(attachment, "")

        messages.append({
            "date": dt.strftime("%d/%m/%Y"),
            "time": dt.strftime("%H:%M:%S"),
            "datetime": dt.isoformat(),
            "sender": sender,
            "text": text,
            "attachment": attachment,
            "media_type": media_type,
            "transcription": transcription,
            "visual_description": visual_description,
            "video_transcription": video_transcription,
            "pdf_text": pdf_text,
            "mentioned_sender": [],
        })

    # Add name mention detection
    messages = add_name_mentions(messages)

    return messages


def detect_platform(chat_dir: str) -> str:
    """Auto-detect if a chat directory is WhatsApp or Telegram export.

    Returns 'whatsapp', 'telegram', or 'unknown'.
    """
    if os.path.exists(os.path.join(chat_dir, "_chat.txt")):
        return "whatsapp"
    if os.path.exists(os.path.join(chat_dir, "result.json")):
        return "telegram"
    # Check for JSON files that look like Telegram exports
    for f in os.listdir(chat_dir):
        if f.endswith(".json"):
            try:
                with open(os.path.join(chat_dir, f), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if "messages" in data and isinstance(data["messages"], list):
                        if len(data["messages"]) > 0 and "from" in data["messages"][0]:
                            return "telegram"
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
    return "unknown"


def detect_chat_language(messages: list, sample_size: int = 100) -> str:
    """Detect the primary language of a chat based on message text.

    Samples messages and checks for character set patterns.
    Returns ISO 639-1 code: 'he', 'ar', 'ru', 'en', 'es', 'fr', 'de', 'pt', etc.
    """
    if not messages:
        return "en"

    # Sample messages evenly
    step = max(1, len(messages) // sample_size)
    sampled = [m["text"] for m in messages[::step] if m.get("text", "").strip()][:sample_size]
    combined = " ".join(sampled)

    if not combined.strip():
        return "en"

    # Count character ranges
    total = len(combined)
    hebrew = sum(1 for c in combined if '\u0590' <= c <= '\u05FF')
    arabic = sum(1 for c in combined if '\u0600' <= c <= '\u06FF')
    cyrillic = sum(1 for c in combined if '\u0400' <= c <= '\u04FF')
    cjk = sum(1 for c in combined if '\u4E00' <= c <= '\u9FFF')
    latin = sum(1 for c in combined if ('a' <= c <= 'z') or ('A' <= c <= 'Z'))

    # Thresholds (% of total chars)
    if hebrew / total > 0.1:
        return "he"
    if arabic / total > 0.1:
        return "ar"
    if cyrillic / total > 0.1:
        return "ru"
    if cjk / total > 0.05:
        return "zh"

    # Latin-based: try to differentiate by common words
    text_lower = combined.lower()
    spanish_words = ["que", "los", "las", "una", "para", "por", "como", "pero", "con"]
    french_words = ["les", "des", "une", "que", "dans", "pour", "pas", "avec", "est"]
    german_words = ["und", "der", "die", "das", "ist", "ein", "eine", "nicht", "ich"]
    portuguese_words = ["que", "uma", "para", "com", "por", "como", "mais", "isso"]

    scores = {
        "es": sum(1 for w in spanish_words if f" {w} " in text_lower),
        "fr": sum(1 for w in french_words if f" {w} " in text_lower),
        "de": sum(1 for w in german_words if f" {w} " in text_lower),
        "pt": sum(1 for w in portuguese_words if f" {w} " in text_lower),
    }

    best = max(scores, key=scores.get)
    if scores[best] >= 3:
        return best

    return "en"
