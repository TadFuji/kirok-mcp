"""vec_search returns ranked results and falls back to brute-force."""

import json
import tempfile
from pathlib import Path

import pytest

from kirok_mcp.db import MemoryDB, _serialize_vector
from kirok_mcp.embeddings import EMBEDDING_DIM, semantic_search


def _make_vec(seed: float) -> list[float]:
    return [seed + i * 1e-4 for i in range(EMBEDDING_DIM)]


def _setup_db_with_memories(n: int = 10, bank_id: str = "b1"):
    """Create a temp DB with n memories, each with a unique embedding."""
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "test.db"
    db = MemoryDB(db_path=path)
    db.connect()
    ids = []
    for i in range(n):
        mid = db.insert_memory(
            bank_id=bank_id,
            content=f"memory {i}",
            embedding=_make_vec(float(i) * 0.1),
            entities=[f"e{i}"],
            keywords=[f"k{i}"],
        )
        ids.append(mid)
    return db, tmp, ids


def test_vec_search_returns_results():
    db, tmp, ids = _setup_db_with_memories(10, "b1")
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        query = _make_vec(0.05)
        results = db.vec_search(query, "b1", top_k=5)
        assert len(results) <= 5
        assert len(results) > 0
        for r in results:
            assert "id" in r
            assert "content" in r
            assert "similarity" in r
            assert 0.0 <= r["similarity"] <= 1.0
    finally:
        db.close()


def test_vec_search_filters_by_bank():
    db, tmp, _ = _setup_db_with_memories(5, "b1")
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        # Add memories to different bank
        for i in range(5):
            db.insert_memory(bank_id="b2", content=f"other {i}", embedding=_make_vec(float(i) * 0.1))
        query = _make_vec(0.05)
        results = db.vec_search(query, "b1", top_k=10)
        result_ids = {r["id"] for r in results}
        # All results should be from b1
        for r in results:
            mem = db.get_memory(r["id"])
            assert mem["bank_id"] == "b1"
    finally:
        db.close()


def test_vec_search_accuracy_vs_brute_force():
    """Top results from vec_search should largely overlap with brute-force."""
    db, tmp, ids = _setup_db_with_memories(20, "b1")
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        query = _make_vec(0.15)

        vec_results = db.vec_search(query, "b1", top_k=5)
        vec_ids = {r["id"] for r in vec_results}

        bf_results = db._brute_force_search(query, "b1", top_k=5)
        bf_ids = {r["id"] for r in bf_results}

        # Jaccard similarity should be high (>= 0.6)
        intersection = vec_ids & bf_ids
        union = vec_ids | bf_ids
        jaccard = len(intersection) / len(union) if union else 1.0
        assert jaccard >= 0.6, f"vec vs brute-force Jaccard={jaccard}, vec={vec_ids}, bf={bf_ids}"
    finally:
        db.close()


def test_vec_search_fallback_when_unavailable():
    """When _vec_available is False, vec_search uses brute-force."""
    db, tmp, ids = _setup_db_with_memories(5, "b1")
    try:
        db._vec_available = False
        query = _make_vec(0.05)
        results = db.vec_search(query, "b1", top_k=3)
        assert len(results) > 0
        assert len(results) <= 3
        for r in results:
            assert "similarity" in r
    finally:
        db.close()


def test_vec_search_small_bank_not_crowded_out():
    """A small bank's matches must not be crowded out by a larger, closer bank.

    Regression for the global-KNN-then-Python-filter approach: with bank_id as a
    vec0 partition key the KNN is per-bank, so a 100-row bank packed next to the
    query can no longer squeeze a 3-row bank out of the results.
    """
    tmp = tempfile.mkdtemp()
    db = MemoryDB(db_path=Path(tmp) / "test.db")
    db.connect()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        # Large bank packed extremely close to the query.
        for i in range(100):
            db.insert_memory(bank_id="big", content=f"big {i}", embedding=_make_vec(i * 1e-6))
        # Small bank, farther from the query.
        small_ids = {
            db.insert_memory(bank_id="small", content=f"small {i}", embedding=_make_vec(0.5 + i * 0.05))
            for i in range(3)
        }
        query = _make_vec(0.0)
        vec_ids = {r["id"] for r in db.vec_search(query, "small", top_k=5)}
        bf_ids = {r["id"] for r in db._brute_force_search(query, "small", top_k=5)}
        # All 3 small-bank memories returned, matching brute force (no crowd-out).
        assert vec_ids == small_ids
        assert vec_ids == bf_ids
    finally:
        db.close()


def _setup_db_with_observations(n: int = 10, bank_id: str = "b1"):
    """Create a temp DB with n observations, each with a unique embedding."""
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "test.db"
    db = MemoryDB(db_path=path)
    db.connect()
    anchor = db.insert_memory(bank_id=bank_id, content="anchor", embedding=_make_vec(0.0))
    ids = []
    for i in range(n):
        oid = db.insert_observation(
            bank_id, f"observation {i}", [anchor], embedding=_make_vec(float(i) * 0.1)
        )
        ids.append(oid)
    return db, tmp, ids


def test_vec_search_observations_returns_results():
    db, tmp, ids = _setup_db_with_observations(8, "b1")
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        results = db.vec_search_observations(_make_vec(0.05), "b1", top_k=5)
        assert 0 < len(results) <= 5
        for r in results:
            assert {"id", "content", "similarity"} <= set(r)
            assert 0.0 <= r["similarity"] <= 1.0
    finally:
        db.close()


def test_vec_search_observations_filters_by_bank():
    db, tmp, _ = _setup_db_with_observations(5, "b1")
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        m2 = db.insert_memory(bank_id="b2", content="x", embedding=_make_vec(0.0))
        for i in range(5):
            db.insert_observation("b2", f"other {i}", [m2], embedding=_make_vec(float(i) * 0.1))
        results = db.vec_search_observations(_make_vec(0.05), "b1", top_k=10)
        b1_obs_ids = {o["id"] for o in db.get_observations("b1", limit=100)}
        for r in results:
            assert r["id"] in b1_obs_ids
    finally:
        db.close()


def test_vec_search_observations_matches_brute_force():
    db, tmp, ids = _setup_db_with_observations(12, "b1")
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        q = _make_vec(0.15)
        vec_ids = {r["id"] for r in db.vec_search_observations(q, "b1", top_k=5)}
        bf_ids = {r["id"] for r in db._brute_force_search_observations(q, "b1", top_k=5)}
        union = vec_ids | bf_ids
        jaccard = len(vec_ids & bf_ids) / len(union) if union else 1.0
        assert jaccard >= 0.6, f"vec vs brute-force Jaccard={jaccard}"
    finally:
        db.close()


def test_vec_search_observations_fallback_when_unavailable():
    db, tmp, ids = _setup_db_with_observations(5, "b1")
    try:
        db._vec_available = False
        results = db.vec_search_observations(_make_vec(0.05), "b1", top_k=3)
        assert 0 < len(results) <= 3
        for r in results:
            assert "similarity" in r
    finally:
        db.close()


def test_vec_search_dedup_survives_large_noise_bank():
    """The dedup path (top_k=5) must still surface a near-duplicate in a small
    bank even when a large, closer bank floods the global nearest neighbours."""
    tmp = tempfile.mkdtemp()
    db = MemoryDB(db_path=Path(tmp) / "test.db")
    db.connect()
    try:
        if not db._vec_available:
            pytest.skip("sqlite-vec not loaded")
        # The genuine near-duplicate living in a small bank.
        near_dup = _make_vec(0.30)
        db.insert_memory(bank_id="prefs", content="durable fact", embedding=near_dup)
        # Large noise bank nearly identical to the query (closer than the near-dup).
        for i in range(50):
            db.insert_memory(bank_id="noise", content=f"noise {i}", embedding=_make_vec(i * 1e-6))
        query = _make_vec(0.0)
        results = db.vec_search(query, "prefs", top_k=5)
        assert len(results) == 1
        # Surfaces above the 0.85 dedup threshold despite the noise bank.
        assert results[0]["similarity"] > 0.85
    finally:
        db.close()
