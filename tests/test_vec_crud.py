"""vec_memories stays in sync with memories CRUD operations."""

import shutil
import tempfile
from pathlib import Path

import pytest

from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import EMBEDDING_DIM


def _make_vec(seed: float = 0.1) -> list[float]:
    return [seed + i * 1e-4 for i in range(EMBEDDING_DIM)]


def _make_db():
    """Create a MemoryDB with a temp directory, connect, return (db, tmpdir)."""
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "test.db"
    db = MemoryDB(db_path=path)
    db.connect()
    return db, tmp


def test_insert_memory_syncs_to_vec():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        mid = db.insert_memory(
            bank_id="b1",
            content="test content",
            embedding=_make_vec(0.1),
            entities=["e1"],
            keywords=["k1"],
        )
        count = db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
        assert count == 1
        row = db.conn.execute("SELECT memory_id FROM vec_memories").fetchone()
        assert row["memory_id"] == mid
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_update_memory_syncs_to_vec():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        mid = db.insert_memory(bank_id="b1", content="old", embedding=_make_vec(0.1))
        db.update_memory(memory_id=mid, content="new", embedding=_make_vec(0.2))
        count = db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
        assert count == 1  # still 1 row, just updated
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_memory_removes_from_vec():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        mid = db.insert_memory(bank_id="b1", content="del me", embedding=_make_vec())
        assert db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0] == 1
        db.delete_memory(mid)
        assert db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0] == 0
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_clear_bank_removes_vec_rows():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        db.insert_memory(bank_id="b1", content="m1", embedding=_make_vec(0.1))
        db.insert_memory(bank_id="b1", content="m2", embedding=_make_vec(0.2))
        db.insert_memory(bank_id="b2", content="m3", embedding=_make_vec(0.3))
        assert db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0] == 3
        db.clear_bank("b1")
        assert db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0] == 1
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
