"""Consolidation is atomic and LLM deletes are soft (deprecated, not destroyed).

These exercise ``server._run_consolidation`` end-to-end against a REAL MemoryDB
(temp file) with an offline fake embedder and fake LLM, so the SQLite
transaction semantics under test are the real ones — no API key required.
"""

import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from kirok_mcp import server
from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import EMBEDDING_DIM


def _vec(seed: float = 0.1) -> list[float]:
    return [seed + i * 1e-4 for i in range(EMBEDDING_DIM)]


class _FakeEmbedder:
    """Returns a fixed-size vector; optionally raises on the Nth embed call."""

    def __init__(self, fail_on_call: int | None = None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    async def embed(self, text: str, task_type: str = "") -> list[float]:
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise RuntimeError("embed API failure (simulated)")
        return _vec(0.1 + 0.001 * self.calls)


class _FakeLLM:
    """Returns a canned consolidate() action list."""

    def __init__(self, actions: list[dict]):
        self._actions = actions

    async def consolidate(
        self, new_memories, existing_observations, observations_mission=""
    ) -> list[dict]:
        return self._actions


def _make_db():
    tmp = tempfile.mkdtemp()
    db = MemoryDB(db_path=Path(tmp) / "memory.db")
    db.connect()
    return db, tmp


def _run(db, embedder, llm, bank_id: str):
    """Swap the server module globals, run consolidation, then restore."""
    saved = (server._db, server._embedder, server._llm)
    server._db, server._embedder, server._llm = db, embedder, llm
    try:
        return asyncio.run(server._run_consolidation(bank_id))
    finally:
        server._db, server._embedder, server._llm = saved


def test_embed_failure_leaves_db_untouched():
    """Embed failing on the 2nd action must apply ZERO observation changes and
    leave every source memory unconsolidated (no half-applied batch)."""
    db, tmp = _make_db()
    try:
        db.insert_memory("b", "memory one", embedding=_vec(0.2))
        db.insert_memory("b", "memory two", embedding=_vec(0.3))

        llm = _FakeLLM([
            {"action": "create", "content": "obs A",
             "observation_id": "", "source_memory_ids": []},
            {"action": "create", "content": "obs B",
             "observation_id": "", "source_memory_ids": []},
        ])
        embedder = _FakeEmbedder(fail_on_call=2)  # 2nd create's embed blows up

        with pytest.raises(RuntimeError):
            _run(db, embedder, llm, "b")

        # Pre-embed runs before any write, so nothing landed in the DB.
        assert embedder.calls == 2
        assert db.get_observations("b") == []
        assert db.conn.execute(
            "SELECT COUNT(*) FROM observations"
        ).fetchone()[0] == 0
        # Both memories remain unconsolidated → they roll into the next batch.
        assert db.count_unconsolidated_memories("b") == 2
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_normal_path_applies_changes_and_marks_memories():
    """Happy path: create + update land, and every source memory is marked
    consolidated in the same commit."""
    db, tmp = _make_db()
    try:
        m1 = db.insert_memory("b", "memory one", embedding=_vec(0.2))
        m2 = db.insert_memory("b", "memory two", embedding=_vec(0.3))
        o1 = db.insert_observation("b", "old observation", [m1], embedding=_vec(0.4))

        llm = _FakeLLM([
            {"action": "create", "content": "new observation",
             "observation_id": "", "source_memory_ids": [m2]},
            {"action": "update", "content": "updated observation",
             "observation_id": o1, "source_memory_ids": [m2]},
        ])
        result = _run(db, _FakeEmbedder(), llm, "b")

        obs = {o["content"]: o for o in db.get_observations("b")}
        assert set(obs) == {"new observation", "updated observation"}
        # Update merged the source memory ids of o1.
        assert set(obs["updated observation"]["source_memory_ids"]) == {m1, m2}
        # Both memories committed as consolidated in the same transaction.
        assert db.count_unconsolidated_memories("b") == 0
        assert "created: 1" in result.lower()
        assert "updated: 1" in result.lower()
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_action_deprecates_without_destroying():
    """A delete action soft-deprecates: the row survives, disappears from every
    read path, and an audit event records what was retired and why."""
    db, tmp = _make_db()
    try:
        m1 = db.insert_memory("b", "memory one", embedding=_vec(0.2))
        o1 = db.insert_observation("b", "stale observation", [m1], embedding=_vec(0.4))

        llm = _FakeLLM([
            {"action": "delete", "content": "contradicted by newer data",
             "observation_id": o1, "source_memory_ids": []},
        ])
        _run(db, _FakeEmbedder(), llm, "b")

        # Gone from listing / existing_obs and from the search read paths...
        assert db.get_observations("b") == []
        assert db.get_observation_embeddings("b") == []
        assert all(
            o["id"] != o1
            for o in db.vec_search_observations(_vec(0.4), "b", top_k=5)
        )
        # ...but the row is still there, now stamped deprecated_at.
        row = db.conn.execute(
            "SELECT deprecated_at FROM observations WHERE id = ?", (o1,)
        ).fetchone()
        assert row is not None and row["deprecated_at"] is not None
        # Audit event captured the id and the LLM's reason. Deprecations are
        # audit trail, not failures, so they are excluded from
        # get_recent_failures -- read system_events directly.
        deprecations = db.conn.execute(
            "SELECT detail FROM system_events "
            "WHERE bank_id = ? AND event = 'observation_deprecated'",
            ("b",),
        ).fetchall()
        assert len(deprecations) == 1
        assert o1 in deprecations[0]["detail"]
        assert "contradicted by newer data" in deprecations[0]["detail"]
        # Memory still consolidated.
        assert db.count_unconsolidated_memories("b") == 0
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_commit_false_defers_persistence_until_batch_commit():
    """The transaction boundary itself: an observation written with commit=False
    is invisible to other connections until a later commit=True flushes the whole
    batch — this is what makes the consolidation batch a single atomic unit."""
    db, tmp = _make_db()
    try:
        db.insert_memory("b", "m", embedding=_vec(0.2))
        oid = db.insert_observation(
            "b", "pending", [], embedding=_vec(0.3), commit=False
        )

        # A separate connection sees the last committed snapshot only — the
        # deferred write is not durable yet.
        other = sqlite3.connect(str(db.db_path))
        pending = other.execute(
            "SELECT COUNT(*) FROM observations WHERE id = ?", (oid,)
        ).fetchone()[0]
        other.close()
        assert pending == 0

        # The final mark (commit=True) commits the whole open transaction.
        db.mark_memories_consolidated([], commit=True)

        after = sqlite3.connect(str(db.db_path))
        committed = after.execute(
            "SELECT COUNT(*) FROM observations WHERE id = ?", (oid,)
        ).fetchone()[0]
        after.close()
        assert committed == 1
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_deprecated_at_migration_is_idempotent_on_old_db():
    """Opening a pre-existing DB whose observations table lacks deprecated_at
    adds the column without touching the legacy row, and re-opening is a no-op."""
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "old.db"
    try:
        # Build an "old" observations table WITHOUT deprecated_at + one row.
        conn = sqlite3.connect(str(path))
        conn.execute(
            """CREATE TABLE observations (
                id TEXT PRIMARY KEY,
                bank_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source_memory_ids TEXT DEFAULT '[]',
                embedding BLOB,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO observations "
            "(id, bank_id, content, source_memory_ids, created_at, updated_at) "
            "VALUES ('o-old', 'b', 'legacy observation', '[]', 't', 't')"
        )
        conn.commit()
        conn.close()

        # First open runs the migration (ALTER ADD COLUMN); legacy row survives.
        db = MemoryDB(db_path=path)
        db.connect()
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(observations)")}
        assert "deprecated_at" in cols
        assert [o["id"] for o in db.get_observations("b")] == ["o-old"]
        db.close()

        # Second open runs the migration again — ADD COLUMN is a swallowed no-op,
        # no error, data intact.
        db2 = MemoryDB(db_path=path)
        db2.connect()
        assert [o["id"] for o in db2.get_observations("b")] == ["o-old"]
        db2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
