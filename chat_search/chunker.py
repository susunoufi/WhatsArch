"""Conversation-aware chunking for WhatsApp messages.

Groups individual messages into overlapping conversation chunks for better
semantic embeddings. Supports both 1-on-1 and group chat modes.

1-on-1: Time-gap session splitting + sliding window with reply-after-gap bridging.
Group:  Thread detection (name addressing, participant pairs, semantic similarity)
        then per-thread chunking.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Chunk:
    chunk_id: int
    message_ids: list[int]       # 1-indexed message IDs (matches SQLite)
    start_message_id: int
    end_message_id: int
    start_datetime: str
    end_datetime: str
    senders: list[str]           # unique senders in this chunk
    combined_text: str           # formatted text for embedding
    message_count: int = 0
    has_media: bool = False
    chat_type: str = "1on1"
    thread_id: int | None = None
    thread_participants: list[str] = field(default_factory=list)
    bridging: bool = False


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def segment_into_chunks(
    messages: list[dict],
    chat_type: str = "1on1",
    session_gap_minutes: int = 30,
    window_size: int = 15,
    overlap: int = 5,
    embedding_model=None,
) -> list[Chunk]:
    """Segment messages into conversation chunks.

    For 1-on-1 chats:
      1. Split into sessions by time gap
      2. Handle reply-after-gap bridging
      3. Apply sliding window within each session

    For group chats:
      1. Detect threads (name mentions, participant pairs, semantic similarity)
      2. Chunk per thread with sliding window
      3. Duplicate ambient messages into all thread chunks

    Args:
        messages: List of message dicts from parser (0-indexed).
        chat_type: '1on1' or 'group'.
        session_gap_minutes: Minutes of silence to split sessions.
        window_size: Number of messages per chunk window.
        overlap: Number of messages overlapping between consecutive windows.
        embedding_model: Loaded sentence-transformers model for group thread detection.

    Returns:
        List of Chunk objects with 1-indexed message IDs.
    """
    if not messages:
        return []

    # Attach original index
    for i, msg in enumerate(messages):
        msg["_index"] = i

    if chat_type == "group":
        return _chunk_group_chat(
            messages, session_gap_minutes, window_size, overlap, embedding_model
        )
    else:
        return _chunk_1on1_chat(
            messages, session_gap_minutes, window_size, overlap
        )


# -----------------------------------------------------------------------
# 1-on-1 Chat Chunking
# -----------------------------------------------------------------------

def _chunk_1on1_chat(
    messages: list[dict],
    session_gap_minutes: int,
    window_size: int,
    overlap: int,
) -> list[Chunk]:
    """Chunk a 1-on-1 chat with time-gap splitting + bridging."""
    sessions = _split_into_sessions(messages, session_gap_minutes)
    sessions = _apply_bridging(sessions)

    chunks = []
    chunk_id = 1

    for session in sessions:
        bridging = getattr(session, "_bridging", False) if hasattr(session, "_bridging") else False
        # Check if this session was tagged as having bridging messages
        has_bridging = any(m.get("_bridging", False) for m in session)

        windows = _apply_sliding_window(session, window_size, overlap)
        for window_msgs in windows:
            chunk = _make_chunk(chunk_id, window_msgs, "1on1", bridging=has_bridging)
            chunks.append(chunk)
            chunk_id += 1

    return chunks


def _apply_bridging(sessions: list[list[dict]]) -> list[list[dict]]:
    """Handle reply-after-gap: short reply bursts get merged with previous session.

    When a new session starts with fewer than 4 short messages (under 50 chars each)
    before the next long gap, append them to the PREVIOUS session's last few messages
    AND also include them in the next chunk if one follows.
    """
    if len(sessions) <= 1:
        return sessions

    merged = []
    i = 0
    while i < len(sessions):
        session = sessions[i]

        # Check if this is a short reply burst
        if i > 0 and _is_short_burst(session):
            # Mark these messages as bridging
            for m in session:
                m["_bridging"] = True

            # Append to previous session
            if merged:
                merged[-1].extend(session)

            # Also prepend to next session if exists
            if i + 1 < len(sessions):
                sessions[i + 1] = session + sessions[i + 1]
        else:
            merged.append(session)

        i += 1

    return merged if merged else sessions


def _is_short_burst(session: list[dict]) -> bool:
    """Check if a session is a short reply burst (< 4 short messages)."""
    if len(session) > 4:
        return False
    for m in session:
        text = m.get("text", "") or ""
        if len(text) > 50:
            return False
    return True


# -----------------------------------------------------------------------
# Group Chat Thread Detection + Chunking
# -----------------------------------------------------------------------

def _chunk_group_chat(
    messages: list[dict],
    session_gap_minutes: int,
    window_size: int,
    overlap: int,
    embedding_model=None,
) -> list[Chunk]:
    """Chunk a group chat with thread detection."""
    # Step 1: Time-session split
    sessions = _split_into_sessions(messages, session_gap_minutes)

    chunks = []
    chunk_id = 1

    for session in sessions:
        # Step 2: Detect threads within this session
        threads = _detect_threads(session, embedding_model)

        # Step 3: Chunk per thread
        for thread_id_local, thread_data in threads.items():
            thread_msgs = thread_data["messages"]
            thread_participants = thread_data["participants"]
            is_ambient = thread_data.get("ambient", False)

            windows = _apply_sliding_window(thread_msgs, window_size, overlap)
            for window_msgs in windows:
                chunk = _make_chunk(
                    chunk_id, window_msgs, "group",
                    thread_id=thread_id_local if not is_ambient else 0,
                    thread_participants=thread_participants,
                )
                chunks.append(chunk)
                chunk_id += 1

    # Renumber thread_ids globally (they were per-session)
    _renumber_threads(chunks)

    return chunks


def _detect_threads(
    session: list[dict],
    embedding_model=None,
) -> dict:
    """Detect conversation threads within a session.

    Signals used:
    1. Sender addressing (message mentions another sender's name)
    2. Participant pair continuity (A->B->A patterns)
    3. Semantic similarity between nearby messages (if model available)
    4. Time proximity per sender pair

    Returns dict: thread_id -> {messages: [...], participants: [...], ambient: bool}
    """
    if len(session) <= 3:
        # Too short to thread-detect, treat as single ambient thread
        all_senders = list(dict.fromkeys(m["sender"] for m in session))
        return {0: {"messages": session, "participants": all_senders, "ambient": True}}

    n = len(session)
    all_senders = list(dict.fromkeys(m["sender"] for m in session))

    # Initialize: each message starts unassigned (thread_id = -1)
    msg_thread = [-1] * n

    # ---- Signal 1: Name addressing ----
    # If message mentions another sender, link them
    address_links = []  # (msg_idx, addressed_sender)
    for i, m in enumerate(session):
        mentioned = m.get("mentioned_sender", [])
        for name in mentioned:
            address_links.append((i, name))

    # ---- Signal 2: Participant pair continuity ----
    # Track conversation pairs: if A sends to B (via addressing) and B replies, they form a pair
    pair_exchanges = defaultdict(list)  # frozenset(A,B) -> [msg_indices]

    for i, m in enumerate(session):
        sender = m["sender"]
        mentioned = m.get("mentioned_sender", [])
        for name in mentioned:
            pair_key = frozenset([sender, name])
            pair_exchanges[pair_key].append(i)

    # Also detect implicit pairs via alternating sender patterns (A->B->A within 3 minutes)
    for i in range(2, n):
        if session[i]["sender"] == session[i - 2]["sender"] and session[i]["sender"] != session[i - 1]["sender"]:
            dt_i = _parse_datetime(session[i]["datetime"])
            dt_prev = _parse_datetime(session[i - 2]["datetime"])
            if dt_i and dt_prev and (dt_i - dt_prev).total_seconds() <= 180:
                pair_key = frozenset([session[i]["sender"], session[i - 1]["sender"]])
                pair_exchanges[pair_key].extend([i - 2, i - 1, i])

    # Deduplicate indices per pair
    for pair_key in pair_exchanges:
        pair_exchanges[pair_key] = sorted(set(pair_exchanges[pair_key]))

    # ---- Signal 3: Semantic similarity (optional) ----
    semantic_links = []  # (idx_a, idx_b) pairs with high similarity
    if embedding_model and n <= 500:
        semantic_links = _compute_semantic_links(session, embedding_model)

    # ---- Build thread chains using Union-Find ----
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Merge based on pair exchanges
    for pair_key, indices in pair_exchanges.items():
        for j in range(1, len(indices)):
            union(indices[0], indices[j])

    # Merge based on addressing
    for msg_idx, addressed_sender in address_links:
        # Find nearby messages from addressed sender to link
        for j in range(max(0, msg_idx - 5), min(n, msg_idx + 5)):
            if session[j]["sender"] == addressed_sender:
                union(msg_idx, j)
                break

    # Merge based on semantic similarity
    for idx_a, idx_b in semantic_links:
        union(idx_a, idx_b)

    # ---- Signal 4: Time proximity per sender pair ----
    # Messages from the same sender pair within 3 minutes get merged
    last_msg_time = {}  # (sender, other_sender) -> (datetime, msg_idx)
    for i, m in enumerate(session):
        sender = m["sender"]
        dt = _parse_datetime(m["datetime"])
        if not dt:
            continue

        # Check against recent messages from other senders in same thread
        for j in range(max(0, i - 8), i):
            other = session[j]
            if other["sender"] == sender:
                continue
            other_dt = _parse_datetime(other["datetime"])
            if other_dt and (dt - other_dt).total_seconds() <= 180:
                if find(i) == find(j) or find(i - 1) == find(j):
                    union(i, j)

    # ---- Collect threads ----
    thread_groups = defaultdict(list)
    for i in range(n):
        root = find(i)
        thread_groups[root].append(i)

    # ---- Classify threads: real threads vs ambient ----
    threads = {}
    ambient_msgs = []
    thread_counter = 1

    for root, indices in thread_groups.items():
        participants = list(dict.fromkeys(session[i]["sender"] for i in indices))

        if len(indices) <= 2 and len(participants) <= 1:
            # Single message or monologue - ambient
            ambient_msgs.extend(indices)
        else:
            msgs = [session[i] for i in sorted(indices)]
            threads[thread_counter] = {
                "messages": msgs,
                "participants": participants,
                "ambient": False,
            }
            thread_counter += 1

    # Ambient thread: messages that couldn't be assigned
    if ambient_msgs:
        ambient_msg_objs = [session[i] for i in sorted(ambient_msgs)]
        threads[0] = {
            "messages": ambient_msg_objs,
            "participants": all_senders,
            "ambient": True,
        }

    # ---- Duplicate ambient messages into other threads ----
    if 0 in threads and len(threads) > 1:
        ambient_msg_objs = threads[0]["messages"]
        for tid in threads:
            if tid == 0:
                continue
            thread_msgs = threads[tid]["messages"]
            # Add ambient messages that fall within this thread's time window
            if thread_msgs:
                t_start = _parse_datetime(thread_msgs[0]["datetime"])
                t_end = _parse_datetime(thread_msgs[-1]["datetime"])
                if t_start and t_end:
                    for am in ambient_msg_objs:
                        am_dt = _parse_datetime(am["datetime"])
                        if am_dt and t_start <= am_dt <= t_end:
                            thread_msgs.append(am)
                    # Re-sort by original index
                    threads[tid]["messages"] = sorted(
                        thread_msgs, key=lambda m: m["_index"]
                    )

    return threads


def _compute_semantic_links(
    session: list[dict], embedding_model, window: int = 10, threshold: float = 0.6
) -> list[tuple[int, int]]:
    """Compute semantic similarity between nearby messages for thread detection.

    Only checks within a sliding window of `window` messages for performance.
    Returns list of (idx_a, idx_b) pairs with similarity > threshold.
    """
    import numpy as np

    n = len(session)
    texts = []
    for m in session:
        parts = []
        if m.get("text"):
            parts.append(m["text"])
        if m.get("transcription"):
            parts.append(m["transcription"])
        combined = " ".join(parts).strip()
        texts.append(f"passage: {combined}" if combined else "passage: .")

    # Batch encode
    embeddings = embedding_model.encode(texts, normalize_embeddings=True, batch_size=64)

    links = []
    for i in range(n):
        for j in range(i + 1, min(i + window, n)):
            if session[i]["sender"] == session[j]["sender"]:
                continue  # Same sender, skip
            sim = float(embeddings[i] @ embeddings[j])
            if sim > threshold:
                links.append((i, j))

    return links


def _renumber_threads(chunks: list[Chunk]):
    """Renumber thread IDs globally across all chunks."""
    # Collect unique (original) thread IDs, map to sequential global IDs
    seen = {}
    counter = 1
    for chunk in chunks:
        if chunk.thread_id is None or chunk.thread_id == 0:
            chunk.thread_id = 0
            continue
        if chunk.thread_id not in seen:
            seen[chunk.thread_id] = counter
            counter += 1
        chunk.thread_id = seen[chunk.thread_id]


# -----------------------------------------------------------------------
# Common Helpers
# -----------------------------------------------------------------------

def _split_into_sessions(
    messages: list[dict], gap_minutes: int
) -> list[list[dict]]:
    """Split messages into sessions by time gaps."""
    if not messages:
        return []

    sessions = []
    current_session = [messages[0]]

    for i in range(1, len(messages)):
        prev_dt = _parse_datetime(messages[i - 1]["datetime"])
        curr_dt = _parse_datetime(messages[i]["datetime"])

        if prev_dt and curr_dt:
            gap = (curr_dt - prev_dt).total_seconds() / 60.0
            if gap > gap_minutes:
                sessions.append(current_session)
                current_session = []

        current_session.append(messages[i])

    if current_session:
        sessions.append(current_session)

    return sessions


def _apply_sliding_window(
    session: list[dict], window_size: int, overlap: int
) -> list[list[dict]]:
    """Apply sliding window to a session, returning list of message windows.

    - Small sessions (<8 messages): single chunk
    - Adjusts window size for messages with long content (>200 chars)
    - Tail handling: skip remainder if < overlap messages
    """
    n = len(session)
    stride = window_size - overlap

    if n < 8:
        return [session]

    # Check for long messages and reduce window size if needed
    effective_window = window_size
    long_count = sum(1 for m in session if len(m.get("text", "") or "") > 200)
    if long_count > n * 0.3:
        effective_window = max(8, window_size - 3)
        stride = effective_window - overlap

    windows = []
    start = 0
    while start < n:
        end = min(start + effective_window, n)
        window = session[start:end]

        # Skip tiny tail if already covered by previous window's overlap
        if windows and len(window) < overlap:
            break

        windows.append(window)

        if end >= n:
            break
        start += stride

    return windows if windows else [session]


def _make_chunk(
    chunk_id: int,
    messages: list[dict],
    chat_type: str,
    thread_id: int | None = None,
    thread_participants: list[str] | None = None,
    bridging: bool = False,
) -> Chunk:
    """Create a Chunk from a list of messages."""
    msg_ids = [m["_index"] + 1 for m in messages]  # 1-indexed
    senders = list(dict.fromkeys(m["sender"] for m in messages))
    has_media = any(m.get("media_type") and m["media_type"] not in ("", "file") for m in messages)

    return Chunk(
        chunk_id=chunk_id,
        message_ids=msg_ids,
        start_message_id=msg_ids[0],
        end_message_id=msg_ids[-1],
        start_datetime=messages[0]["datetime"],
        end_datetime=messages[-1]["datetime"],
        senders=senders,
        combined_text=_format_chunk_text(messages),
        message_count=len(messages),
        has_media=has_media,
        chat_type=chat_type,
        thread_id=thread_id,
        thread_participants=thread_participants or senders,
        bridging=bridging,
    )


def _format_chunk_text(messages: list[dict]) -> str:
    """Format messages into combined text for embedding.

    Format: [Sender HH:MM] text content [תמלול: ...] [תיאור חזותי: ...]
    """
    lines = []
    for m in messages:
        # Extract time from datetime (HH:MM)
        dt_str = m.get("datetime", "")
        time_part = ""
        if len(dt_str) >= 16:
            time_part = dt_str[11:16]  # "HH:MM" from "YYYY-MM-DDTHH:MM"

        sender = m.get("sender", "?")

        parts = []
        if m.get("text"):
            parts.append(m["text"])
        if m.get("transcription"):
            parts.append(f"[תמלול: {m['transcription']}]")
        if m.get("visual_description"):
            parts.append(f"[תיאור חזותי: {m['visual_description']}]")
        if m.get("video_transcription"):
            parts.append(f"[תמלול וידאו: {m['video_transcription']}]")
        if m.get("pdf_text"):
            parts.append(f"[טקסט PDF: {m['pdf_text']}]")

        content = " ".join(parts).strip()
        if not content:
            continue

        lines.append(f"[{sender} {time_part}] {content}")

    return "\n".join(lines)


def _parse_datetime(dt_str: str) -> datetime | None:
    """Parse ISO datetime string, returning None on failure."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None
