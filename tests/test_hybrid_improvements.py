"""Optimization batch: failure surfacing, observation keyword search, NFKC FTS.

Covers the changes from the 2026-07 expert-panel review:
- A consolidation LLM failure must leave memories unconsolidated (never mark
  them consolidated with nothing produced) and be recorded to system_events.
- Observations are reachable by keyword (fts_observations was previously
  written on every insert/update but never queried).
- FTS text is NFKC-normalized on both the index and query side, so width
  variants (full-width ASCII, half-width katakana) match; legacy indexes are
  rebuilt via the PRAGMA user_version gate.
- The short-CJK LIKE rescue is OR-joined (any token hits), matching the
  OR-joined FTS MATCH semantics.
- update_memory(commit=False) leaves the transaction open for the caller
  (dedup folds its audit event into the same commit).

All offline: real MemoryDB on a temp file, fake embedder/LLM.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from kirok_mcp import server
from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import EMBEDDING_DIM


def _vec(axis: int = 0) -> list[float]:
    """Unit vector along one axis — orthogonal axes give cosine similarity 0."""
    v = [0.0] * EMBEDDING_DIM
    v[axis] = 1.0
    return v


def _make_db():
    tmp = tempfile.mkdtemp()
    db = MemoryDB(db_path=Path(tmp) / "memory.db")
    db.connect()
    return db, tmp


class _FixedEmbedder:
    async def embed(self, text: str, task_type: str = "") -> list[float]:
        return _vec(0)


class _FailingLLM:
    async def consolidate(self, *args, **kwargs):
        raise RuntimeError("LLM down (simulated)")


# ── Consolidation failure surfacing ───────────────────────────────────


def test_consolidation_llm_failure_keeps_memories_pending():
    """LLM failure must propagate and leave every memory unconsolidated —
    the pre-fix behavior returned [] and permanently retired the batch."""
    db, tmp = _make_db()
    saved = (server._db, server._embedder, server._llm)
    server._db, server._embedder, server._llm = db, _FixedEmbedder(), _FailingLLM()
    try:
        db.insert_memory("cons-fail", "memory one", embedding=_vec(1))
        db.insert_memory("cons-fail", "memory two", embedding=_vec(2))

        with pytest.raises(RuntimeError):
            asyncio.run(server._run_consolidation("cons-fail"))

        assert db.count_unconsolidated_memories("cons-fail") == 2
        assert db.get_observations("cons-fail") == []
    finally:
        server._db, server._embedder, server._llm = saved
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_maybe_consolidate_records_failure_event():
    """The debounced auto path swallows the error (fail-open for retain) but
    must record it so KIROK_stats surfaces the silent skip."""
    db, tmp = _make_db()
    saved = (server._db, server._embedder, server._llm)
    server._db, server._embedder, server._llm = db, _FixedEmbedder(), _FailingLLM()
    try:
        for i in range(server.CONSOLIDATION_BATCH_SIZE):
            db.insert_memory("cons-auto", f"memory {i}", embedding=_vec(i + 1))

        asyncio.run(server._maybe_consolidate("cons-auto"))  # must not raise

        events = [
            r["event"]
            for r in db.conn.execute(
                "SELECT event FROM system_events WHERE bank_id = ?",
                ("cons-auto",),
            ).fetchall()
        ]
        assert "auto_consolidation" in events
        assert (
            db.count_unconsolidated_memories("cons-auto")
            == server.CONSOLIDATION_BATCH_SIZE
        )
    finally:
        server._db, server._embedder, server._llm = saved
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ── Observation keyword search ────────────────────────────────────────


def test_fts_search_observations_keyword_and_short_cjk():
    db, tmp = _make_db()
    try:
        m = db.insert_memory("obank", "src", embedding=_vec(1))
        db.insert_observation(
            "obank", "quokka deployment notes", [m], embedding=_vec(2)
        )
        db.insert_observation(
            "obank", "京都の出張で得た教訓", [m], embedding=_vec(3)
        )

        hits = db.fts_search_observations("obank", "quokka")
        assert [h["content"] for h in hits] == ["quokka deployment notes"]
        assert hits[0]["source_memory_ids"] == [m]

        # 2-char kanji token → served by the LIKE rescue, not trigram MATCH
        hits = db.fts_search_observations("obank", "京都")
        assert [h["content"] for h in hits] == ["京都の出張で得た教訓"]
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_fts_search_observations_excludes_deprecated():
    db, tmp = _make_db()
    try:
        m = db.insert_memory("obank2", "src", embedding=_vec(1))
        obs_id = db.insert_observation(
            "obank2", "quokka deployment notes", [m], embedding=_vec(2)
        )
        assert db.fts_search_observations("obank2", "quokka")

        db.deprecate_observation(obs_id, reason="test")
        assert db.fts_search_observations("obank2", "quokka") == []
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_recall_surfaces_observation_on_keyword_only_hit():
    """An observation whose cosine similarity is below the floor must still
    appear when the query literally matches its text — pre-fix, observations
    had no keyword path at all."""
    db, tmp = _make_db()
    saved = (server._db, server._embedder)
    server._db, server._embedder = db, _FixedEmbedder()
    try:
        m = db.insert_memory("okw", "unrelated source", embedding=_vec(5))
        # Orthogonal to the query embedding (axis 0) → similarity 0 < floor.
        db.insert_observation(
            "okw", "quokka rollout was reverted twice", [m], embedding=_vec(7)
        )

        result = asyncio.run(server.KIROK_recall(bank_id="okw", query="quokka"))
        assert "quokka rollout was reverted twice" in result
        assert "★" in result
    finally:
        server._db, server._embedder = saved
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ── NFKC normalization ────────────────────────────────────────────────


def test_fts_matches_across_width_variants():
    db, tmp = _make_db()
    try:
        full_width = db.insert_memory(
            "nfkc", "ＭＣＰの設定手順", embedding=_vec(1)
        )
        half_kana = db.insert_memory(
            "nfkc", "ﾊﾞｸﾞ修正の記録", embedding=_vec(2)
        )

        # ASCII query vs full-width stored text (trigram MATCH path)
        ids = {r["id"] for r in db.fts_search("nfkc", "MCP")}
        assert full_width in ids

        # Full-width katakana query vs half-width stored text (LIKE rescue —
        # NFKC folds the half-width form into the katakana block)
        ids = {r["id"] for r in db.fts_search("nfkc", "バグ")}
        assert half_kana in ids
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_legacy_fts_index_rebuilt_via_user_version():
    """A database whose FTS rows predate the NFKC normalization gets rebuilt
    from the source tables on the next connect (user_version gate)."""
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "memory.db"
    try:
        db = MemoryDB(db_path=path)
        db.connect()
        mid = db.insert_memory("legacy", "ＭＣＰの設定手順", embedding=_vec(1))
        # Simulate a pre-normalization index: raw full-width text + version 0.
        db.conn.execute("DELETE FROM fts_memories WHERE id = ?", (mid,))
        db.conn.execute(
            "INSERT INTO fts_memories (id, bank_id, content, entities, keywords, context) "
            "VALUES (?, 'legacy', 'ＭＣＰの設定手順', '', '', '')",
            (mid,),
        )
        db.conn.execute("PRAGMA user_version = 0")
        db.conn.commit()
        assert db.fts_search("legacy", "MCP") == []  # legacy index can't match
        db.close()

        db = MemoryDB(db_path=path)
        db.connect()  # migration rebuilds + stamps user_version
        assert {r["id"] for r in db.fts_search("legacy", "MCP")} == {mid}
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] >= 1
        db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── OR-joined LIKE rescue ─────────────────────────────────────────────


def test_short_token_rescue_is_or_joined():
    """A memory containing ANY short token must surface — AND semantics hid
    every partial match while the FTS MATCH side was already OR-joined."""
    db, tmp = _make_db()
    try:
        kyoto = db.insert_memory("orsem", "京都の記録", embedding=_vec(1))
        osaka = db.insert_memory("orsem", "大阪の記録", embedding=_vec(2))

        ids = {r["id"] for r in db.fts_search("orsem", "京都 大阪")}
        assert ids == {kyoto, osaka}
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ── update_memory(commit=False) ───────────────────────────────────────


def test_update_memory_commit_false_leaves_transaction_open():
    db, tmp = _make_db()
    try:
        mid = db.insert_memory("txn", "original", embedding=_vec(1))

        assert db.update_memory(mid, content="staged", commit=False)
        db.conn.rollback()
        assert db.get_memory(mid)["content"] == "original"

        assert db.update_memory(mid, content="committed")  # default commits
        db.conn.rollback()  # no-op: already committed
        assert db.get_memory(mid)["content"] == "committed"
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ── Schema ────────────────────────────────────────────────────────────


def test_unconsolidated_partial_index_exists():
    db, tmp = _make_db()
    try:
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_memories_unconsolidated",),
        ).fetchone()
        assert row is not None
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
