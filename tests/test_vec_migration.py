"""Startup migration fills vec_memories from memories.embedding."""

import tempfile
from pathlib import Path

import pytest

from kirok_mcp.db import MemoryDB, _serialize_vector
from kirok_mcp.embeddings import EMBEDDING_DIM


def _make_vec(dim: int, seed: float) -> list[float]:
    return [seed + i * 1e-4 for i in range(dim)]


def test_migration_populates_vec_from_memories():
    dim = EMBEDDING_DIM
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.db"
        db = MemoryDB(db_path=path)
        db.connect()
        try:
            if not db._vec_available:
                pytest.skip("sqlite-vec not loaded")
            bid = "bank-a"
            for i in range(12):
                eid = f"id-{i}"
                emb = _make_vec(dim, float(i) * 0.01)
                db.conn.execute(
                    """INSERT INTO memories
                       (id, bank_id, content, entities, keywords, context,
                        embedding, timestamp, created_at, metadata)
                       VALUES (?, ?, 'x', '[]', '[]', '', ?, ?, ?, '{}')""",
                    (
                        eid,
                        bid,
                        _serialize_vector(emb),
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:00Z",
                    ),
                )
                db.conn.execute(
                    """INSERT INTO fts_memories (id, bank_id, content, entities, keywords, context)
                       VALUES (?, ?, 'x', '', '', '')""",
                    (eid, bid),
                )
            db.conn.commit()
        finally:
            db.close()

        db2 = MemoryDB(db_path=path)
        db2.connect()
        try:
            if not db2._vec_available:
                pytest.skip("sqlite-vec not loaded")
            n_mem = db2.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL"
            ).fetchone()[0]
            n_vec = db2.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
            assert n_vec == n_mem
        finally:
            db2.close()


def test_observation_migration_populates_and_reconciles_vec():
    """vec_observations is backfilled from observations and orphans are reconciled."""
    dim = EMBEDDING_DIM
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "obs.db"
        db = MemoryDB(db_path=path)
        db.connect()
        try:
            if not db._vec_available:
                pytest.skip("sqlite-vec not loaded")
            mid = db.insert_memory(bank_id="b1", content="m", embedding=_make_vec(dim, 0.1))
            oid = db.insert_observation("b1", "o", [mid], embedding=_make_vec(dim, 0.2))
            # Inject an orphan vec_observations row (simulates a delete made while
            # the extension was unavailable).
            db.conn.execute(
                "INSERT INTO vec_observations (observation_id, bank_id, embedding) "
                "VALUES (?, ?, ?)",
                ("orphan-obs", "b1", _serialize_vector(_make_vec(dim, 0.3))),
            )
            db.conn.commit()
            assert db.conn.execute(
                "SELECT COUNT(*) FROM vec_observations"
            ).fetchone()[0] == 2
        finally:
            db.close()

        db2 = MemoryDB(db_path=path)
        db2.connect()
        try:
            if not db2._vec_available:
                pytest.skip("sqlite-vec not loaded")
            ids = {
                r["observation_id"]
                for r in db2.conn.execute("SELECT observation_id FROM vec_observations")
            }
            assert ids == {oid}  # orphan reconciled away, real observation kept
        finally:
            db2.close()


def test_migration_reconciles_orphan_vec_rows():
    """Reconnect repairs vec rows whose source memory no longer exists.

    Regression for the old one-directional (vec_count < total) guard, which could
    not heal an over-count. A delete made while the extension was unavailable
    leaves an orphan vec row; the id-set reconciliation must drop it on connect.
    """
    dim = EMBEDDING_DIM
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "orphan.db"
        db = MemoryDB(db_path=path)
        db.connect()
        try:
            if not db._vec_available:
                pytest.skip("sqlite-vec not loaded")
            mid = db.insert_memory(
                bank_id="b1", content="real", embedding=_make_vec(dim, 0.1)
            )
            # Inject an orphan vec row with no matching memory (simulates a delete
            # that happened while the extension was unavailable).
            db.conn.execute(
                "INSERT INTO vec_memories (memory_id, bank_id, embedding) "
                "VALUES (?, ?, ?)",
                ("orphan-id", "b1", _serialize_vector(_make_vec(dim, 0.2))),
            )
            db.conn.commit()
            assert db.conn.execute(
                "SELECT COUNT(*) FROM vec_memories"
            ).fetchone()[0] == 2
        finally:
            db.close()

        db2 = MemoryDB(db_path=path)
        db2.connect()
        try:
            if not db2._vec_available:
                pytest.skip("sqlite-vec not loaded")
            ids = {
                r["memory_id"]
                for r in db2.conn.execute("SELECT memory_id FROM vec_memories")
            }
            assert ids == {mid}  # orphan reconciled away, only the real row remains
        finally:
            db2.close()
