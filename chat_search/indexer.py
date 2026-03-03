"""SQLite FTS5 search index builder with semantic embedding support."""

import sqlite3
import os

# Module-level caches for semantic search (loaded once, reused across queries)
_embedding_cache = {}  # db_path -> numpy array (legacy message-level)
_chunk_embedding_cache = {}  # db_path -> numpy array (chunk-level)
_model_cache = {}      # singleton


def build_index(messages: list, db_path: str):
    """Build SQLite database with FTS5 full-text search index."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Remove old DB to rebuild fresh
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            datetime TEXT,
            sender TEXT,
            text TEXT,
            attachment TEXT,
            media_type TEXT,
            transcription TEXT,
            visual_description TEXT,
            video_transcription TEXT,
            pdf_text TEXT
        )
    """)

    c.execute("""
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            text,
            transcription,
            sender,
            visual_description,
            video_transcription,
            pdf_text,
            content='messages',
            content_rowid='id',
            tokenize='trigram'
        )
    """)

    # Bulk insert
    for msg in messages:
        c.execute(
            "INSERT INTO messages (datetime, sender, text, attachment, media_type, "
            "transcription, visual_description, video_transcription, pdf_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg["datetime"],
                msg["sender"],
                msg["text"],
                msg["attachment"],
                msg["media_type"],
                msg["transcription"],
                msg.get("visual_description", ""),
                msg.get("video_transcription", ""),
                msg.get("pdf_text", ""),
            ),
        )

    # Populate FTS index
    c.execute("""
        INSERT INTO messages_fts (rowid, text, transcription, sender,
                                  visual_description, video_transcription, pdf_text)
        SELECT id, text, transcription, sender,
               visual_description, video_transcription, pdf_text
        FROM messages
    """)

    # Chat metadata table
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Chunks table (populated later by build_chunks)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            start_message_id INTEGER NOT NULL,
            end_message_id INTEGER NOT NULL,
            start_datetime TEXT NOT NULL,
            end_datetime TEXT NOT NULL,
            combined_text TEXT NOT NULL,
            senders TEXT NOT NULL,
            message_count INTEGER DEFAULT 0,
            has_media BOOLEAN DEFAULT 0,
            chat_type TEXT DEFAULT '1on1',
            thread_id INTEGER,
            thread_participants TEXT,
            bridging BOOLEAN DEFAULT 0
        )
    """)
    c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            combined_text, senders,
            content='chunks', content_rowid='id',
            tokenize='trigram'
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_range ON chunks(start_message_id, end_message_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_thread ON chunks(thread_id)")

    conn.commit()
    conn.close()

    print(f"Indexed {len(messages)} messages into {db_path}")


def save_chat_metadata(db_path: str, metadata: dict):
    """Save chat metadata key-value pairs."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for key, value in metadata.items():
        c.execute(
            "INSERT OR REPLACE INTO chat_metadata (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
    conn.commit()
    conn.close()


def get_chat_metadata(db_path: str) -> dict:
    """Load chat metadata as a dict."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("SELECT key, value FROM chat_metadata")
        result = {row[0]: row[1] for row in c.fetchall()}
    except Exception:
        result = {}
    conn.close()
    return result


def search(db_path: str, query: str, sender: str = "", date_from: str = "", date_to: str = "", page: int = 1, per_page: int = 50, search_type: str = "all"):
    """Search messages using FTS5. Returns (results, total_count).
    search_type: 'all' (text+transcription), 'text' (text only), 'transcription' (transcription only)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    conditions = []
    params = []

    if sender:
        sender_list = [s.strip() for s in sender.split(",") if s.strip()]
        if len(sender_list) == 1:
            conditions.append("m.sender = ?")
            params.append(sender_list[0])
        elif len(sender_list) > 1:
            placeholders = ",".join(["?"] * len(sender_list))
            conditions.append(f"m.sender IN ({placeholders})")
            params.extend(sender_list)
    if date_from:
        conditions.append("m.datetime >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("m.datetime <= ?")
        params.append(date_to + "T23:59:59")

    # Filter by search type (supports comma-separated multiple types)
    if search_type and search_type != "all":
        type_list = [t.strip() for t in search_type.split(",") if t.strip()]
        type_conds = []
        for st in type_list:
            if st == "text":
                type_conds.append("(m.text IS NOT NULL AND m.text != '')")
            elif st == "transcription":
                type_conds.append("(m.transcription IS NOT NULL AND m.transcription != '')")
            elif st == "visual":
                type_conds.append("(m.visual_description IS NOT NULL AND m.visual_description != '')")
            elif st == "pdf":
                type_conds.append("(m.pdf_text IS NOT NULL AND m.pdf_text != '')")
        if type_conds:
            conditions.append("(" + " OR ".join(type_conds) + ")")

    where = ""
    if conditions:
        where = "AND " + " AND ".join(conditions)

    # Trigram tokenizer needs at least 3 chars; fall back to LIKE for shorter queries
    use_fts = len(query) >= 3

    if use_fts:
        safe_query = query.replace('"', '""')
        fts_match = f'"{safe_query}"'

        count_sql = f"""
            SELECT COUNT(*) FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            WHERE messages_fts MATCH ? {where}
        """
        c.execute(count_sql, [fts_match] + params)
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        results_sql = f"""
            SELECT
                m.id,
                m.datetime,
                m.sender,
                m.text,
                m.attachment,
                m.media_type,
                m.transcription,
                m.visual_description,
                m.video_transcription,
                m.pdf_text,
                snippet(messages_fts, 0, '<mark>', '</mark>', '...', 30) as text_snippet,
                snippet(messages_fts, 1, '<mark>', '</mark>', '...', 30) as transcription_snippet,
                snippet(messages_fts, 3, '<mark>', '</mark>', '...', 30) as visual_description_snippet,
                snippet(messages_fts, 4, '<mark>', '</mark>', '...', 30) as video_transcription_snippet,
                snippet(messages_fts, 5, '<mark>', '</mark>', '...', 30) as pdf_text_snippet
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            WHERE messages_fts MATCH ? {where}
            ORDER BY m.datetime DESC
            LIMIT ? OFFSET ?
        """
        c.execute(results_sql, [fts_match] + params + [per_page, offset])
    else:
        like_pattern = f"%{query}%"

        type_list = [t.strip() for t in search_type.split(",") if t.strip()] if search_type and search_type != "all" else []

        if type_list:
            like_fields = []
            type_to_col = {
                "text": "m.text",
                "transcription": "m.transcription",
                "visual": "m.visual_description",
                "pdf": "m.pdf_text",
            }
            for st in type_list:
                col = type_to_col.get(st)
                if col:
                    like_fields.append(f"{col} LIKE ?")
            if not like_fields:
                like_fields = ["m.text LIKE ?"]
            like_where = "(" + " OR ".join(like_fields) + ")"
            like_params = [like_pattern] * len(like_fields)
        else:
            like_where = ("(m.text LIKE ? OR m.transcription LIKE ? "
                          "OR m.visual_description LIKE ? OR m.video_transcription LIKE ? "
                          "OR m.pdf_text LIKE ?)")
            like_params = [like_pattern] * 5

        count_sql = f"""
            SELECT COUNT(*) FROM messages m
            WHERE {like_where} {where}
        """
        c.execute(count_sql, like_params + params)
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        results_sql = f"""
            SELECT
                m.id,
                m.datetime,
                m.sender,
                m.text,
                m.attachment,
                m.media_type,
                m.transcription,
                m.visual_description,
                m.video_transcription,
                m.pdf_text,
                '' as text_snippet,
                '' as transcription_snippet,
                '' as visual_description_snippet,
                '' as video_transcription_snippet,
                '' as pdf_text_snippet
            FROM messages m
            WHERE {like_where} {where}
            ORDER BY m.datetime DESC
            LIMIT ? OFFSET ?
        """
        c.execute(results_sql, like_params + params + [per_page, offset])

    results = [dict(row) for row in c.fetchall()]

    # For short queries (LIKE fallback), add highlighting manually
    if not use_fts and query:
        import re
        esc_query = re.escape(query)
        pattern = re.compile(f'({esc_query})', re.IGNORECASE)
        for r in results:
            q_lower = query.lower()
            for field, snippet_key in [
                ('text', 'text_snippet'),
                ('transcription', 'transcription_snippet'),
                ('visual_description', 'visual_description_snippet'),
                ('video_transcription', 'video_transcription_snippet'),
                ('pdf_text', 'pdf_text_snippet'),
            ]:
                if r.get(field) and q_lower in r[field].lower():
                    r[snippet_key] = pattern.sub(r'<mark>\1</mark>', r[field])

    conn.close()

    return results, total


def get_context(db_path: str, message_id: int, before: int = 5, after: int = 5):
    """Get surrounding messages for context."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute(
        "SELECT * FROM messages WHERE id BETWEEN ? AND ? ORDER BY id",
        (message_id - before, message_id + after),
    )
    results = [dict(row) for row in c.fetchall()]
    conn.close()
    return results


def get_stats(db_path: str) -> dict:
    """Get chat statistics."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM messages")
    total = c.fetchone()[0]

    c.execute("SELECT MIN(datetime), MAX(datetime) FROM messages")
    date_min, date_max = c.fetchone()

    c.execute("SELECT sender, COUNT(*) FROM messages GROUP BY sender")
    senders = {row[0]: row[1] for row in c.fetchall()}

    c.execute("SELECT COUNT(*) FROM messages WHERE media_type = 'audio'")
    audio_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE transcription != '' AND transcription IS NOT NULL")
    transcribed_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE media_type = 'image'")
    image_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE media_type = 'video'")
    video_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE media_type = 'pdf'")
    pdf_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE visual_description != '' AND visual_description IS NOT NULL")
    described_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE pdf_text != '' AND pdf_text IS NOT NULL")
    pdf_extracted_count = c.fetchone()[0]

    conn.close()

    return {
        "total_messages": total,
        "date_range": {"from": date_min, "to": date_max},
        "senders": senders,
        "audio_messages": audio_count,
        "transcribed_messages": transcribed_count,
        "image_messages": image_count,
        "video_messages": video_count,
        "pdf_messages": pdf_count,
        "described_media": described_count,
        "pdf_extracted": pdf_extracted_count,
    }


# --- Semantic embedding support ---

EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-large"


def _get_embedding_model():
    """Lazy-load and cache the sentence transformer model."""
    if "model" not in _model_cache:
        from sentence_transformers import SentenceTransformer
        _model_cache["model"] = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model_cache["model"]


def _get_embeddings(db_path: str):
    """Load and cache embeddings array for a given db_path."""
    if db_path not in _embedding_cache:
        import numpy as np
        embeddings_path = db_path.replace(".db", "_embeddings.npy")
        if not os.path.exists(embeddings_path):
            return None
        _embedding_cache[db_path] = np.load(embeddings_path)
    return _embedding_cache[db_path]


def build_embeddings(messages: list, db_path: str):
    """Build semantic embedding vectors for all messages.

    Stores a numpy .npy file alongside chat.db.
    Row index i corresponds to SQLite message id i+1 (1-indexed).
    """
    import numpy as np

    embeddings_path = db_path.replace(".db", "_embeddings.npy")

    # Combine all text fields into one string per message
    texts = []
    for msg in messages:
        parts = []
        if msg.get("text"):
            parts.append(msg["text"])
        if msg.get("transcription"):
            parts.append(msg["transcription"])
        if msg.get("visual_description"):
            parts.append(msg["visual_description"])
        if msg.get("video_transcription"):
            parts.append(msg["video_transcription"])
        if msg.get("pdf_text"):
            parts.append(msg["pdf_text"])
        texts.append(" ".join(parts).strip() if parts else "")

    model = _get_embedding_model()

    print(f"  Encoding {len(texts)} messages...")
    embeddings = model.encode(
        texts,
        batch_size=256,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    np.save(embeddings_path, embeddings)
    print(f"  Saved embeddings ({embeddings.shape}) to {embeddings_path}")

    # Invalidate cache so next query picks up fresh embeddings
    _embedding_cache.pop(db_path, None)


def semantic_search(db_path: str, queries, top_k: int = 30) -> list:
    """Find messages semantically similar to the query or queries.

    Accepts a single query string or a list of query strings.
    When multiple queries are given, uses round-robin merge: each query's
    ranked results are interleaved fairly so no single generic query can
    drown out specific matches from other queries.

    Returns list of (message_id, similarity_score) tuples sorted by
    similarity descending. message_id is 1-indexed (matches SQLite).
    Returns empty list if embeddings file doesn't exist.
    """
    import numpy as np

    embeddings = _get_embeddings(db_path)
    if embeddings is None:
        return []

    model = _get_embedding_model()

    if isinstance(queries, str):
        queries = [queries]

    # Batch-encode all queries at once
    query_embeddings = model.encode(queries, normalize_embeddings=True)

    # Compute all similarities at once: (num_queries, num_messages)
    all_similarities = query_embeddings @ embeddings.T

    # Pre-sort each query's results by similarity (descending)
    sorted_indices = []
    for qi in range(len(queries)):
        order = np.argsort(all_similarities[qi])[::-1]
        sorted_indices.append(order)

    # Round-robin merge: take rank-1 from each query, then rank-2, etc.
    # This ensures every query gets fair representation regardless of score range
    candidates = {}  # msg_id -> max score across queries
    seen = set()
    rank_ptr = [0] * len(queries)  # current rank pointer per query

    while len(candidates) < top_k:
        added_this_round = False
        for qi in range(len(queries)):
            while rank_ptr[qi] < len(sorted_indices[qi]):
                idx = int(sorted_indices[qi][rank_ptr[qi]])
                rank_ptr[qi] += 1
                score = float(all_similarities[qi][idx])
                if score <= 0.15:
                    break  # No more useful results from this query
                msg_id = idx + 1
                if msg_id not in seen:
                    seen.add(msg_id)
                    candidates[msg_id] = score
                    added_this_round = True
                    break  # Move to next query (round-robin)
                else:
                    # Update score if higher
                    candidates[msg_id] = max(candidates[msg_id], score)
            # If this query is exhausted, skip it
        if not added_this_round:
            break  # All queries exhausted

    # Sort by score descending for final output
    sorted_candidates = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
    return sorted_candidates[:top_k]


# --- Chunk support ---


def build_chunks(chunks: list, db_path: str):
    """Insert chunk records into the chunks table and populate chunks_fts.

    Args:
        chunks: List of Chunk dataclass instances from chunker.py.
        db_path: Path to the SQLite database.
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Clear existing chunks (rebuild fresh)
    c.execute("DELETE FROM chunks")
    c.execute("DELETE FROM chunks_fts")

    for chunk in chunks:
        senders_str = ", ".join(chunk.senders)
        thread_participants = ""
        if hasattr(chunk, "thread_participants") and chunk.thread_participants:
            thread_participants = ", ".join(chunk.thread_participants)
        c.execute(
            "INSERT INTO chunks (id, start_message_id, end_message_id, "
            "start_datetime, end_datetime, combined_text, senders, "
            "message_count, has_media, chat_type, thread_id, "
            "thread_participants, bridging) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chunk.chunk_id,
                chunk.start_message_id,
                chunk.end_message_id,
                chunk.start_datetime,
                chunk.end_datetime,
                chunk.combined_text,
                senders_str,
                getattr(chunk, "message_count", len(chunk.message_ids)),
                getattr(chunk, "has_media", False),
                getattr(chunk, "chat_type", "1on1"),
                getattr(chunk, "thread_id", None),
                thread_participants,
                getattr(chunk, "bridging", False),
            ),
        )

    # Populate FTS index
    c.execute("""
        INSERT INTO chunks_fts (rowid, combined_text, senders)
        SELECT id, combined_text, senders FROM chunks
    """)

    conn.commit()
    conn.close()
    print(f"  Inserted {len(chunks)} chunks into {db_path}")


def build_chunk_embeddings(chunks: list, db_path: str, cancel_event=None, progress_callback=None):
    """Build semantic embeddings for conversation chunks.

    Uses E5-large with "passage: " prefix. Saves as chat_chunk_embeddings.npy.
    Row index i corresponds to chunk id i+1 (1-indexed).
    Encodes in batches with cancel support between each batch.
    """
    import numpy as np

    embeddings_path = db_path.replace(".db", "_chunk_embeddings.npy")

    texts = [f"passage: {chunk.combined_text}" for chunk in chunks]

    model = _get_embedding_model()

    batch_size = 32
    num_batches = (len(texts) + batch_size - 1) // batch_size
    print(f"  Encoding {len(texts)} chunks ({num_batches} batches) with {EMBEDDING_MODEL_NAME}...")

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        if cancel_event and cancel_event.is_set():
            print("  Embedding cancelled by user.")
            raise EmbeddingCancelled()

        batch = texts[i:i + batch_size]
        batch_emb = model.encode(
            batch,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        all_embeddings.append(batch_emb)

        done_batches = len(all_embeddings)
        done_chunks = min(i + batch_size, len(texts))
        print(f"  Batch {done_batches}/{num_batches} ({done_chunks}/{len(texts)} chunks)")
        if progress_callback:
            progress_callback("embeddings", done_chunks, len(texts))

    embeddings = np.vstack(all_embeddings)

    np.save(embeddings_path, embeddings)
    print(f"  Saved chunk embeddings ({embeddings.shape}) to {embeddings_path}")

    # Invalidate cache
    _chunk_embedding_cache.pop(db_path, None)


class EmbeddingCancelled(Exception):
    """Raised when embedding is cancelled by the user."""
    pass


def _get_chunk_embeddings(db_path: str):
    """Load and cache chunk embeddings array."""
    if db_path not in _chunk_embedding_cache:
        import numpy as np
        embeddings_path = db_path.replace(".db", "_chunk_embeddings.npy")
        if not os.path.exists(embeddings_path):
            return None
        _chunk_embedding_cache[db_path] = np.load(embeddings_path)
    return _chunk_embedding_cache[db_path]


def semantic_search_chunks(db_path: str, queries, top_k: int = 30) -> list:
    """Find chunks semantically similar to the query or queries.

    Uses E5-large with "query: " prefix. Same round-robin merge algorithm
    as semantic_search() but operates on chunk embeddings.

    Returns list of (chunk_id, similarity_score) tuples sorted by
    similarity descending. chunk_id is 1-indexed.
    """
    import numpy as np

    embeddings = _get_chunk_embeddings(db_path)
    if embeddings is None:
        return []

    model = _get_embedding_model()

    if isinstance(queries, str):
        queries = [queries]

    # E5-large requires "query: " prefix
    prefixed_queries = [f"query: {q}" for q in queries]
    query_embeddings = model.encode(prefixed_queries, normalize_embeddings=True)

    all_similarities = query_embeddings @ embeddings.T

    sorted_indices = []
    for qi in range(len(queries)):
        order = np.argsort(all_similarities[qi])[::-1]
        sorted_indices.append(order)

    # Round-robin merge
    candidates = {}
    seen = set()
    rank_ptr = [0] * len(queries)

    while len(candidates) < top_k:
        added_this_round = False
        for qi in range(len(queries)):
            while rank_ptr[qi] < len(sorted_indices[qi]):
                idx = int(sorted_indices[qi][rank_ptr[qi]])
                rank_ptr[qi] += 1
                score = float(all_similarities[qi][idx])
                if score <= 0.15:
                    break
                chunk_id = idx + 1
                if chunk_id not in seen:
                    seen.add(chunk_id)
                    candidates[chunk_id] = score
                    added_this_round = True
                    break
                else:
                    candidates[chunk_id] = max(candidates[chunk_id], score)
        if not added_this_round:
            break

    sorted_candidates = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
    return sorted_candidates[:top_k]


def get_chunk_messages(db_path: str, chunk_id: int) -> dict | None:
    """Get chunk metadata and its individual messages.

    Returns dict with chunk info and list of message dicts, or None.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,))
    chunk_row = c.fetchone()
    if not chunk_row:
        conn.close()
        return None

    chunk = dict(chunk_row)

    c.execute(
        "SELECT * FROM messages WHERE id BETWEEN ? AND ? ORDER BY id",
        (chunk["start_message_id"], chunk["end_message_id"]),
    )
    messages = [dict(row) for row in c.fetchall()]
    conn.close()

    chunk["messages"] = messages
    return chunk


def search_chunks(db_path: str, query: str, page: int = 1, per_page: int = 50) -> tuple:
    """FTS5 search on chunks_fts. Returns (results, total_count)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    use_fts = len(query) >= 3

    if use_fts:
        safe_query = query.replace('"', '""')
        fts_match = f'"{safe_query}"'

        c.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
            (fts_match,),
        )
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        c.execute(
            "SELECT c.* FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
            "WHERE chunks_fts MATCH ? LIMIT ? OFFSET ?",
            (fts_match, per_page, offset),
        )
    else:
        like_pattern = f"%{query}%"
        c.execute(
            "SELECT COUNT(*) FROM chunks WHERE combined_text LIKE ?",
            (like_pattern,),
        )
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        c.execute(
            "SELECT * FROM chunks WHERE combined_text LIKE ? LIMIT ? OFFSET ?",
            (like_pattern, per_page, offset),
        )

    results = [dict(row) for row in c.fetchall()]
    conn.close()
    return results, total
