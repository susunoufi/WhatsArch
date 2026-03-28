"""RAG-based AI chat for WhatsApp conversation analysis."""

import bisect
import os
import re
import sqlite3

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from . import config
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

SYSTEM_PROMPT_HE = """אתה עוזר חכם שמנתח שיחות WhatsApp בעברית.
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

SYSTEM_PROMPT_EN = """You are a smart assistant that analyzes chat conversations.
You received relevant conversation segments from chat "{chat_name}".

Rules:
- Always respond in the same language as the conversation
- Be concise and to the point
- Cite relevant message IDs in square brackets, e.g.: [#1234]
- If there is not enough information, say so honestly
- Do not make up information not present in the segments
- When the answer is based on image or video descriptions, mention it
- [visual description] indicates a description of an image or video sent in the conversation
- [video transcription] indicates audio transcription from a video
- [PDF text] indicates content from an attached PDF file"""

SYSTEM_PROMPTS = {
    "he": SYSTEM_PROMPT_HE,
    "ar": SYSTEM_PROMPT_HE.replace("בעברית", "בערבית").replace("ענה בעברית תמיד", "ענה בערבית תמיד"),
    "en": SYSTEM_PROMPT_EN,
}

# Multilingual stop words
STOP_WORDS = {
    "en": {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "is", "was", "are", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "not", "no", "nor", "so",
        "if", "then", "than", "that", "this", "these", "those", "it", "its",
        "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his",
        "she", "her", "they", "them", "their", "what", "which", "who", "whom",
        "when", "where", "why", "how", "all", "each", "every", "both", "few",
        "more", "most", "some", "any", "just", "about", "very", "also", "too",
        "here", "there", "up", "down", "out", "from", "into", "over", "after",
        "before", "between", "under", "again", "once", "yes", "no", "ok", "okay",
    },
    "es": {
        "el", "la", "los", "las", "un", "una", "unos", "unas", "y", "o", "pero",
        "en", "de", "del", "al", "con", "por", "para", "que", "como", "si",
        "no", "más", "muy", "este", "esta", "estos", "estas", "ese", "esa",
        "yo", "tú", "él", "ella", "nosotros", "ellos", "ellas", "me", "te", "se",
        "lo", "le", "nos", "les", "mi", "tu", "su", "es", "son", "fue", "ser",
        "estar", "hay", "tiene", "hace", "puede", "todo", "bien", "sí", "ya",
    },
    "fr": {
        "le", "la", "les", "un", "une", "des", "et", "ou", "mais", "dans",
        "de", "du", "en", "à", "au", "aux", "pour", "par", "sur", "avec",
        "que", "qui", "ne", "pas", "plus", "ce", "cette", "ces", "je", "tu",
        "il", "elle", "nous", "vous", "ils", "elles", "me", "te", "se",
        "mon", "ton", "son", "ma", "ta", "sa", "est", "sont", "été", "être",
        "avoir", "fait", "peut", "tout", "bien", "oui", "non", "très",
    },
    "de": {
        "der", "die", "das", "ein", "eine", "und", "oder", "aber", "in", "im",
        "an", "auf", "von", "zu", "mit", "für", "ist", "sind", "war", "hat",
        "ich", "du", "er", "sie", "es", "wir", "ihr", "mein", "dein", "sein",
        "nicht", "kein", "auch", "noch", "schon", "sehr", "ja", "nein", "gut",
        "den", "dem", "des", "dass", "wenn", "als", "wie", "was", "wer",
    },
    "ru": {
        "и", "в", "не", "на", "я", "он", "она", "мы", "вы", "они", "что",
        "как", "это", "но", "с", "по", "для", "от", "до", "из", "за", "к",
        "у", "бы", "же", "то", "все", "так", "его", "её", "их", "мой", "твой",
        "наш", "ваш", "был", "была", "были", "есть", "нет", "да", "уже",
        "ещё", "тоже", "очень", "тут", "там", "вот", "ну", "ок", "хорошо",
    },
    "pt": {
        "o", "a", "os", "as", "um", "uma", "e", "ou", "mas", "em", "de",
        "do", "da", "no", "na", "com", "por", "para", "que", "como", "se",
        "não", "mais", "este", "esta", "esse", "essa", "eu", "tu", "ele",
        "ela", "nós", "eles", "elas", "me", "te", "se", "meu", "teu", "seu",
        "é", "são", "foi", "ser", "estar", "tem", "há", "pode", "tudo", "bem",
    },
}

# Hebrew stop words remain the default
STOP_WORDS["he"] = HEBREW_STOP_WORDS


def get_system_prompt(chat_name: str, language: str = "he") -> str:
    """Get the system prompt for the given language."""
    if language in SYSTEM_PROMPTS:
        return SYSTEM_PROMPTS[language].format(chat_name=chat_name)
    # Default: English prompt with language instruction
    return SYSTEM_PROMPT_EN.format(chat_name=chat_name).replace(
        "Always respond in the same language as the conversation",
        f"Always respond in the language of the conversation (detected: {language})"
    )


def get_stop_words(language: str = "he") -> set:
    """Get stop words for the given language."""
    return STOP_WORDS.get(language, STOP_WORDS["en"])


def extract_keywords(question: str, language: str = "he") -> list[str]:
    """Extract meaningful search keywords from a question in any language."""
    # Remove punctuation
    cleaned = re.sub(r'[^\w\s]', ' ', question)
    # Split into tokens
    tokens = cleaned.split()
    # Filter stop words and single-char tokens
    stop = get_stop_words(language)
    keywords = []
    seen = set()
    for t in tokens:
        t_lower = t.strip()
        if len(t_lower) < 2:
            continue
        if t_lower in stop or t_lower.lower() in stop:
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
    try:
        c = conn.cursor()
        like_pattern = f"%{query}%"
        c.execute(
            "SELECT id FROM messages WHERE text LIKE ? OR transcription LIKE ? "
            "OR visual_description LIKE ? OR video_transcription LIKE ? "
            "OR pdf_text LIKE ? OR sender LIKE ? LIMIT ?",
            (like_pattern, like_pattern, like_pattern, like_pattern, like_pattern, like_pattern, limit),
        )
        return [dict(row) for row in c.fetchall()]
    finally:
        conn.close()


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


def retrieve_chunks(db_path: str, question: str, max_results: int = 12, language: str = "he") -> list[dict]:
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
    keywords = extract_keywords(question, language)

    scored = {}  # chunk_id -> score
    chunk_roots = {}  # chunk_id -> set of root indices

    def add_chunk_score(cid, score, root_idx=None):
        scored[cid] = scored.get(cid, 0) + score
        if root_idx is not None:
            if cid not in chunk_roots:
                chunk_roots[cid] = set()
            chunk_roots[cid].add(root_idx)

    # Load chunk ranges for message-to-chunk mapping (sorted by start_message_id)
    chunk_ranges = _load_chunk_ranges(db_path)
    # Pre-extract start IDs for bisect lookup
    chunk_starts = [r[1] for r in chunk_ranges]

    def msg_ids_to_chunk_ids(msg_ids):
        """Map message IDs to containing chunk IDs using binary search."""
        cids = set()
        for mid in msg_ids:
            # Find the rightmost chunk whose start_id <= mid
            idx = bisect.bisect_right(chunk_starts, mid) - 1
            # Check this and nearby chunks (overlapping chunks may contain mid)
            for i in range(max(0, idx - 1), min(len(chunk_ranges), idx + 3)):
                cid, start, end = chunk_ranges[i]
                if start <= mid <= end:
                    cids.add(cid)
        return cids

    # === TIER 1: Semantic search on chunks ===
    MAX_SEMANTIC_QUERIES = 15
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

            # Adjacent pairs
            for i in range(len(keywords) - 1):
                if len(semantic_queries) >= MAX_SEMANTIC_QUERIES:
                    break
                for fi in kw_forms[i]:
                    if len(semantic_queries) >= MAX_SEMANTIC_QUERIES:
                        break
                    for fj in kw_forms[i + 1]:
                        if len(semantic_queries) >= MAX_SEMANTIC_QUERIES:
                            break
                        semantic_queries.append(f"{fi} {fj}")

            # Adjacent triplets (only if room remains)
            for i in range(len(keywords) - 2):
                if len(semantic_queries) >= MAX_SEMANTIC_QUERIES:
                    break
                for fi in kw_forms[i]:
                    if len(semantic_queries) >= MAX_SEMANTIC_QUERIES:
                        break
                    for fj in kw_forms[i + 1]:
                        if len(semantic_queries) >= MAX_SEMANTIC_QUERIES:
                            break
                        for fk in kw_forms[i + 2]:
                            if len(semantic_queries) >= MAX_SEMANTIC_QUERIES:
                                break
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
        if score >= 8:
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


_chunk_ranges_cache = {}   # db_path -> list of (id, start, end)
_chunk_threads_cache = {}  # db_path -> dict {chunk_id: thread_id}


def invalidate_caches(chat_name=None):
    """Clear chunk range and thread caches.

    If chat_name is given, only clear entries whose db_path contains that name.
    Otherwise, clear all entries.
    """
    if chat_name is None:
        _chunk_ranges_cache.clear()
        _chunk_threads_cache.clear()
    else:
        to_remove = [k for k in _chunk_ranges_cache if chat_name in k]
        for k in to_remove:
            _chunk_ranges_cache.pop(k, None)
        to_remove = [k for k in _chunk_threads_cache if chat_name in k]
        for k in to_remove:
            _chunk_threads_cache.pop(k, None)


def _load_chunk_ranges(db_path: str) -> list[tuple]:
    """Load all chunk (id, start_message_id, end_message_id) for mapping. Cached."""
    if db_path in _chunk_ranges_cache:
        return _chunk_ranges_cache[db_path]
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT id, start_message_id, end_message_id FROM chunks ORDER BY start_message_id")
        ranges = c.fetchall()
        _chunk_ranges_cache[db_path] = ranges
        return ranges
    except Exception:
        return []
    finally:
        conn.close()


def _load_chunk_threads(db_path: str) -> dict:
    """Load chunk_id -> thread_id mapping for thread boosting. Cached."""
    if db_path in _chunk_threads_cache:
        return _chunk_threads_cache[db_path]
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT id, thread_id FROM chunks WHERE thread_id IS NOT NULL")
        result = {row[0]: row[1] for row in c.fetchall()}
        _chunk_threads_cache[db_path] = result
        return result
    except Exception:
        return {}
    finally:
        conn.close()


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


class LLMClient:
    """Abstraction over Anthropic, OpenAI, Google Gemini, and Ollama APIs."""

    def __init__(self, provider=None, model=None, project_root=None):
        self.provider = None
        self.client = None
        self.model = None

        # If not specified, read from settings
        if provider is None and project_root:
            settings = config.load_settings(project_root)
            provider = settings.get("rag_provider", "anthropic")
            model = settings.get("rag_model", "claude-opus-4-20250514")

        if provider == "anthropic":
            anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
            if not anthropic_key:
                raise RuntimeError("ANTHROPIC_API_KEY not configured")
            import anthropic
            self.provider = "anthropic"
            self.client = anthropic.Anthropic(api_key=anthropic_key)
            self.model = model or "claude-opus-4-20250514"

        elif provider == "openai":
            openai_key = os.environ.get("OPENAI_API_KEY")
            if not openai_key:
                raise RuntimeError("OPENAI_API_KEY not configured")
            from openai import OpenAI
            self.provider = "openai"
            self.client = OpenAI(api_key=openai_key)
            self.model = model or "gpt-4o-mini"

        elif provider == "gemini":
            gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not gemini_key:
                raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY not configured")
            from google import genai
            self.provider = "gemini"
            self.client = genai.Client(api_key=gemini_key)
            self.model = model or "gemini-2.5-flash"

        elif provider == "ollama":
            from openai import OpenAI
            self.provider = "ollama"
            ollama_url = "http://localhost:11434/v1"
            if project_root:
                settings = config.load_settings(project_root)
                base = settings.get("ollama_base_url", "http://localhost:11434")
                ollama_url = base.rstrip("/") + "/v1"
                model = model or settings.get("ollama_rag_model", "qwen2.5:14b")
            self.client = OpenAI(base_url=ollama_url, api_key="ollama")
            self.model = model or "qwen2.5:14b"

        else:
            # Fallback: try anthropic, then openai
            anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
            openai_key = os.environ.get("OPENAI_API_KEY")
            if anthropic_key:
                import anthropic
                self.provider = "anthropic"
                self.client = anthropic.Anthropic(api_key=anthropic_key)
                self.model = "claude-opus-4-20250514"
            elif openai_key:
                from openai import OpenAI
                self.provider = "openai"
                self.client = OpenAI(api_key=openai_key)
                self.model = "gpt-4o-mini"
            else:
                raise RuntimeError("AI לא מוגדר. הגדירו מפתח API בהגדרות.")

    @staticmethod
    def is_configured() -> bool:
        return bool(
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        )

    @staticmethod
    def get_provider_info() -> dict:
        # Check what's available
        providers = []
        if os.environ.get("ANTHROPIC_API_KEY"):
            providers.append("anthropic")
        if os.environ.get("OPENAI_API_KEY"):
            providers.append("openai")
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            providers.append("gemini")
        # Ollama is always potentially available (local)
        providers.append("ollama")

        return {
            "configured": len(providers) > 1 or bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            "providers": providers,
        }

    def chat_stream(self, system_prompt: str, user_message: str, max_tokens: int = 1024):
        """Generator that yields text chunks as they arrive from the LLM."""
        if self.provider == "anthropic":
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                for text in stream.text_stream:
                    yield text
        elif self.provider == "gemini":
            response = self.client.models.generate_content_stream(
                model=self.model,
                contents=f"{system_prompt}\n\n{user_message}",
                config={"max_output_tokens": max_tokens},
            )
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        else:
            # OpenAI and Ollama both use OpenAI-compatible API
            stream = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                stream=True,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    def chat(self, system_prompt: str, user_message: str, max_tokens: int = 1024) -> str:
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        elif self.provider == "gemini":
            response = self.client.models.generate_content(
                model=self.model,
                contents=f"{system_prompt}\n\n{user_message}",
                config={"max_output_tokens": max_tokens},
            )
            return response.text
        else:
            # OpenAI and Ollama both use OpenAI-compatible API
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content


_llm_client = None
_llm_client_key = None  # track settings to detect changes


def _get_llm_client(project_root=None):
    """Get or create a singleton LLMClient instance. Recreates if settings changed."""
    global _llm_client, _llm_client_key
    # Build a key from current settings to detect changes
    current_key = None
    if project_root:
        settings = config.load_settings(project_root)
        current_key = (settings.get("rag_provider"), settings.get("rag_model"))

    if _llm_client is None or (current_key and current_key != _llm_client_key):
        _llm_client = LLMClient(project_root=project_root)
        _llm_client_key = current_key
    return _llm_client


def ask(db_path: str, question: str, chat_name: str, history: list = None, project_root: str = None, language: str = "he") -> dict:
    """Main RAG pipeline: retrieve relevant chunks, then ask LLM.

    Returns {answer, sources, keywords, provider, debug}.
    """
    # 1. Retrieve relevant conversation chunks
    chunk_groups = retrieve_chunks(db_path, question, max_results=12, language=language)

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
    llm = _get_llm_client(project_root)
    system = get_system_prompt(chat_name, language)
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

    # Log RAG usage
    try:
        from . import usage_tracker
        usage_tracker.log_event({
            "type": "rag", "chat_name": chat_name,
            "provider": llm.provider, "model": llm.model,
            "file": question[:80],
        }, project_root or os.path.dirname(os.path.dirname(db_path)))
    except Exception:
        pass

    return {
        "answer": answer,
        "sources": sources,
        "keywords": extract_keywords(question, language),
        "provider": llm.provider,
        "debug": {
            "chunks_retrieved": len(chunk_groups),
            "chunks_detail": [
                {
                    "chunk_id": g["chunk_id"],
                    "score": g["score"],
                    "start_message_id": g["chunk_data"].get("start_message_id"),
                    "end_message_id": g["chunk_data"].get("end_message_id"),
                    "thread_id": g["chunk_data"].get("thread_id"),
                    "message_count": g["chunk_data"].get("message_count"),
                    "senders": g["chunk_data"].get("senders", ""),
                    "preview": g["chunk_data"].get("combined_text", "")[:200],
                }
                for g in chunk_groups
            ],
        },
    }


def ask_stream(db_path: str, question: str, chat_name: str, history: list = None, project_root: str = None, language: str = "he"):
    """Streaming RAG pipeline. Yields: first a JSON metadata line, then text chunks.

    First yield: JSON string with sources, keywords, debug info
    Subsequent yields: text chunks of the answer
    """
    import json

    # 1. Retrieve relevant conversation chunks
    chunk_groups = retrieve_chunks(db_path, question, max_results=12, language=language)

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

    # 5. Extract source citations from chunks
    sources = []
    seen_ids = set()
    for group in chunk_groups:
        chunk_data = group["chunk_data"]
        messages = chunk_data.get("messages", [])
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

    debug_info = {
        "chunks_retrieved": len(chunk_groups),
        "chunks_detail": [
            {
                "chunk_id": g["chunk_id"],
                "score": g["score"],
                "start_message_id": g["chunk_data"].get("start_message_id"),
                "end_message_id": g["chunk_data"].get("end_message_id"),
                "thread_id": g["chunk_data"].get("thread_id"),
                "message_count": g["chunk_data"].get("message_count"),
                "senders": g["chunk_data"].get("senders", ""),
                "preview": g["chunk_data"].get("combined_text", "")[:200],
            }
            for g in chunk_groups
        ],
    }

    llm = _get_llm_client(project_root)

    # First yield: metadata (sources, keywords, debug)
    yield json.dumps({
        "type": "metadata",
        "sources": sources,
        "keywords": extract_keywords(question, language),
        "provider": llm.provider,
        "debug": debug_info,
    }) + "\n"

    # 6. Stream the answer from LLM
    system = get_system_prompt(chat_name, language)
    for chunk in llm.chat_stream(system, user_message, max_tokens=2048):
        yield chunk

    # Log RAG usage after stream completes
    try:
        from . import usage_tracker
        usage_tracker.log_event({
            "type": "rag", "chat_name": chat_name,
            "provider": llm.provider, "model": llm.model,
            "file": question[:80],
        }, project_root or os.path.dirname(os.path.dirname(db_path)))
    except Exception:
        pass


def ask_with_context(context_text: str, question: str, chat_name: str, history: list = None,
                     project_root: str = None, language: str = "he") -> dict:
    """RAG answer using pre-formatted context (used by proxy endpoint).

    Unlike ask(), this does NOT do retrieval — the caller already retrieved and
    formatted the chunks. This just calls the LLM with the provided context.
    """
    # Build user message
    user_message = f"קטעי שיחה רלוונטיים:\n{context_text}\n\nשאלה: {question}"

    if history and len(history) > 0:
        recent = history[-4:]
        history_text = "\n".join(
            f"{'שאלה קודמת' if h['role'] == 'user' else 'תשובה קודמת'}: {h['content'][:200]}"
            for h in recent
        )
        user_message = f"היסטוריית שיחה אחרונה:\n{history_text}\n\n{user_message}"

    llm = _get_llm_client(project_root)
    system = get_system_prompt(chat_name, language)
    answer = llm.chat(system, user_message, max_tokens=2048)

    return {
        "answer": answer,
        "provider": llm.provider,
        "sources": [],
        "keywords": extract_keywords(question),
        "debug": {"proxy": True, "context_length": len(context_text)},
    }
