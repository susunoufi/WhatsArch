"""WhatsApp chat export parser."""

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
