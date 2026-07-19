"""Stage-3 improvements: retain parallelization, recall dedup, embedding
dimension guard, dedup-update audit, API call counters + doctor --online, and
the independent short-token LIKE rescue.

All offline: fakes for embedder/LLM, real MemoryDB on a temp file for the DB
paths. No API key required.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kirok_mcp import server
from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import EMBEDDING_DIM, EmbeddingClient
from kirok_mcp.llm import LLMClient
from kirok_mcp import diagnostics


# ── Improvement 1: retain embed + extract run in parallel ─────────────────

class _ParallelDB:
    def __init__(self):
        self.inserted = []

    def get_bank_config(self, bank_id):
        return {"bank_id": bank_id, "retain_mission": "m", "observations_mission": ""}

    def vec_search(self, emb, bank_id, top_k):
        return []  # no similar memories → straight to ADD

    def insert_memory(self, **kw):
        self.inserted.append(kw)
        return "id-1"

    def count_unconsolidated_memories(self, bank_id):
        return 0  # keeps _maybe_consolidate a no-op


class _ParallelEmbedder:
    def __init__(self):
        self.calls = 0

    async def embed(self, text, task_type=""):
        self.calls += 1
        return [1.0, 0.0]


class _ParallelLLM:
    def __init__(self):
        self.extract_calls = 0

    async def extract_entities(self, text, mission=""):
        self.extract_calls += 1
        return {"entities": ["e"], "keywords": ["k"]}


class RetainParallelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = (server._db, server._llm, server._embedder)

    def tearDown(self):
        server._db, server._llm, server._embedder = self.saved

    async def test_add_runs_embed_and_extract_together(self):
        db, emb, llm = _ParallelDB(), _ParallelEmbedder(), _ParallelLLM()
        server._db, server._embedder, server._llm = db, emb, llm

        result = await server.KIROK_retain(bank_id="b", content="a durable fact")

        self.assertIn("Action: ADD", result)
        # Both sides of the gather actually ran (embed for storage, extract for
        # entities/keywords) and the ADD reused the up-front extraction.
        self.assertEqual(emb.calls, 1)
        self.assertEqual(llm.extract_calls, 1)
        self.assertEqual(len(db.inserted), 1)
        self.assertEqual(db.inserted[0]["entities"], ["e"])


# ── Improvement 2: recall drops memories already inside an observation ─────

class _RecallDedupDB:
    def vec_search(self, emb, bank_id, top_k):
        return [
            {"id": "mem-1", "content": "folded into obs", "embedding": [1.0, 0.0],
             "timestamp": "t", "context": "", "entities": [], "keywords": [],
             "similarity": 0.9},
            {"id": "mem-2", "content": "standalone memory", "embedding": [1.0, 0.0],
             "timestamp": "t", "context": "", "entities": [], "keywords": [],
             "similarity": 0.8},
        ][:top_k]

    def fts_search(self, bank_id, query, limit=20):
        return []

    def vec_search_observations(self, emb, bank_id, top_k):
        return [{"id": "obs-1", "content": "an observation",
                 "timestamp": "t", "source_memory_ids": ["mem-1"],
                 "similarity": 0.9}][:top_k]

    def fts_search_observations(self, bank_id, query, limit=5):
        return []


class _RecallEmbedder:
    async def embed(self, text, task_type=""):
        return [1.0, 0.0]


class RecallDedupTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = (server._db, server._embedder)
        server._db = _RecallDedupDB()
        server._embedder = _RecallEmbedder()

    def tearDown(self):
        server._db, server._embedder = self.saved

    async def test_memory_inside_observation_is_not_repeated_or_counted(self):
        result = await server.KIROK_recall(bank_id="b", query="anything")

        # mem-1 is a source of obs-1, so it must not appear again as a
        # Supporting Memory; mem-2 is independent and stays.
        self.assertNotIn("mem-1", result)
        self.assertIn("mem-2", result)
        self.assertIn("obs-1", result)
        # 1 observation + 1 remaining memory = 2, not 3 (mem-1 was double-counted
        # before the fix).
        self.assertIn("Found 2 relevant items", result)


# ── Improvement 3: embed() rejects wrong-width vectors ────────────────────

class _BadEmbedResult:
    def __init__(self, values):
        self.embeddings = [type("E", (), {"values": values})()]


class _BadAioModels:
    async def embed_content(self, model, contents, config):
        return _BadEmbedResult([0.1, 0.2])  # 2 dims, not EMBEDDING_DIM


class _BadClient:
    def __init__(self):
        self.aio = type("Aio", (), {"models": _BadAioModels()})()


class EmbeddingDimGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_dimension_raises_value_error(self):
        ec = EmbeddingClient(api_key="dummy")
        ec.client = _BadClient()
        with self.assertRaises(ValueError) as cm:
            await ec.embed("x")
        self.assertIn(str(EMBEDDING_DIM), str(cm.exception))


# ── Improvement 4: dedup update audits the pre-merge content ──────────────

class _DedupUpdateLLM:
    def __init__(self, target_id):
        self._target_id = target_id

    async def extract_entities(self, content, mission=""):
        return {"entities": ["e"], "keywords": ["k"]}

    async def deduplicate(self, new_content, similar_memories, mission=""):
        return {
            "action": "update",
            "reason": "enriches existing",
            "target_memory_id": self._target_id,
            "merged_content": "new merged content",
        }


class _DedupUpdateEmbedder:
    async def embed(self, text, task_type=""):
        return [1.0, 0.0]


class DedupUpdateAuditTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = MemoryDB(db_path=Path(self.tmp) / "test.db")
        self.db.connect()
        self.saved = (server._db, server._llm, server._embedder)

    def tearDown(self):
        server._db, server._llm, server._embedder = self.saved
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_old_content_is_audited_but_not_surfaced_as_failure(self):
        mid = self.db.insert_memory(
            "bank-x", "the original content", embedding=[1.0, 0.0]
        )
        server._db = self.db
        server._llm = _DedupUpdateLLM(target_id=mid)
        server._embedder = _DedupUpdateEmbedder()

        result = await server.KIROK_retain(bank_id="bank-x", content="an enriching update")

        self.assertIn("Action: UPDATE", result)
        # The memory was overwritten with the merged content.
        self.assertEqual(self.db.get_memory(mid)["content"], "new merged content")

        # The pre-merge content and id were recorded as an audit event.
        rows = self.db.conn.execute(
            "SELECT detail FROM system_events WHERE event = 'memory_dedup_update'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("the original content", rows[0]["detail"])
        self.assertIn(mid, rows[0]["detail"])

        # It is an audit trail, not a failure — it must never crowd real
        # background failures out of KIROK_stats.
        failures = self.db.get_recent_failures("bank-x")
        self.assertNotIn(
            "memory_dedup_update", [f["event"] for f in failures]
        )


# ── Improvement 5: API call counters + doctor --online flag ───────────────

class _CountingEmbedResult:
    def __init__(self):
        self.embeddings = [
            type("E", (), {"values": [0.0] * EMBEDDING_DIM})()
        ]


class _CountingEmbedAio:
    async def embed_content(self, model, contents, config):
        return _CountingEmbedResult()


class _CountingEmbedClient:
    def __init__(self):
        self.aio = type("Aio", (), {"models": _CountingEmbedAio()})()


class _CountingLLMResponse:
    text = '{"entities": [], "keywords": []}'


class _CountingLLMAio:
    async def generate_content(self, model, contents):
        return _CountingLLMResponse()


class _CountingLLMClient:
    def __init__(self):
        self.aio = type("Aio", (), {"models": _CountingLLMAio()})()


class ApiCounterTest(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_counter_increments(self):
        ec = EmbeddingClient(api_key="dummy")
        ec.client = _CountingEmbedClient()
        self.assertEqual(ec.api_calls, 0)
        await ec.embed("x")
        await ec.embed("y")
        self.assertEqual(ec.api_calls, 2)

    async def test_llm_counter_increments(self):
        llm = LLMClient(api_key="dummy")
        llm.client = _CountingLLMClient()
        self.assertEqual(llm.api_calls, 0)
        await llm.extract_entities("x")
        self.assertEqual(llm.api_calls, 1)


class DoctorOnlineFlagTest(unittest.TestCase):
    def test_online_flag_is_parsed_and_defaults_off(self):
        # Default (no flag): offline check set is unchanged.
        with mock.patch.object(diagnostics, "run_diagnostics", return_value=[]) as m:
            diagnostics.main([])
        self.assertIs(m.call_args.kwargs["online"], False)

        # --online: the flag reaches run_diagnostics as online=True.
        with mock.patch.object(diagnostics, "run_diagnostics", return_value=[]) as m:
            diagnostics.main(["--online"])
        self.assertIs(m.call_args.kwargs["online"], True)

    def test_default_run_has_no_online_check(self):
        # Guard the "default behaviour unchanged" contract without any network:
        # run_diagnostics() must not append the online embedding check.
        results = diagnostics.run_diagnostics()
        self.assertNotIn("online_embedding", [r.name for r in results])


# ── Improvement 6: short-token LIKE rescue runs even when FTS fills limit ──

class ShortTokenRescueIndependenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = MemoryDB(db_path=Path(self.tmp) / "test.db")
        self.db.connect()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_short_hit_survives_a_full_fts_result(self):
        # Three memories match the 3-char FTS token "開発合宿" — enough to fill
        # limit=3 on their own.
        for i in range(3):
            self.db.insert_memory(
                "bank", f"来週の開発合宿の議題その{i}", embedding=[1.0, 0.0]
            )
        # One memory matches ONLY the 2-char short token "京都" (trigram cannot
        # index it; it is served by the LIKE rescue).
        kyoto = self.db.insert_memory(
            "bank", "京都で紅葉を見た。", embedding=[1.0, 0.0]
        )

        ids = [r["id"] for r in self.db.fts_search("bank", "開発合宿 京都", limit=3)]

        # Before the fix the rescue was gated on len(results) < limit, so the
        # full FTS result silenced the short-token hit entirely.
        self.assertIn(kyoto, ids)
        self.assertEqual(len(ids), 4)  # 3 FTS hits + 1 rescued short hit


if __name__ == "__main__":
    unittest.main()
