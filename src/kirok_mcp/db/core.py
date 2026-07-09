"""MemoryDB: the public database facade, composed from per-domain mixins."""

import sqlite3
from pathlib import Path
from typing import Optional

from kirok_mcp.db.banks import BankMixin
from kirok_mcp.db.base import _resolve_db_path
from kirok_mcp.db.memories import MemoryMixin
from kirok_mcp.db.models import MentalModelMixin
from kirok_mcp.db.observations import ObservationMixin
from kirok_mcp.db.schema import SchemaMixin
from kirok_mcp.db.search import SearchMixin

# How long a connection waits on a locked database before raising
# "database is locked" (ms for the PRAGMA, seconds for sqlite3.connect).
_BUSY_TIMEOUT_MS = 30000


class MemoryDB(
    SchemaMixin,
    MemoryMixin,
    SearchMixin,
    ObservationMixin,
    MentalModelMixin,
    BankMixin,
):
    """SQLite-backed memory database with FTS5 full-text search."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = _resolve_db_path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        # Set True in connect() once the sqlite-vec extension loads successfully.
        # When False, all vector search transparently falls back to brute force.
        self._vec_available: bool = False

    def connect(self) -> None:
        """Open database connection and initialize schema."""
        self.conn = sqlite3.connect(str(self.db_path), timeout=_BUSY_TIMEOUT_MS / 1000)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        self._load_vec_extension()
        self._init_schema()

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
