"""Search-quality regressions for the OR-ized FTS, FTS metadata backfill,
direct time-range filtering, the semantic similarity floor, and RRF field
merging. Offline: no API key, no network (query embeddings are supplied
directly or the DB is faked)."""

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kirok_mcp import server
from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import reciprocal_rank_fusion


class FtsOrAndMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "memory.db"
        self.db = MemoryDB(self.path)
        self.db.connect()

    def tearDown(self) -> None:
        self.db.close()
        self.tmpdir.cleanup()

    def test_or_query_hits_memory_with_only_one_token(self) -> None:
        # Under the old space-joined (implicit AND) sanitizer, neither memory
        # would match "deploy pipeline" because neither contains both tokens.
        only_deploy = self.db.insert_memory(
            "bank", "The deploy step failed today.", embedding=[1.0, 0.0]
        )
        only_pipeline = self.db.insert_memory(
            "bank", "Our pipeline runs nightly.", embedding=[1.0, 0.0]
        )

        ids = {r["id"] for r in self.db.fts_search("bank", "deploy pipeline")}
        self.assertIn(only_deploy, ids)
        self.assertIn(only_pipeline, ids)

    def test_fts_only_hit_carries_timestamp_and_entities(self) -> None:
        mid = self.db.insert_memory(
            "bank",
            "The deploy pipeline runs.",
            embedding=[1.0, 0.0],
            entities=["CI", "pipeline"],
            timestamp="2026-03-01T00:00:00+00:00",
        )
        hits = self.db.fts_search("bank", "deploy")
        self.assertEqual([h["id"] for h in hits], [mid])
        self.assertEqual(hits[0]["timestamp"], "2026-03-01T00:00:00+00:00")
        self.assertEqual(hits[0]["entities"], ["CI", "pipeline"])

    def test_short_cjk_like_hit_carries_timestamp_and_entities(self) -> None:
        # The LIKE-rescue path must return the same metadata shape as the
        # BM25 path so recall renders it identically.
        mid = self.db.insert_memory(
            "bank",
            "京都で紅葉を見た。",
            embedding=[1.0, 0.0],
            entities=["京都"],
            timestamp="2026-02-02T00:00:00+00:00",
        )
        hits = self.db.fts_search("bank", "京都")
        self.assertEqual([h["id"] for h in hits], [mid])
        self.assertEqual(hits[0]["timestamp"], "2026-02-02T00:00:00+00:00")
        self.assertEqual(hits[0]["entities"], ["京都"])


class _StubEmbedder:
    async def embed(self, text: str, task_type: str = "") -> list[float]:
        return [1.0, 0.0]


class _TimeFilterDB:
    """Fake DB that returns preset FTS rows and no semantic hits.

    ``search_by_timestamp`` raises: hybrid_search_memories must filter the
    window directly from FTS timestamps, never consult it.
    """

    def __init__(self, fts_rows: list[dict]) -> None:
        self._fts_rows = fts_rows

    def vec_search(self, query_embedding, bank_id, top_k, **kw) -> list[dict]:
        return []

    def get_embeddings_in_range(self, bank_id, time_min=None, time_max=None):
        return []

    def fts_search(self, bank_id, query, limit=20) -> list[dict]:
        return list(self._fts_rows)

    def search_by_timestamp(self, *args, **kwargs):
        raise AssertionError(
            "hybrid_search_memories must not call search_by_timestamp"
        )


class TimeFilterTest(unittest.IsolatedAsyncioTestCase):
    async def test_in_range_old_fts_hits_survive(self) -> None:
        rows = [
            {"id": "recent", "content": "a", "score": -3.0,
             "timestamp": "2026-06-10T00:00:00+00:00", "entities": []},
            {"id": "mid", "content": "b", "score": -2.0,
             "timestamp": "2026-03-15T00:00:00+00:00", "entities": []},
            # In range but the oldest — the old intersect-with-latest-N logic
            # silently dropped this one.
            {"id": "old-in-range", "content": "c", "score": -1.0,
             "timestamp": "2026-01-02T00:00:00+00:00", "entities": []},
            {"id": "before-range", "content": "d", "score": -2.5,
             "timestamp": "2025-01-01T00:00:00+00:00", "entities": []},
            {"id": "after-range", "content": "e", "score": -2.5,
             "timestamp": "2027-01-01T00:00:00+00:00", "entities": []},
        ]
        db = _TimeFilterDB(rows)
        results = await server.hybrid_search_memories(
            db,
            _StubEmbedder(),
            "bank",
            "q",
            limit=10,
            time_min="2026-01-01T00:00:00+00:00",
            time_max="2026-12-31T23:59:59+00:00",
            query_embedding=[1.0, 0.0],
        )
        ids = {r["id"] for r in results}
        self.assertEqual(ids, {"recent", "mid", "old-in-range"})


class _FloorDB:
    def __init__(self, vec_rows: list[dict], fts_rows: list[dict]) -> None:
        self._vec = vec_rows
        self._fts = fts_rows

    def vec_search(self, query_embedding, bank_id, top_k, **kw) -> list[dict]:
        return list(self._vec)

    def fts_search(self, bank_id, query, limit=20) -> list[dict]:
        return list(self._fts)


def _floor_db() -> _FloorDB:
    vec_rows = [
        {"id": "sem-high", "content": "relevant", "similarity": 0.7,
         "timestamp": "2026-05-01T00:00:00+00:00", "entities": []},
        {"id": "sem-low", "content": "unrelated", "similarity": 0.5,
         "timestamp": "2026-05-01T00:00:00+00:00", "entities": []},
    ]
    # An FTS keyword hit whose (irrelevant) similarity is far below the floor;
    # it must survive because the floor applies only to the semantic side.
    fts_rows = [
        {"id": "fts-hit", "content": "keyword match", "score": -1.2,
         "similarity": 0.05, "timestamp": "2026-05-01T00:00:00+00:00",
         "entities": []},
    ]
    return _FloorDB(vec_rows, fts_rows)


class SimilarityFloorTest(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_below_floor_dropped_fts_kept(self) -> None:
        results = await server.hybrid_search_memories(
            _floor_db(), _StubEmbedder(), "bank", "q", limit=10,
            query_embedding=[1.0, 0.0],
        )
        ids = {r["id"] for r in results}
        self.assertIn("sem-high", ids)   # 0.7 >= 0.62 floor
        self.assertNotIn("sem-low", ids)  # 0.5 < 0.62 floor
        self.assertIn("fts-hit", ids)     # keyword hit exempt from the floor

    async def test_floor_env_override_raises_bar(self) -> None:
        # A higher floor drops sem-high too; FTS still exempt.
        with patch.object(server, "RECALL_MIN_SIMILARITY", 0.8):
            results = await server.hybrid_search_memories(
                _floor_db(), _StubEmbedder(), "bank", "q", limit=10,
                query_embedding=[1.0, 0.0],
            )
        ids = {r["id"] for r in results}
        self.assertNotIn("sem-high", ids)
        self.assertNotIn("sem-low", ids)
        self.assertIn("fts-hit", ids)

    def test_min_similarity_read_from_env(self) -> None:
        with patch.dict(os.environ, {"KIROK_RECALL_MIN_SIMILARITY": "0.75"}):
            importlib.reload(server)
            self.assertEqual(server.RECALL_MIN_SIMILARITY, 0.75)
        importlib.reload(server)  # restore module defaults for other tests
        self.assertEqual(server.RECALL_MIN_SIMILARITY, 0.62)


class RrfMergeTest(unittest.TestCase):
    def test_fields_from_both_lists_are_preserved(self) -> None:
        semantic = [{"id": "x", "similarity": 0.9, "timestamp": "t1",
                     "embedding": [1.0, 2.0, 3.0]}]
        fts = [{"id": "x", "score": -1.2, "content": "hello"}]

        merged = reciprocal_rank_fusion(semantic, fts)
        self.assertEqual(len(merged), 1)
        m = merged[0]
        # Fields unique to each list all survive the merge.
        self.assertEqual(m["similarity"], 0.9)
        self.assertEqual(m["timestamp"], "t1")
        self.assertEqual(m["score"], -1.2)
        self.assertEqual(m["content"], "hello")
        self.assertIn("rrf_score", m)

    def test_first_writer_wins_on_shared_key(self) -> None:
        a = [{"id": "y", "content": "from_a"}]
        b = [{"id": "y", "content": "from_b"}]
        merged = reciprocal_rank_fusion(a, b)
        self.assertEqual(merged[0]["content"], "from_a")

    def test_merge_does_not_mutate_input_dicts(self) -> None:
        semantic = [{"id": "x", "similarity": 0.9}]
        fts = [{"id": "x", "score": -1.2}]
        reciprocal_rank_fusion(semantic, fts)
        self.assertNotIn("score", semantic[0])
        self.assertNotIn("similarity", fts[0])


if __name__ == "__main__":
    unittest.main()
