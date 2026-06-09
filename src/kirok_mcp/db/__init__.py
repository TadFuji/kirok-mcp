"""SQLite database layer for Kirok memory storage.

Manages memories, mental models, observations, bank configs, FTS5
full-text indexes, and sqlite-vec KNN indexes. Split by domain into
submodules; ``MemoryDB`` (core.py) composes them. The public import
surface is unchanged: ``from kirok_mcp.db import MemoryDB``.
"""

from kirok_mcp.db.base import (
    MAX_SYSTEM_EVENTS,
    _deserialize_vector,
    _join_json_list,
    _resolve_db_path,
    _sanitize_fts_query,
    _serialize_vector,
)
from kirok_mcp.db.core import MemoryDB

__all__ = [
    "MAX_SYSTEM_EVENTS",
    "MemoryDB",
    "_deserialize_vector",
    "_join_json_list",
    "_resolve_db_path",
    "_sanitize_fts_query",
    "_serialize_vector",
]
