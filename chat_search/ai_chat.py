"""RAG-based AI chat for WhatsApp conversation analysis."""

import os
import re
import sqlite3

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from . import indexer

# Hebrew stop words - common words to filter out from search queries
HEBREW_STOP_WORDS = {
    # Prepositions
    "של", "על", "את", "אל", "מן", "עם", "בין", "לפני", "אחרי",
    "תחת", "מול", "נגד", "בלי", "למען", "כמו", "כדי", "בשביל",
    "לגבי", "לפי", "בגלל", "למרות", "דרך",
    # Conjunctions
    "או", "אבל", "אם", "כי", "גם", "רק", "עוד",
    "כאשר", "אז", "לכן", "אולם", "אלא", "שגם",
    # Pronouns
    "אני", "אתה", "את", "הוא", "היא", "אנחנו", "אתם", "אתן", "הם", "הן",
    "זה", "זו", "זאת", "אלה", "אלו",
    # Auxiliary / common verbs
    "היה", "היתה", "היו", "יהיה", "יש", "אין", "צריך", "יכול", "רוצה",
    # Possessives / preposition+pronoun
    "לי", "לו", "לה", "לנו", "להם", "להן", "לך",
    "שלי", "שלו", "שלה", "שלנו", "שלהם", "שלך",
    "ממני", "ממך", "ממנו", "ממנה", "מהם",
    "אותי", "אותו", "אותה", "אותנו", "אותם", "אותך",
    # Common fillers
    "הרבה", "קצת", "מאוד", "כל", "איזה", "כבר", "עדיין",
    "פה", "שם", "כאן", "ככה", "כך", "אולי", "בטח", "ממש",
    "עכשיו", "היום", "אתמול", "מחר", "תמיד", "אף", "פעם",
    "בסדר", "טוב", "נכון", "לא", "כן", "בבקשה", "תודה",
    # Single-char prefix particles
    "ה", "ב", "ל", "מ", "כ", "ו", "ש",
    # Question starters (keep for intent, but remove from search)
    "האם",
}

# Hebrew prefix particles to strip for root extraction
HEBREW_PREFIXES = (
    # Multi-char prefixes first (longer = higher priority)
    "וכש", "ובש", "ולכ", "ומש", "וכשה", "שמ", "שה", "שב", "של", "שכ",
    "וה", "וב", "ול", "ומ", "וכ", "ובה", "ולה",
    "בה", "לה", "מה", "כה", "כש",
    # Single-char prefixes
    "ה", "ב", "ל", "מ", "כ", "ו", "ש", "נ", "י", "ת", "א",
)

SYSTEM_PROMPT = """אתה עוזר חכם שמנתח שיחות WhatsApp בעברית.
קיבלת קטעי שיחה רלוונטיים מצ'אט "{chat_name}".

כללים:
- ענה בעברית תמיד
- ענה בקצרה ובתמציתיות
- ציין מספרי הודעות רלוונטיים בסוגריים מרובעים, לדוגמה: [#1234]
- אם אין מידע מספיק בקטעים שניתנו, אמור זאת בכנות
- אל תמציא מידע שלא מופיע בקטעים
- כשהתשובה מבוססת על תיאור תמונה או וידאו, ציין זאת
- [תיאור חזותי] מציין תיאור של תמונה או וידאו שנשלחו בשיחה
- [תמלול וידאו] מציין תמלול של הקול בסרטון וידאו
- [טקסט PDF] מציין תוכן של קובץ PDF שצורף בשיחה"""


def extract_keywords(question: str) -> list[str]:
    """Extract meaningful search keywords from a Hebrew question."""
    # Remove punctuation
    cleaned = re.sub(r'[^\w\s]', ' ', question)
    # Split into tokens
    tokens = cleaned.split()
    # Filter stop words and single-char tokens
    keywords = []
    seen = set()
    for t in tokens:
        t_lower = t.strip()
        if len(t_lower) < 2:
            continue
        if t_lower in HEBREW_STOP_WORDS:
            continue
        if t_lower not in seen:
            seen.add(t_lower)
            keywords.append(t_lower)

    # Fallback: if too few keywords, use longest original tokens
    if len(keywords) < 2:
        all_tokens = [t for t in tokens if len(t) >= 2]
        all_tokens.sort(key=len, reverse=True)
        for t in all_tokens:
            if t not in seen:
                seen.add(t)
                keywords.append(t)
            if len(keywords) >= 2:
                break

    return keywords


def strip_hebrew_prefix(word: str) -> str:
    """Strip common Hebrew prefix particles from a word."""
    for prefix in sorted(HEBREW_PREFIXES, key=len, reverse=True):
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            return word[len(prefix):]
    return word


# Hebrew suffixes to strip for root extraction (plural, gender, tense endings)
HEBREW_SUFFIXES = (
    # Longest first
    "ויות", "יות",  # abstract noun plural (e.g., אפשרויות)
    "ים", "ות",     # masculine/feminine plural (מנופים→מנוף, מנורות→מנור)
    "ון", "ית",     # diminutive / feminine (e.g., שולחנית)
)

# Hebrew non-final → final letter mapping (sofiot)
HEBREW_SOFIT_MAP = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}


def strip_hebrew_suffix(word: str) -> str:
    """Strip common Hebrew suffix particles from a word, fixing final letters."""
    for suffix in HEBREW_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            stripped = word[:-len(suffix)]
            # Fix sofit: non-final letter at end → final form (e.g., מנופ→מנוף)
            if stripped and stripped[-1] in HEBREW_SOFIT_MAP:
                stripped = stripped[:-1] + HEBREW_SOFIT_MAP[stripped[-1]]
            return stripped
    return word


def _like_search(db_path: str, query: str, limit: int = 50) -> list[dict]:
    """Direct LIKE search on messages table - catches things FTS5 trigram may miss."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    like_pattern = f"%{query}%"
    c.execute(
        "SELECT id FROM messages WHERE text LIKE ? OR transcription LIKE ? "
        "OR visual_description LIKE ? OR video_transcription LIKE ? "
        "OR pdf_text LIKE ? OR sender LIKE ? LIMIT ?",
        (like_pattern, like_pattern, like_pattern, like_pattern, like_pattern, like_pattern, limit),
    )
    results = [dict(row) for row in c.fetchall()]
    conn.close()
    return results


def _expand_keywords(keywords: list[str]) -> list[dict]:
    """Expand keywords with root forms by stripping Hebrew prefixes and suffixes.

    Returns list of {original, root, variants} for each keyword.
    Variants is a set of all unique forms to search for.
    """
    expanded = []
    for kw in keywords:
        variants = {kw}

        # Strip prefix: המנופים → מנופים
        prefix_stripped = strip_hebrew_prefix(kw)
        if prefix_stripped != kw and len(prefix_stripped) >= 2:
            variants.add(prefix_stripped)

        # Strip suffix: מנופים → מנוף
        suffix_stripped = strip_hebrew_suffix(kw)
        if suffix_stripped != kw and len(suffix_stripped) >= 2:
            variants.add(suffix_stripped)

        # Strip both prefix+suffix: המנופים → ה→מנופים → מנוף
        if prefix_stripped != kw:
            both_stripped = strip_hebrew_suffix(prefix_stripped)
            if both_stripped != prefix_stripped and len(both_stripped) >= 2:
                variants.add(both_stripped)

        # Pick the shortest form as the "root"
        root = min(variants, key=len)

        expanded.append({
            "original": kw,
            "root": root,
            "variants": variants,
            "is_root_different": root != kw,
        })
    return expanded


def retrieve_chunks(db_path: str, question: str, max_results: int = 12) -> list[dict]:
    """Retrieve relevant conversation chunks using hybrid search.

    Primary: Semantic vector similarity on chunk embeddings (E5-large)
    Secondary: Keyword search mapped to chunks

    Tiers:
      1. Semantic search on chunks (sim*20 weight)
      2. FTS5 on chunks_fts (4 pts)
      3. FTS5 per-keyword on messages_fts → map to chunks (1-2 pts)
      4. LIKE on messages → map to chunks (2 pts)
      5. Intersection boost (+3/+6 pts)

    Returns list of {chunk_id, chunk_data} dicts.
    """
    keywords = extract_keywords(question)

    scored = {}  # chunk_id -> score
    chunk_roots = {}  # chunk_id -> set of root indices

    def add_chunk_score(cid, score, root_idx=None):
        scored[cid] = scored.get(cid, 0) + score
        if root_idx is not None:
            if cid not in chunk_roots:
                chunk_roots[cid] = set()
            chunk_roots[cid].add(root_idx)

    # Load chunk ranges for message-to-chunk mapping
    chunk_ranges = _load_chunk_ranges(db_path)

    def msg_ids_to_chunk_ids(msg_ids):
        """Map message IDs to containing chunk IDs."""
        cids = set()
        for mid in msg_ids:
            for cid, start, end in chunk_ranges:
                if start <= mid <= end:
                    cids.add(cid)
        return cids

    # === TIER 1: Semantic search on chunks ===
    try:
        semantic_queries = [question]
        if keywords:
            expanded = _expand_keywords(keywords)
            kw_forms = []
            for exp in expanded:
                forms = {exp["original"]}
                if exp["root"] != exp["original"] and len(exp["root"]) >= 2:
                    forms.add(exp["root"])
                kw_forms.append(forms)

            for i in range(len(keywords) - 1):
                for fi in kw_forms[i]:
                    for fj in kw_forms[i + 1]:
                        semantic_queries.append(f"{fi} {fj}")

            for i in range(len(keywords) - 2):
                for fi in kw_forms[i]:
                    for fj in kw_forms[i + 1]:
                        for fk in kw_forms[i + 2]:
                            semantic_queries.append(f"{fi} {fj} {fk}")

        semantic_results = indexer.semantic_search_chunks(db_path, semantic_queries, top_k=100)
        for chunk_id, sim_score in semantic_results:
            weight = round(sim_score * 20, 1)
            add_chunk_score(chunk_id, weight)
    except Exception:
        pass

    # === TIER 2-5: Keyword search ===
    if not keywords:
        if not scored:
            return []
    else:
        expanded = _expand_keywords(keywords)

        # --- Tier 2: FTS5 on chunks_fts ---
        combined = " ".join(keywords)
        try:
            results, _ = indexer.search_chunks(db_path, combined, page=1, per_page=50)
            for r in results:
                add_chunk_score(r["id"], 4)
        except Exception:
            pass

        # --- Tier 3: FTS5 per-keyword on messages_fts → map to chunks ---
        for i, exp in enumerate(expanded):
            weight = 2 if len(exp["original"]) >= 4 else 1
            searched = set()
            for variant in exp["variants"]:
                if variant in searched or len(variant) < 2:
                    continue
                searched.add(variant)
                try:
                    results, _ = indexer.search(db_path, variant, page=1, per_page=50)
                    msg_ids = [r["id"] for r in results]
                    for cid in msg_ids_to_chunk_ids(msg_ids):
                        add_chunk_score(cid, weight, i)
                except Exception:
                    pass

        # --- Tier 4: LIKE on messages → map to chunks ---
        for i, exp in enumerate(expanded):
            searched = set()
            for variant in exp["variants"]:
                if variant in searched or len(variant) < 2:
                    continue
                searched.add(variant)
                try:
                    results = _like_search(db_path, variant, limit=80)
                    msg_ids = [r["id"] for r in results]
                    for cid in msg_ids_to_chunk_ids(msg_ids):
                        add_chunk_score(cid, 2, i)
                except Exception:
                    pass

        # --- Tier 5: Intersection boost ---
        for cid, roots in chunk_roots.items():
            if len(roots) >= 3:
                scored[cid] += 6
            elif len(roots) >= 2:
                scored[cid] += 3

    if not scored:
        return []

    # === Thread boost for group chats ===
    # When a chunk matches well, boost other chunks from the same thread
    chunk_threads = _load_chunk_threads(db_path)
    top_scored = sorted(scored.items(), key=lambda x: x[1], reverse=True)[:20]
    boosted_threads = set()
    for cid, score in top_scored:
        if score >= 5:
            tid = chunk_threads.get(cid)
            if tid and tid > 0:
                boosted_threads.add(tid)

    for cid, tid in chunk_threads.items():
        if tid in boosted_threads and cid not in scored:
            scored[cid] = 2  # base thread boost
        elif tid in boosted_threads and cid in scored:
            scored[cid] += 2  # additional thread boost

    # Sort by score descending
    sorted_ids = sorted(scored.keys(), key=lambda cid: scored[cid], reverse=True)

    # Deduplicate: skip chunks with >30% message range overlap with already-selected
    selected = []
    selected_ranges = []  # list of (start, end) tuples

    for cid in sorted_ids:
        if len(selected) >= max_results:
            break

        chunk_data = indexer.get_chunk_messages(db_path, cid)
        if not chunk_data:
            continue

        start = chunk_data["start_message_id"]
        end = chunk_data["end_message_id"]
        span = end - start + 1

        # Check overlap with already-selected chunks
        dominated = False
        for sel_start, sel_end in selected_ranges:
            overlap_start = max(start, sel_start)
            overlap_end = min(end, sel_end)
            if overlap_start <= overlap_end:
                overlap_size = overlap_end - overlap_start + 1
                if overlap_size / span > 0.3:
                    dominated = True
                    break

        if dominated:
            continue

        selected.append({
            "chunk_id": cid,
            "chunk_data": chunk_data,
            "score": scored[cid],
        })
        selected_ranges.append((start, end))

    # Sort selected by thread_id then datetime for coherent context (group chats)
    # Chunks from the same thread should appear together
    selected.sort(key=lambda g: (
        g["chunk_data"].get("thread_id") or 0,
        g["chunk_data"].get("start_datetime", ""),
    ))

    return selected


def _load_chunk_ranges(db_path: str) -> list[tuple]:
    """Load all chunk (id, start_message_id, end_message_id) for mapping."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("SELECT id, start_message_id, end_message_id FROM chunks")
        ranges = c.fetchall()
    except Exception:
        ranges = []
    conn.close()
    return ranges


def _load_chunk_threads(db_path: str) -> dict:
    """Load chunk_id -> thread_id mapping for thread boosting."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("SELECT id, thread_id FROM chunks WHERE thread_id IS NOT NULL")
        result = {row[0]: row[1] for row in c.fetchall()}
    except Exception:
        result = {}
    conn.close()
    return result


def format_chunks_for_prompt(chunk_groups: list[dict], chat_name: str) -> str:
    """Format retrieved chunks into text for the LLM prompt.

    Groups chunks by thread_id for group chats so the LLM sees coherent
    conversation threads rather than interleaved chaos.
    """
    if not chunk_groups:
        return "(לא נמצאו הודעות רלוונטיות)"

    # Group by thread_id if available
    thread_groups = {}
    no_thread = []
    for group in chunk_groups:
        tid = group["chunk_data"].get("thread_id")
        if tid and tid > 0:
            if tid not in thread_groups:
                thread_groups[tid] = []
            thread_groups[tid].append(group)
        else:
            no_thread.append(group)

    parts = []
    section_num = 1

    # Format non-threaded chunks first
    for group in no_thread:
        text = _format_single_chunk(group, section_num)
        if text:
            parts.append(text)
            section_num += 1

    # Format threaded chunks grouped together
    for tid, groups in sorted(thread_groups.items()):
        participants = groups[0]["chunk_data"].get("thread_participants", "")
        header = f"--- שרשור #{tid}"
        if participants:
            header += f" (משתתפים: {participants})"
        header += " ---"

        thread_parts = []
        for group in groups:
            text = _format_single_chunk(group, section_num, indent="  ")
            if text:
                thread_parts.append(text)
                section_num += 1

        if thread_parts:
            parts.append(header + "\n" + "\n".join(thread_parts))

    return "\n\n".join(parts)


def _format_single_chunk(group: dict, section_num: int, indent: str = "") -> str:
    """Format a single chunk group into text."""
    chunk_data = group["chunk_data"]
    messages = chunk_data.get("messages", [])

    lines = []
    for m in messages:
        text = m.get("text") or ""
        transcription = m.get("transcription") or ""
        visual_desc = m.get("visual_description") or ""
        video_trans = m.get("video_transcription") or ""
        pdf_text = m.get("pdf_text") or ""

        if not text and not transcription and not visual_desc and not video_trans and not pdf_text:
            continue

        content_parts = []
        if text:
            content_parts.append(text[:500])
        if transcription:
            content_parts.append(f"[תמלול: {transcription[:300]}]")
        if visual_desc:
            content_parts.append(f"[תיאור חזותי: {visual_desc[:300]}]")
        if video_trans:
            content_parts.append(f"[תמלול וידאו: {video_trans[:300]}]")
        if pdf_text:
            content_parts.append(f"[טקסט PDF: {pdf_text[:500]}]")

        content = " ".join(content_parts)
        dt = m.get("datetime", "")[:16]
        sender = m.get("sender", "?")

        lines.append(f"{indent}    [{dt}] {sender} (#{m['id']}): {content}")

    if lines:
        return f"{indent}--- קטע #{section_num} ---\n" + "\n".join(lines)
    return ""


# Legacy: message-level retrieval (kept for reference)
def retrieve_messages(db_path: str, question: str, max_results: int = 25) -> list[dict]:
    """Legacy: Retrieve relevant messages using semantic-first hybrid search."""
    keywords = extract_keywords(question)

    # Collect scored results from multiple search strategies
    scored = {}  # message_id -> score
    # Track which root concepts each message matches (for intersection boost)
    message_roots = {}  # message_id -> set of root indices

    def add_score(msg_id, score, root_idx=None):
        scored[msg_id] = scored.get(msg_id, 0) + score
        if root_idx is not None:
            if msg_id not in message_roots:
                message_roots[msg_id] = set()
            message_roots[msg_id].add(root_idx)

    # === PRIMARY: Multi-query semantic search (vector similarity) ===
    # Search with the full question AND keyword sub-phrases.
    # Each sub-query independently contributes its top-k candidates, then merged.
    # Uses both original keyword forms and morphological variants to catch
    # different verb conjugations and noun forms in short WhatsApp messages.
    try:
        semantic_queries = [question]
        if keywords:
            expanded = _expand_keywords(keywords)
            # For each keyword: original form + root (most-stripped) form
            kw_forms = []
            for exp in expanded:
                forms = {exp["original"]}
                if exp["root"] != exp["original"] and len(exp["root"]) >= 2:
                    forms.add(exp["root"])
                kw_forms.append(forms)

            # Generate adjacent pairs with variant substitution
            for i in range(len(keywords) - 1):
                for fi in kw_forms[i]:
                    for fj in kw_forms[i + 1]:
                        semantic_queries.append(f"{fi} {fj}")

            # Generate adjacent triplets with variant substitution
            for i in range(len(keywords) - 2):
                for fi in kw_forms[i]:
                    for fj in kw_forms[i + 1]:
                        for fk in kw_forms[i + 2]:
                            semantic_queries.append(f"{fi} {fj} {fk}")

        semantic_results = indexer.semantic_search(db_path, semantic_queries, top_k=200)
        for msg_id, sim_score in semantic_results:
            # High weight: top semantic matches get 10-15 points
            weight = round(sim_score * 20, 1)
            add_score(msg_id, weight)
    except Exception:
        pass  # Graceful degradation if embeddings not available

    # === SECONDARY: Keyword search (precision boost) ===
    # Keyword matches boost messages that also contain exact terms
    if not keywords:
        if not scored:
            return []
    else:
        expanded = _expand_keywords(keywords)

        # --- Keyword Strategy 1: FTS5 with all keywords combined ---
        combined = " ".join(keywords)
        try:
            results, _ = indexer.search(db_path, combined, page=1, per_page=50)
            for r in results:
                add_score(r["id"], 4)
        except Exception:
            pass

        # --- Keyword Strategy 2: FTS5 per keyword + morphological variants ---
        for i, exp in enumerate(expanded):
            weight = 2 if len(exp["original"]) >= 4 else 1
            searched = set()
            for variant in exp["variants"]:
                if variant in searched or len(variant) < 2:
                    continue
                searched.add(variant)
                try:
                    results, _ = indexer.search(db_path, variant, page=1, per_page=50)
                    for r in results:
                        add_score(r["id"], weight, i)
                except Exception:
                    pass

        # --- Keyword Strategy 3: LIKE search with all morphological variants ---
        for i, exp in enumerate(expanded):
            searched = set()
            for variant in exp["variants"]:
                if variant in searched or len(variant) < 2:
                    continue
                searched.add(variant)
                try:
                    results = _like_search(db_path, variant, limit=80)
                    for r in results:
                        add_score(r["id"], 2, i)
                except Exception:
                    pass

        # --- Keyword Strategy 4: Intersection boost ---
        for msg_id, roots in message_roots.items():
            if len(roots) >= 3:
                scored[msg_id] += 6
            elif len(roots) >= 2:
                scored[msg_id] += 3

    if not scored:
        return []

    # Sort by score descending, take top results
    sorted_ids = sorted(scored.keys(), key=lambda mid: scored[mid], reverse=True)
    top_ids = sorted_ids[:max_results]

    # Deduplicate overlapping context windows (wider window = wider gap)
    selected_ids = []
    for mid in top_ids:
        if any(abs(mid - s) <= 10 for s in selected_ids):
            continue
        selected_ids.append(mid)

    # Fetch context for each selected message (wider window: ±5)
    groups = []
    for mid in selected_ids:
        messages = indexer.get_context(db_path, mid, before=5, after=5)
        groups.append({
            "focus_id": mid,
            "messages": messages,
        })

    return groups


def format_messages_for_prompt(message_groups: list[dict], chat_name: str) -> str:
    """Format retrieved message groups into compact text for the LLM."""
    if not message_groups:
        return "(לא נמצאו הודעות רלוונטיות)"

    parts = []
    for i, group in enumerate(message_groups, 1):
        lines = []
        for m in group["messages"]:
            text = m.get("text") or ""
            transcription = m.get("transcription") or ""
            visual_desc = m.get("visual_description") or ""
            video_trans = m.get("video_transcription") or ""
            pdf_text = m.get("pdf_text") or ""

            # Skip truly empty messages
            if not text and not transcription and not visual_desc and not video_trans and not pdf_text:
                continue

            # Build content parts
            content_parts = []
            if text:
                content_parts.append(text[:500])
            if transcription:
                content_parts.append(f"[תמלול: {transcription[:300]}]")
            if visual_desc:
                content_parts.append(f"[תיאור חזותי: {visual_desc[:300]}]")
            if video_trans:
                content_parts.append(f"[תמלול וידאו: {video_trans[:300]}]")
            if pdf_text:
                content_parts.append(f"[טקסט PDF: {pdf_text[:500]}]")

            content = " ".join(content_parts)

            dt = m.get("datetime", "")[:16]  # YYYY-MM-DDTHH:MM
            sender = m.get("sender", "?")
            marker = ">>> " if m["id"] == group["focus_id"] else "    "

            lines.append(f"{marker}[{dt}] {sender} (#{m['id']}): {content}")

        if lines:
            parts.append(f"--- קטע #{i} ---\n" + "\n".join(lines))

    return "\n\n".join(parts)


class LLMClient:
    """Abstraction over Anthropic and OpenAI APIs."""

    def __init__(self):
        self.provider = None
        self.client = None
        self.model = None

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")

        if anthropic_key:
            import anthropic
            self.provider = "anthropic"
            self.client = anthropic.Anthropic(api_key=anthropic_key)
            self.model = "claude-opus-4-6"
        elif openai_key:
            from openai import OpenAI
            self.provider = "openai"
            self.client = OpenAI(api_key=openai_key)
            self.model = "gpt-4o-mini"
        else:
            raise RuntimeError(
                "AI לא מוגדר. הגדירו ANTHROPIC_API_KEY או OPENAI_API_KEY כמשתנה סביבה."
            )

    @staticmethod
    def is_configured() -> bool:
        return bool(
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    @staticmethod
    def get_provider_info() -> dict:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")
        if anthropic_key:
            return {"configured": True, "provider": "anthropic", "model": "claude-opus-4-6"}
        elif openai_key:
            return {"configured": True, "provider": "openai", "model": "gpt-4o-mini"}
        return {"configured": False, "provider": None, "model": None}

    def chat(self, system_prompt: str, user_message: str, max_tokens: int = 1024) -> str:
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content


def ask(db_path: str, question: str, chat_name: str, history: list = None) -> dict:
    """Main RAG pipeline: retrieve relevant chunks, then ask LLM.

    Returns {answer, sources, keywords, provider}.
    """
    # 1. Retrieve relevant conversation chunks
    chunk_groups = retrieve_chunks(db_path, question, max_results=12)

    # 2. Format for prompt
    context_text = format_chunks_for_prompt(chunk_groups, chat_name)

    # 3. Build user message
    user_message = f"קטעי שיחה רלוונטיים:\n{context_text}\n\nשאלה: {question}"

    # 4. Append recent history if available (max 2 exchanges = 4 items)
    if history and len(history) > 0:
        recent = history[-4:]
        history_text = "\n".join(
            f"{'שאלה קודמת' if h['role'] == 'user' else 'תשובה קודמת'}: {h['content'][:200]}"
            for h in recent
        )
        user_message = f"היסטוריית שיחה אחרונה:\n{history_text}\n\n{user_message}"

    # 5. Call LLM
    llm = LLMClient()
    system = SYSTEM_PROMPT.format(chat_name=chat_name)
    answer = llm.chat(system, user_message, max_tokens=2048)

    # 6. Extract source citations from chunks
    sources = []
    seen_ids = set()
    for group in chunk_groups:
        chunk_data = group["chunk_data"]
        messages = chunk_data.get("messages", [])
        # Use first message with text as preview
        for m in messages:
            if m["id"] in seen_ids:
                continue
            preview_text = (m.get("text")
                            or m.get("transcription")
                            or m.get("visual_description")
                            or m.get("pdf_text")
                            or "")
            if preview_text:
                seen_ids.add(m["id"])
                sources.append({
                    "message_id": m["id"],
                    "datetime": m.get("datetime", ""),
                    "sender": m.get("sender", ""),
                    "preview": preview_text[:80],
                })
                break  # One source per chunk

    return {
        "answer": answer,
        "sources": sources,
        "keywords": extract_keywords(question),
        "provider": llm.provider,
    }
