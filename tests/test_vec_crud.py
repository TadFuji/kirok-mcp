"""vec_memories stays in sync with memories CRUD operations."""

import shutil
import tempfile
from pathlib import Path

import pytest

from kirok_mcp.db import MemoryDB, _serialize_vector
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


def _vec_obs_count(db) -> int:
    return db.conn.execute("SELECT COUNT(*) FROM vec_observations").fetchone()[0]


def test_observation_crud_syncs_to_vec():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        mid = db.insert_memory(bank_id="b1", content="m", embedding=_make_vec(0.1))
        oid = db.insert_observation("b1", "obs", [mid], embedding=_make_vec(0.2))
        assert _vec_obs_count(db) == 1

        # update keeps a single row
        db.update_observation(oid, "obs v2", [mid], embedding=_make_vec(0.3))
        assert _vec_obs_count(db) == 1

        db.delete_observation(oid)
        assert _vec_obs_count(db) == 0
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_clear_bank_removes_observation_vec_rows():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        m1 = db.insert_memory(bank_id="b1", content="m1", embedding=_make_vec(0.1))
        m2 = db.insert_memory(bank_id="b2", content="m2", embedding=_make_vec(0.2))
        db.insert_observation("b1", "o1", [m1], embedding=_make_vec(0.3))
        db.insert_observation("b2", "o2", [m2], embedding=_make_vec(0.4))
        assert _vec_obs_count(db) == 2
        db.clear_bank("b1")
        assert _vec_obs_count(db) == 1
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_offsize_observation_embedding_not_in_vec():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        # 2-dim embedding != EMBEDDING_DIM → stays on brute-force, not in vec.
        db.insert_observation("b1", "obs", ["m"], embedding=[1.0, 0.0])
        assert _vec_obs_count(db) == 0
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_update_memory_embedding_resyncs_vec():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        mid = db.insert_memory(bank_id="b1", content="m", embedding=_make_vec(0.1))
        assert db.update_memory_embedding(mid, _make_vec(0.9)) is True
        # Still exactly one vec row, and it carries the new vector.
        assert db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0] == 1
        stored = db.conn.execute(
            "SELECT embedding FROM memories WHERE id = ?", (mid,)
        ).fetchone()["embedding"]
        assert stored == _serialize_vector(_make_vec(0.9))
        assert db.update_memory_embedding("ghost", _make_vec(0.1)) is False
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_update_observation_embedding_resyncs_vec_and_keeps_content():
    db, tmp = _make_db()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        mid = db.insert_memory(bank_id="b1", content="m", embedding=_make_vec(0.1))
        oid = db.insert_observation("b1", "keep me", [mid], embedding=_make_vec(0.2))

        assert db.update_observation_embedding(oid, _make_vec(0.9)) is True
        assert _vec_obs_count(db) == 1
        # Content and source_memory_ids are untouched.
        obs = db.get_observations("b1")[0]
        assert obs["content"] == "keep me"
        assert obs["source_memory_ids"] == [mid]
        assert db.update_observation_embedding("ghost", _make_vec(0.1)) is False
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_update_embedding_rejects_wrong_dimension():
    db, tmp = _make_db()
    try:
        mid = db.insert_memory(bank_id="b1", content="m", embedding=_make_vec(0.1))
        oid = db.insert_observation("b1", "o", [mid], embedding=_make_vec(0.2))
        # A mis-sized vector must fail loudly rather than corrupt the BLOB width.
        with pytest.raises(ValueError):
            db.update_memory_embedding(mid, [1.0, 0.0])
        with pytest.raises(ValueError):
            db.update_observation_embedding(oid, [1.0, 0.0])
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
