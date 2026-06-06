"""sqlite-vec loads and vec_memories exists after connect."""

import tempfile
from pathlib import Path

import pytest

from kirok_mcp.db import MemoryDB


def test_vec_table_exists_when_extension_available():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db = MemoryDB(db_path=db_path)
        db.connect()
        try:
            if not db._vec_available:
                pytest.skip("sqlite-vec did not load in this environment")
            row = db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
            ).fetchone()
            assert row is not None
        finally:
            db.close()
