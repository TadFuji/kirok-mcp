"""Shared helpers for the Kirok database layer.

Vector (de)serialization, FTS5 query sanitization, JSON utilities, and
database path resolution. Pure functions and constants only.
"""

import json
import re
import struct
from pathlib import Path


# Upper bound on retained background-failure rows (oldest pruned on insert),
# keeping the system_events table from growing without bound.
MAX_SYSTEM_EVENTS = 200


def _serialize_vector(vector: list[float]) -> bytes:
    """Serialize a float vector to bytes for SQLite BLOB storage."""
    return struct.pack(f"{len(vector)}f", *vector)


def _deserialize_vector(blob: bytes) -> list[float]:
    """Deserialize bytes back to a float vector."""
    n = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{n}f", blob))


_FTS5_OPERATORS = re.compile(r'\b(AND|OR|NOT|NEAR)\b', re.IGNORECASE)


def _join_json_list(raw: str) -> str:
    """Space-join a JSON array string for FTS indexing, tolerating bad JSON.

    Mirrors how ``insert_memory`` feeds entities/keywords into the FTS index.
    A row whose JSON is malformed (legacy or hand-edited data) falls back to
    empty text so a single bad row cannot abort an index rebuild.
    """
    try:
        parsed = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(parsed, list):
        return " ".join(str(x) for x in parsed)
    return ""


def _sanitize_fts_query(query: str) -> str | None:
    """Sanitize a query string for safe use with FTS5 MATCH.

    FTS5 interprets hyphens as NOT, bare uppercase words (e.g. ASCII)
    as column names, and has other special syntax that can cause crashes
    with user-supplied queries (especially Japanese text with hyphens
    like dates '2026-03-25').

    Strategy: wrap each token in double quotes to force literal matching.
    Returns None if no valid tokens remain after sanitization.
    """
    if not query or not query.strip():
        return None

    # Remove FTS5 special characters: *, ^, ", NEAR()
    cleaned = query.replace('"', ' ').replace('*', ' ').replace('^', ' ')
    # Replace hyphens with spaces (prevents NOT interpretation)
    cleaned = cleaned.replace('-', ' ')
    # Remove parentheses used in NEAR()
    cleaned = cleaned.replace('(', ' ').replace(')', ' ')
    # Remove FTS5 operators
    cleaned = _FTS5_OPERATORS.sub(' ', cleaned)

    # Split into tokens. The trigram tokenizer indexes 3-character windows, so
    # tokens shorter than 3 chars can never match — drop them and let semantic
    # search cover those (e.g. 2-char Japanese words). If nothing remains, return
    # None so recall falls back to the semantic path (existing behavior).
    tokens = [t for t in cleaned.split() if len(t) >= 3]
    if not tokens:
        return None

    # Double-quote each token for safe literal matching
    quoted = ' '.join(f'"{t}"' for t in tokens)
    return quoted


def _resolve_db_path(db_path: str | Path | None) -> Path:
    """Resolve the database path.

    Priority:
    1. Explicit db_path argument (if provided)
    2. ~/.kirok/memory.db (default)
    """
    if db_path is not None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    kirok_dir = Path.home() / ".kirok"
    kirok_db = kirok_dir / "memory.db"
    kirok_dir.mkdir(parents=True, exist_ok=True)
    return kirok_db
