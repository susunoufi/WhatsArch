"""SQLite FTS5 search index builder with semantic embedding support."""

import sqlite3
import os

# Module-level caches for semantic search (loaded once, reused across queries)
_chunk_embedding_cache = {}  # db_path -> numpy array (chunk-level)
_model_cache = {}      # singleton


def build_index_incremental(db_path: str, messages: list) -> bool:
    """Incrementally update the search index. Returns True if incremental update was possible."""
    if not os.path.exists(db_path):
        return False  # Need full build

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()

        # Check existing message count
        try:
            c.execute("SELECT COUNT(*) FROM messages")
            existing_count = c.fetchone()[0]
        except Exception:
            return False  # Table doesn't exist, need full build

        if existing_count == 0:
            return False  # Empty DB, need full build

        if len(messages) <= existing_count:
            if len(messages) == existing_count:
                # Check if enrichment fields changed (new transcriptions, descriptions, etc.)
                c.execute("SELECT COUNT(*) FROM messages WHERE transcription != '' AND transcription IS NOT NULL")
                db_trans = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM messages WHERE visual_description != '' AND visual_description IS NOT NULL")
                db_desc = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM messages WHERE pdf_text != '' AND pdf_text IS NOT NULL")
                db_pdf = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM messages WHERE video_transcription != '' AND video_transcription IS NOT NULL")
                db_vtrans = c.fetchone()[0]

                new_trans = sum(1 for m in messages if m.get("transcription"))
                new_desc = sum(1 for m in messages if m.get("visual_description"))
                new_pdf = sum(1 for m in messages if m.get("pdf_text"))
                new_vtrans = sum(1 for m in messages if m.get("video_transcription"))

                if db_trans == new_trans and db_desc == new_desc and db_pdf == new_pdf and db_vtrans == new_vtrans:
                    print(f"  Index up to date ({existing_count} messages)")
                    return True  # No changes
                print(f"  Enrichment changed (trans: {db_trans}->{new_trans}, desc: {db_desc}->{new_desc}, pdf: {db_pdf}->{new_pdf}, vtrans: {db_vtrans}->{new_vtrans}), rebuilding...")
                return False  # Force full rebuild
            return False  # Fewer messages = something changed, full rebuild

        # We have new messages to add
        new_messages = messages[existing_count:]
        new_count = len(new_messages)
        print(f"  Adding {new_count} new messages (total: {len(messages)}, existing: {existing_count})")

        # Insert new messages
        for i, msg in enumerate(new_messages, start=existing_count + 1):
            c.execute(
                "INSERT INTO messages (id, datetime, sender, text, attachment, media_type, transcription, visual_description, video_transcription, pdf_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (i, msg.get("datetime", ""), msg.get("sender", ""), msg.get("text", ""),
                 msg.get("attachment", ""), msg.get("media_type", ""),
                 msg.get("transcription", ""), msg.get("visual_description", ""),
                 msg.get("video_transcription", ""), msg.get("pdf_text", ""))
            )
            # Update FTS5
            c.execute(
                "INSERT INTO messages_fts (rowid, text, transcription, sender, visual_description, video_transcription, pdf_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (i, msg.get("text", ""), msg.get("transcription", ""), msg.get("sender", ""),
                 msg.get("visual_description", ""), msg.get("video_transcription", ""), msg.get("pdf_text", ""))
            )

        conn.commit()
        print(f"  Incrementally indexed {new_count} new messages")
        return True
    except Exception as e:
        print(f"  Incremental index failed: {e}, will do full rebuild")
        return False
    finally:
        conn.close()


def build_index(messages: list, db_path: str):
    """Build (or incrementally update) the FTS5 search index."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Try incremental update first
    if build_index_incremental(db_path, messages):
        return

    # Full rebuild
    # Remove old DB to rebuild fresh
    if os.path.exists(db_path):
        os.remove(db_path)
        # Also remove stale chunk embeddings to prevent mismatch
        # (chunks table will be empty until build_chunks is called)
        emb_path = db_path.replace(".db", "_chunk_embeddings.npy")
        if os.path.exists(emb_path):
            os.remove(emb_path)
            _chunk_embedding_cache.pop(db_path, None)

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
    try:
        c = conn.cursor()
        for key, value in metadata.items():
            c.execute(
                "INSERT OR REPLACE INTO chat_metadata (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        conn.commit()
    finally:
        conn.close()


def get_chat_metadata(db_path: str) -> dict:
    """Load chat metadata as a dict."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT key, value FROM chat_metadata")
        return {row[0]: row[1] for row in c.fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def search(db_path: str, query: str, sender: str = "", date_from: str = "", date_to: str = "", page: int = 1, per_page: int = 50, search_type: str = "all"):
    """Search messages using FTS5. Returns (results, total_count).
    search_type: 'all' (text+transcription), 'text' (text only), 'transcription' (transcription only)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
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
                elif st == "image":
                    type_conds.append("(m.media_type = 'image' AND m.visual_description IS NOT NULL AND m.visual_description != '')")
                elif st == "video":
                    type_conds.append("(m.media_type = 'video')")
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
            import re as _re
            safe_query = query.replace('"', '""')
            # Strip FTS5 special operators/characters to prevent syntax errors
            safe_query = _re.sub(r'\b(NOT|OR|AND|NEAR)\b', '', safe_query)
            safe_query = safe_query.replace('*', '').replace('^', '')
            safe_query = safe_query.strip()
            if not safe_query or len(safe_query) < 3:
                # After stripping, query is too short for FTS5 trigram; fall back to LIKE
                use_fts = False

        if use_fts:
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
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.rowid
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
                    "image": "m.visual_description",
                    "video": "m.visual_description",
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

        # Add metadata flags to each result
        for r in results:
            r["has_transcription"] = bool(r.get("transcription"))
            r["has_visual"] = bool(r.get("visual_description"))
            r["has_video_transcription"] = bool(r.get("video_transcription"))
            r["has_pdf"] = bool(r.get("pdf_text"))
            r["media_type"] = r.get("media_type", "")
            r["relevance_score"] = r.get("relevance_score", 0)

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

        return results, total
    finally:
        conn.close()


def search_filtered(db_path: str, sender: str = "", date_from: str = "", date_to: str = "", page: int = 1, per_page: int = 50, search_type: str = "all"):
    """Search messages by filters only (no text query). Returns (results, total_count)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
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
                elif st == "image":
                    type_conds.append("(m.media_type = 'image' AND m.visual_description IS NOT NULL AND m.visual_description != '')")
                elif st == "video":
                    type_conds.append("(m.media_type = 'video')")
                elif st == "pdf":
                    type_conds.append("(m.pdf_text IS NOT NULL AND m.pdf_text != '')")
            if type_conds:
                conditions.append("(" + " OR ".join(type_conds) + ")")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count_sql = f"SELECT COUNT(*) FROM messages m {where}"
        c.execute(count_sql, params)
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        results_sql = f"""
            SELECT m.id, m.datetime, m.sender, m.text, m.attachment, m.media_type,
                   m.transcription, m.visual_description, m.video_transcription, m.pdf_text,
                   '' as text_snippet, '' as transcription_snippet,
                   '' as visual_description_snippet, '' as video_transcription_snippet,
                   '' as pdf_text_snippet
            FROM messages m
            {where}
            ORDER BY m.datetime DESC
            LIMIT ? OFFSET ?
        """
        c.execute(results_sql, params + [per_page, offset])

        results = [dict(row) for row in c.fetchall()]
        for r in results:
            r["has_transcription"] = bool(r.get("transcription"))
            r["has_visual"] = bool(r.get("visual_description"))
            r["has_video_transcription"] = bool(r.get("video_transcription"))
            r["has_pdf"] = bool(r.get("pdf_text"))
            r["media_type"] = r.get("media_type", "")

        return results, total
    finally:
        conn.close()


def browse_enriched(db_path: str, per_category: int = 10):
    """Return a sample of enriched messages from each category (transcriptions, descriptions, etc.)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        categories = [
            ("transcription", "m.transcription IS NOT NULL AND m.transcription != ''", "transcription"),
            ("visual_description", "m.visual_description IS NOT NULL AND m.visual_description != '' AND m.media_type = 'image'", "image"),
            ("visual_description", "m.visual_description IS NOT NULL AND m.visual_description != '' AND m.media_type = 'video'", "video"),
            ("video_transcription", "m.video_transcription IS NOT NULL AND m.video_transcription != ''", "video_transcription"),
            ("pdf_text", "m.pdf_text IS NOT NULL AND m.pdf_text != ''", "pdf"),
        ]
        results = []
        seen_ids = set()
        for field, condition, category in categories:
            sql = f"""
                SELECT m.id, m.datetime, m.sender, m.text, m.attachment, m.media_type,
                       m.transcription, m.visual_description, m.video_transcription, m.pdf_text
                FROM messages m
                WHERE {condition}
                ORDER BY m.datetime DESC
                LIMIT ?
            """
            c.execute(sql, [per_category])
            for row in c.fetchall():
                if row["id"] in seen_ids:
                    continue
                seen_ids.add(row["id"])
                r = dict(row)
                r["_category"] = category
                # Add snippet from the enriched field
                val = r.get(field, "")
                if val:
                    snippet = val[:200] + ("..." if len(val) > 200 else "")
                    r[f"{field}_snippet"] = snippet
                results.append(r)
        total = len(results)
        # Sort by datetime desc
        results.sort(key=lambda x: x.get("datetime", ""), reverse=True)
        return results, total
    finally:
        conn.close()


def get_context(db_path: str, message_id: int, before: int = 5, after: int = 5):
    """Get surrounding messages for context."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM messages WHERE id BETWEEN ? AND ? ORDER BY id",
            (message_id - before, message_id + after),
        )
        return [dict(row) for row in c.fetchall()]
    finally:
        conn.close()


def get_stats(db_path: str) -> dict:
    """Get chat statistics."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()

        # Single query for all aggregate counts
        c.execute("""
            SELECT
                COUNT(*),
                MIN(datetime), MAX(datetime),
                SUM(CASE WHEN media_type = 'audio' THEN 1 ELSE 0 END),
                SUM(CASE WHEN transcription != '' AND transcription IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN media_type = 'image' THEN 1 ELSE 0 END),
                SUM(CASE WHEN media_type = 'video' THEN 1 ELSE 0 END),
                SUM(CASE WHEN media_type = 'pdf' THEN 1 ELSE 0 END),
                SUM(CASE WHEN visual_description != '' AND visual_description IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN pdf_text != '' AND pdf_text IS NOT NULL THEN 1 ELSE 0 END)
            FROM messages
        """)
        row = c.fetchone()
        total, date_min, date_max, audio_count, transcribed_count, \
            image_count, video_count, pdf_count, described_count, pdf_extracted_count = row

        c.execute("SELECT sender, COUNT(*) FROM messages GROUP BY sender")
        senders = {r[0]: r[1] for r in c.fetchall()}

        return {
            "total_messages": total,
            "date_range": {"from": date_min, "to": date_max},
            "senders": senders,
            "audio_messages": audio_count or 0,
            "transcribed_messages": transcribed_count or 0,
            "image_messages": image_count or 0,
            "video_messages": video_count or 0,
            "pdf_messages": pdf_count or 0,
            "described_media": described_count or 0,
            "pdf_extracted": pdf_extracted_count or 0,
        }
    finally:
        conn.close()


# --- Semantic embedding support ---

EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-large"


def _get_embedding_model():
    """Lazy-load and cache the sentence transformer model."""
    if "model" not in _model_cache:
        from sentence_transformers import SentenceTransformer
        _model_cache["model"] = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model_cache["model"]


# --- Chunk support ---


def build_chunks(chunks: list, db_path: str):
    """Insert chunk records into the chunks table and populate chunks_fts.

    Args:
        chunks: List of Chunk dataclass instances from chunker.py.
        db_path: Path to the SQLite database.
    """
    conn = sqlite3.connect(db_path)
    try:
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
    finally:
        conn.close()
    print(f"  Inserted {len(chunks)} chunks into {db_path}")


def load_chunks_from_db(db_path: str) -> list:
    """Load existing chunks from the database for embedding reuse.

    Returns a list of simple objects with .combined_text attribute,
    ordered by chunk id. Returns empty list if no chunks exist.
    """
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT id, combined_text FROM chunks ORDER BY id")
        rows = c.fetchall()
        conn.close()
    except Exception:
        return []
    if not rows:
        return []

    class _ChunkStub:
        __slots__ = ("chunk_id", "combined_text")
        def __init__(self, chunk_id, combined_text):
            self.chunk_id = chunk_id
            self.combined_text = combined_text

    return [_ChunkStub(row[0], row[1]) for row in rows]


def build_chunk_embeddings(chunks: list, db_path: str, cancel_event=None, progress_callback=None,
                           provider: str = "local", api_key: str = None):
    """Build semantic embeddings for conversation chunks.

    Providers: "local" (E5-large), "openai" (text-embedding-3-small), "gemini" (text-embedding-004).
    Saves as chat_chunk_embeddings.npy.
    """
    import numpy as np

    embeddings_path = db_path.replace(".db", "_chunk_embeddings.npy")
    total = len(chunks)

    # Check for existing partial/complete embeddings to enable resume
    start_idx = 0
    existing_embeddings = None
    if os.path.exists(embeddings_path):
        try:
            existing = np.load(embeddings_path)
            if existing.shape[0] >= total:
                if existing.shape[0] == total:
                    print(f"  Chunk embeddings already complete ({total} chunks), skipping.")
                    if progress_callback:
                        progress_callback("embeddings", total, total)
                    return
                # More rows than chunks = stale file, rebuild
                print(f"  Stale embeddings ({existing.shape[0]} rows vs {total} chunks), rebuilding...")
            else:
                # Partial file - resume from where we left off
                start_idx = existing.shape[0]
                existing_embeddings = existing
                print(f"  Resuming embeddings from chunk {start_idx}/{total}")
        except Exception:
            print("  Corrupted embeddings file, rebuilding...")

    texts = [chunk.combined_text for chunk in chunks]

    # Choose provider
    if provider == "openai" and api_key:
        print(f"  Using OpenAI embeddings (text-embedding-3-small) for {total} chunks...")
        combined = _embed_openai(texts, api_key, start_idx, total, cancel_event, progress_callback,
                                 embeddings_path=embeddings_path, existing_embeddings=existing_embeddings)
    elif provider == "gemini" and api_key:
        print(f"  Using Gemini embeddings (text-embedding-004) for {total} chunks...")
        combined = _embed_gemini(texts, api_key, start_idx, total, cancel_event, progress_callback,
                                 embeddings_path=embeddings_path, existing_embeddings=existing_embeddings)
    else:
        # Local E5-large
        prefixed = [f"passage: {t}" for t in texts]
        model = _get_embedding_model()
        batch_size = 32
        remaining = total - start_idx
        num_batches = (remaining + batch_size - 1) // batch_size
        print(f"  Encoding {remaining} chunks ({num_batches} batches) with {EMBEDDING_MODEL_NAME} (local)..." +
              (f" (resuming from {start_idx})" if start_idx > 0 else ""))

        all_emb = [existing_embeddings] if existing_embeddings is not None else []
        if progress_callback and start_idx > 0:
            progress_callback("embeddings", start_idx, total)

        for i in range(start_idx, total, batch_size):
            if cancel_event and cancel_event.is_set():
                raise EmbeddingCancelled()
            batch = prefixed[i:i + batch_size]
            batch_emb = model.encode(batch, batch_size=batch_size, show_progress_bar=False, normalize_embeddings=True)
            all_emb.append(batch_emb)
            combined = np.vstack(all_emb)
            np.save(embeddings_path, combined)
            done = min(i + batch_size, total)
            print(f"  Batch {(i - start_idx) // batch_size + 1}/{num_batches} ({done}/{total})")
            if progress_callback:
                progress_callback("embeddings", done, total)

        combined = np.vstack(all_emb) if all_emb else np.array([])

    np.save(embeddings_path, combined)
    cache_key = os.path.normcase(os.path.abspath(db_path))
    _chunk_embedding_cache.pop(cache_key, None)
    # Store which provider was used so queries use the same one
    save_chat_metadata(db_path, {"embeddings_provider": provider})
    print(f"  Saved chunk embeddings ({combined.shape}) to {embeddings_path} [provider={provider}]")


def _embed_openai(texts, api_key, start_idx, total, cancel_event=None, progress_callback=None,
                   embeddings_path=None, existing_embeddings=None):
    """Embed texts using OpenAI text-embedding-3-small API. Saves incrementally for resume."""
    import numpy as np
    import time as _time
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    batch_size = 100
    all_emb = [existing_embeddings] if existing_embeddings is not None else []
    for i in range(start_idx, total, batch_size):
        if cancel_event and cancel_event.is_set():
            raise EmbeddingCancelled()
        batch = texts[i:i + batch_size]
        # Retry with backoff on rate limit
        for attempt in range(5):
            try:
                resp = client.embeddings.create(model="text-embedding-3-small", input=batch)
                break
            except Exception as e:
                if "rate_limit" in str(e).lower() or "429" in str(e):
                    wait = 2 ** attempt + 1  # 2, 3, 5, 9, 17 seconds
                    print(f"  Rate limit hit, waiting {wait}s...")
                    _time.sleep(wait)
                else:
                    raise
        batch_emb = np.array([d.embedding for d in resp.data], dtype=np.float32)
        all_emb.append(batch_emb)
        # Incremental save for resume support
        if embeddings_path:
            combined = np.vstack(all_emb)
            np.save(embeddings_path, combined)
        done = min(i + batch_size, total)
        print(f"  OpenAI: {done}/{total}")
        if progress_callback:
            progress_callback("embeddings", done, total)
    return np.vstack(all_emb) if all_emb else np.array([], dtype=np.float32)


def _embed_gemini(texts, api_key, start_idx, total, cancel_event=None, progress_callback=None,
                   embeddings_path=None, existing_embeddings=None):
    """Embed texts using Gemini text-embedding-004 API. Saves incrementally for resume."""
    import numpy as np
    import time as _time
    from google import genai
    client = genai.Client(api_key=api_key)
    batch_size = 50
    all_emb = [existing_embeddings] if existing_embeddings is not None else []
    for i in range(start_idx, total, batch_size):
        if cancel_event and cancel_event.is_set():
            raise EmbeddingCancelled()
        batch = texts[i:i + batch_size]
        # Retry with backoff on rate limit
        for attempt in range(5):
            try:
                resp = client.models.embed_content(model="text-embedding-004", contents=batch)
                break
            except Exception as e:
                if "rate" in str(e).lower() or "429" in str(e) or "quota" in str(e).lower():
                    wait = 2 ** attempt + 1
                    print(f"  Rate limit hit, waiting {wait}s...")
                    _time.sleep(wait)
                else:
                    raise
        batch_emb = np.array([[v for v in e.values] for e in resp.embeddings], dtype=np.float32)
        all_emb.append(batch_emb)
        if embeddings_path:
            combined = np.vstack(all_emb)
            np.save(embeddings_path, combined)
        done = min(i + batch_size, total)
        print(f"  Gemini: {done}/{total}")
        if progress_callback:
            progress_callback("embeddings", done, total)
    return np.vstack(all_emb) if all_emb else np.array([], dtype=np.float32)


class EmbeddingCancelled(Exception):
    """Raised when embedding is cancelled by the user."""
    pass


def _get_chunk_embeddings(db_path: str):
    """Load and cache chunk embeddings array."""
    # Normalize path for consistent cache keys (important on Windows)
    cache_key = os.path.normcase(os.path.abspath(db_path))
    if cache_key not in _chunk_embedding_cache:
        import numpy as np
        embeddings_path = db_path.replace(".db", "_chunk_embeddings.npy")
        if not os.path.exists(embeddings_path):
            return None
        _chunk_embedding_cache[cache_key] = np.load(embeddings_path)
    return _chunk_embedding_cache[cache_key]


def semantic_search_chunks(db_path: str, queries, top_k: int = 30) -> list:
    """Find chunks semantically similar to the query or queries.

    Automatically detects which embedding provider was used for chunks
    and uses the same provider for query encoding.

    Returns list of (chunk_id, similarity_score) tuples sorted by
    similarity descending. chunk_id is 1-indexed.
    """
    import numpy as np

    embeddings = _get_chunk_embeddings(db_path)
    if embeddings is None:
        return []

    if isinstance(queries, str):
        queries = [queries]

    # Detect embedding provider from metadata or dimensions
    meta = get_chat_metadata(db_path)
    emb_provider = meta.get("embeddings_provider", "")
    emb_dim = embeddings.shape[1] if len(embeddings.shape) > 1 else 0

    # Auto-detect from dimensions if metadata missing
    if not emb_provider:
        if emb_dim == 1024:
            emb_provider = "local"
        elif emb_dim == 1536:
            emb_provider = "openai"
        elif emb_dim == 768:
            emb_provider = "gemini"
        else:
            emb_provider = "local"

    # Encode queries with the SAME provider
    if emb_provider == "openai":
        try:
            from openai import OpenAI
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if api_key:
                client = OpenAI(api_key=api_key)
                resp = client.embeddings.create(model="text-embedding-3-small", input=queries)
                query_embeddings = np.array([d.embedding for d in resp.data], dtype=np.float32)
            else:
                return []  # Can't search without API key
        except Exception:
            return []
    elif emb_provider == "gemini":
        try:
            from google import genai
            api_key = os.environ.get("GOOGLE_API_KEY", "")
            if api_key:
                client = genai.Client(api_key=api_key)
                resp = client.models.embed_content(model="text-embedding-004", contents=queries)
                query_embeddings = np.array([[v for v in e.values] for e in resp.embeddings], dtype=np.float32)
            else:
                return []
        except Exception:
            return []
    else:
        # Local E5-large
        model = _get_embedding_model()
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
                if score <= 0.05:
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
    try:
        c = conn.cursor()

        c.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,))
        chunk_row = c.fetchone()
        if not chunk_row:
            return None

        chunk = dict(chunk_row)

        c.execute(
            "SELECT * FROM messages WHERE id BETWEEN ? AND ? ORDER BY id",
            (chunk["start_message_id"], chunk["end_message_id"]),
        )
        messages = [dict(row) for row in c.fetchall()]

        chunk["messages"] = messages
        return chunk
    finally:
        conn.close()


def search_chunks(db_path: str, query: str, page: int = 1, per_page: int = 50) -> tuple:
    """FTS5 search on chunks_fts. Returns (results, total_count)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()

        use_fts = len(query) >= 3

        if use_fts:
            import re as _re
            safe_query = query.replace('"', '""')
            # Strip FTS5 special operators/characters to prevent syntax errors
            safe_query = _re.sub(r'\b(NOT|OR|AND|NEAR)\b', '', safe_query)
            safe_query = safe_query.replace('*', '').replace('^', '')
            safe_query = safe_query.strip()
            if not safe_query or len(safe_query) < 3:
                use_fts = False

        if use_fts:
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
        return results, total
    finally:
        conn.close()
