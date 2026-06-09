import os
import tempfile
import unittest
from pathlib import Path


_tmpdir = tempfile.TemporaryDirectory()
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("KIROK_DB_PATH", str(Path(_tmpdir.name) / "memory.db"))

from kirok_mcp import server  # noqa: E402


class _FakeDB:
    def get_bank_config(self, bank_id: str) -> dict:
        return {
            "bank_id": bank_id,
            "retain_mission": "retain durable project knowledge",
            "observations_mission": "",
        }


class _FakeLLM:
    def __init__(self, evaluation: dict):
        self.evaluation = evaluation
        self.importance_calls = []

    async def evaluate_importance(
        self, content: str, mission: str = "", threshold: int = 5
    ) -> dict:
        self.importance_calls.append(
            {"content": content, "mission": mission, "threshold": threshold}
        )
        return self.evaluation


class _FakeReflectDB:
    def __init__(self):
        self.insert_calls = []

    def get_all_embeddings(self, bank_id: str) -> list[dict]:
        return [
            {
                "id": "memory-1",
                "content": "Deploys use staged rollouts.",
                "embedding": [1.0, 0.0],
                "timestamp": "2026-05-04T00:00:00+00:00",
                "context": "",
                "entities": [],
                "keywords": [],
            }
        ]

    def vec_search(
        self,
        query_embedding: list,
        bank_id: str,
        top_k: int,
        *,
        candidate_multiplier: int = 5,
    ) -> list[dict]:
        # Mirror the real vec_search contract: brute-force candidates plus a
        # cosine 'similarity', trimmed to top_k.
        return [
            {**m, "similarity": 1.0}
            for m in self.get_all_embeddings(bank_id)[:top_k]
        ]

    def get_mental_models(self, bank_id: str, limit: int = 10) -> list[dict]:
        return []

    def insert_mental_model_with_options(self, **kwargs) -> str:
        self.insert_calls.append(kwargs)
        return "model-1"


class _FakeReflectLLM:
    async def reflect(
        self,
        query: str,
        memories: list[dict],
        existing_models: list[dict] | None = None,
    ) -> dict:
        return {
            "topic": "Release Process",
            "insight": "The team prefers staged rollouts.",
        }


class _FakeEmbedder:
    async def embed(self, text: str, task_type: str = "") -> list[float]:
        return [1.0, 0.0]


class SmartRetainTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_db = server._db
        self.original_llm = server._llm
        self.original_retain_memory = server._retain_memory
        server._db = _FakeDB()

    def tearDown(self) -> None:
        server._db = self.original_db
        server._llm = self.original_llm
        server._retain_memory = self.original_retain_memory

    async def test_smart_retain_rejects_below_threshold_without_retaining(self) -> None:
        calls = []

        async def fake_retain_memory(**kwargs):
            calls.append(kwargs)
            return "should not be called"

        fake_llm = _FakeLLM(
            {
                "should_retain": False,
                "score": 3,
                "reason": "Too ephemeral.",
            }
        )
        server._llm = fake_llm
        server._retain_memory = fake_retain_memory

        result = await server.KIROK_smart_retain(
            bank_id="scratch",
            content="temporary note",
            threshold=5,
        )

        self.assertIn("Content not retained", result)
        self.assertEqual(calls, [])
        self.assertEqual(
            fake_llm.importance_calls,
            [
                {
                    "content": "temporary note",
                    "mission": "retain durable project knowledge",
                    "threshold": 5,
                }
            ],
        )

    async def test_smart_retain_uses_shared_retain_pipeline_after_threshold(self) -> None:
        calls = []

        async def fake_retain_memory(**kwargs):
            calls.append(kwargs)
            return "Memory stored successfully.\n\n- Action: ADD"

        server._llm = _FakeLLM(
            {
                "should_retain": True,
                "score": 8,
                "reason": "Durable project knowledge.",
            }
        )
        server._retain_memory = fake_retain_memory

        result = await server.KIROK_smart_retain(
            bank_id="architecture",
            content="Use SQLite FTS5 for keyword search.",
            context="architecture decision",
            timestamp="2026-05-04T00:00:00+00:00",
            threshold=5,
        )

        self.assertIn("Content passed importance filter", result)
        self.assertIn("Memory stored successfully", result)
        self.assertEqual(
            calls,
            [
                {
                    "bank_id": "architecture",
                    "content": "Use SQLite FTS5 for keyword search.",
                    "context": "architecture decision",
                    "timestamp": "2026-05-04T00:00:00+00:00",
                }
            ],
        )


class ReflectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_db = server._db
        self.original_llm = server._llm
        self.original_embedder = server._embedder
        self.fake_db = _FakeReflectDB()
        server._db = self.fake_db
        server._llm = _FakeReflectLLM()
        server._embedder = _FakeEmbedder()

    def tearDown(self) -> None:
        server._db = self.original_db
        server._llm = self.original_llm
        server._embedder = self.original_embedder

    async def test_reflect_persists_auto_refresh_options(self) -> None:
        result = await server.KIROK_reflect(
            bank_id="architecture",
            query="How do releases work?",
            limit=20,
            auto_refresh=True,
            source_query="release process",
        )

        self.assertIn("Auto-refresh: enabled", result)
        self.assertEqual(
            self.fake_db.insert_calls,
            [
                {
                    "bank_id": "architecture",
                    "topic": "Release Process",
                    "insight": "The team prefers staged rollouts.",
                    "based_on": ["memory-1"],
                    "auto_refresh": True,
                    "source_query": "release process",
                }
            ],
        )

    async def test_reflect_defaults_auto_refresh_off_and_uses_query_as_source(self) -> None:
        result = await server.KIROK_reflect(
            bank_id="architecture",
            query="How do releases work?",
        )

        self.assertIn("Auto-refresh: disabled", result)
        self.assertEqual(self.fake_db.insert_calls[0]["auto_refresh"], False)
        self.assertEqual(
            self.fake_db.insert_calls[0]["source_query"],
            "How do releases work?",
        )


class _FakeRecallDB:
    def vec_search(
        self,
        query_embedding: list,
        bank_id: str,
        top_k: int,
        *,
        candidate_multiplier: int = 5,
    ) -> list[dict]:
        return [
            {
                "id": "mem-1",
                "content": "User prefers dark mode.",
                "embedding": [1.0, 0.0],
                "timestamp": "2026-05-04T00:00:00+00:00",
                "context": "",
                "entities": ["dark mode"],
                "keywords": [],
                "similarity": 0.92,
            }
        ][:top_k]

    def fts_search(self, bank_id: str, query: str, limit: int = 20) -> list[dict]:
        return [{"id": "mem-1", "content": "User prefers dark mode.", "score": -1.2}]

    def vec_search_observations(
        self, query_embedding: list, bank_id: str, top_k: int
    ) -> list[dict]:
        return [
            {
                "id": "obs-1",
                "content": "User consistently prefers a dark UI.",
                "timestamp": "2026-05-04T00:00:00+00:00",
                "source_memory_ids": ["mem-1"],
                "similarity": 0.88,
            }
        ][:top_k]


class _FakeRecallEmbedder:
    def __init__(self):
        self.last_task_type = None

    async def embed(self, text: str, task_type: str = "") -> list[float]:
        self.last_task_type = task_type
        return [1.0, 0.0]


class RecallOutputTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_db = server._db
        self.original_embedder = server._embedder
        server._db = _FakeRecallDB()
        server._embedder = _FakeRecallEmbedder()

    def tearDown(self) -> None:
        server._db = self.original_db
        server._embedder = self.original_embedder

    async def test_recall_is_compact_by_default(self) -> None:
        result = await server.KIROK_recall(bank_id="user-prefs", query="dark mode")

        # Content and IDs stay (IDs are needed for follow-up get/update/forget).
        self.assertIn("User prefers dark mode.", result)
        self.assertIn("mem-1", result)
        self.assertIn("obs-1", result)
        # Relevance scores are omitted by default to save context tokens.
        self.assertNotIn("RRF:", result)
        self.assertNotIn("Sim:", result)

    async def test_recall_verbose_includes_scores(self) -> None:
        result = await server.KIROK_recall(
            bank_id="user-prefs", query="dark mode", verbose=True
        )

        self.assertIn("RRF:", result)
        self.assertIn("Sim:", result)
        self.assertIn("mem-1", result)

    async def test_recall_embeds_query_with_query_task_type(self) -> None:
        from kirok_mcp.embeddings import TASK_TYPE_QUERY

        await server.KIROK_recall(bank_id="user-prefs", query="dark mode")
        self.assertEqual(server._embedder.last_task_type, TASK_TYPE_QUERY)


class _FakeStatsDB:
    def __init__(self, failures: list | None = None):
        self._failures = failures or []

    def get_stats(self, bank_id: str) -> dict:
        return {
            "bank_id": bank_id,
            "memory_count": 42,
            "mental_model_count": 3,
            "observations_count": 7,
            "unconsolidated_count": 2,
        }

    def get_bank_config(self, bank_id: str) -> dict:
        return {
            "bank_id": bank_id,
            "retain_mission": "mission",
            "observations_mission": "",
        }

    def get_recent_failures(self, bank_id: str = None, limit: int = 5) -> list:
        return self._failures[:limit]


class StatsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_db = server._db
        server._db = _FakeStatsDB()

    def tearDown(self) -> None:
        server._db = self.original_db

    async def test_stats_uses_count_based_fields(self) -> None:
        # _FakeStatsDB deliberately omits get_observations /
        # get_unconsolidated_memories: KIROK_stats must rely solely on the
        # COUNT-based get_stats fields.
        result = await server.KIROK_stats(bank_id="user-prefs")

        self.assertIn("Memories: 42", result)
        self.assertIn("Mental Models: 3", result)
        self.assertIn("Observations: 7", result)
        self.assertIn("Unconsolidated memories: 2", result)

    async def test_stats_reports_no_failures(self) -> None:
        result = await server.KIROK_stats(bank_id="user-prefs")
        self.assertIn("Background failures: none recorded", result)

    async def test_stats_surfaces_background_failures(self) -> None:
        # WHY: background jobs swallow errors so retain never fails; stats is
        # the one place the user can find out something silently went wrong.
        server._db = _FakeStatsDB(failures=[
            {
                "bank_id": "user-prefs",
                "event": "auto_consolidation_timeout",
                "detail": "timed out after 120s",
                "created_at": "2026-06-10T00:00:00+00:00",
            }
        ])
        result = await server.KIROK_stats(bank_id="user-prefs")

        self.assertIn("Recent background failures", result)
        self.assertIn("auto_consolidation_timeout", result)
        self.assertIn("timed out after 120s", result)
        self.assertNotIn("none recorded", result)


class _FakeUpdateDB:
    def __init__(self, memory=None, update_result=True):
        self._memory = memory
        self._update_result = update_result
        self.update_calls = []

    def get_memory(self, memory_id: str):
        return self._memory

    def get_bank_config(self, bank_id: str) -> dict:
        return {
            "bank_id": bank_id,
            "retain_mission": "MISSION-X",
            "observations_mission": "",
        }

    def update_memory(self, **kwargs) -> bool:
        self.update_calls.append(kwargs)
        return self._update_result


class _FakeUpdateLLM:
    def __init__(self):
        self.extract_calls = []

    async def extract_entities(self, text: str, mission: str = "") -> dict:
        self.extract_calls.append({"text": text, "mission": mission})
        return {"entities": ["E"], "keywords": ["K"]}


class _FakeUpdateEmbedder:
    def __init__(self):
        self.embed_calls = []

    async def embed(self, text: str, task_type: str = "") -> list[float]:
        self.embed_calls.append(text)
        return [1.0, 0.0]


class UpdateMemoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.o_db = server._db
        self.o_llm = server._llm
        self.o_emb = server._embedder

    def tearDown(self) -> None:
        server._db = self.o_db
        server._llm = self.o_llm
        server._embedder = self.o_emb

    async def test_content_update_existing_passes_retain_mission(self) -> None:
        db = _FakeUpdateDB(memory={"id": "m1", "bank_id": "bank-x"})
        llm = _FakeUpdateLLM()
        emb = _FakeUpdateEmbedder()
        server._db, server._llm, server._embedder = db, llm, emb

        result = await server.KIROK_update_memory(memory_id="m1", content="new text")

        self.assertIn("updated successfully", result)
        self.assertEqual(llm.extract_calls, [{"text": "new text", "mission": "MISSION-X"}])
        self.assertEqual(emb.embed_calls, ["new text"])

    async def test_content_update_missing_skips_llm_and_embed(self) -> None:
        db = _FakeUpdateDB(memory=None)
        llm = _FakeUpdateLLM()
        emb = _FakeUpdateEmbedder()
        server._db, server._llm, server._embedder = db, llm, emb

        result = await server.KIROK_update_memory(memory_id="ghost", content="new text")

        self.assertIn("not found", result)
        self.assertEqual(llm.extract_calls, [])
        self.assertEqual(emb.embed_calls, [])
        self.assertEqual(db.update_calls, [])

    async def test_context_only_update_existing(self) -> None:
        db = _FakeUpdateDB(memory={"id": "m1", "bank_id": "bank-x"}, update_result=True)
        llm = _FakeUpdateLLM()
        emb = _FakeUpdateEmbedder()
        server._db, server._llm, server._embedder = db, llm, emb

        result = await server.KIROK_update_memory(memory_id="m1", context="ctx")

        self.assertIn("updated successfully", result)
        # The content-less path must not touch the LLM / embedder.
        self.assertEqual(llm.extract_calls, [])
        self.assertEqual(emb.embed_calls, [])
        self.assertEqual(len(db.update_calls), 1)

    async def test_context_only_update_missing_returns_not_found(self) -> None:
        db = _FakeUpdateDB(memory=None, update_result=False)
        server._db = db
        server._llm = _FakeUpdateLLM()
        server._embedder = _FakeUpdateEmbedder()

        result = await server.KIROK_update_memory(memory_id="ghost", context="ctx")

        self.assertIn("not found", result)


class _FakeDedupDB:
    """DB whose vec_search always returns one highly similar memory."""

    def __init__(self, update_result: bool = True):
        self._update_result = update_result
        self.update_calls = []
        self.insert_calls = []

    def get_bank_config(self, bank_id: str) -> dict:
        return {"bank_id": bank_id, "retain_mission": "", "observations_mission": ""}

    def vec_search(self, query_embedding, bank_id, top_k, **kwargs) -> list[dict]:
        return [{
            "id": "existing-1",
            "content": "User prefers dark mode.",
            "similarity": 0.95,
            "timestamp": "2026-06-01T00:00:00+00:00",
            "context": "",
            "entities": [],
            "keywords": [],
        }]

    def update_memory(self, **kwargs) -> bool:
        self.update_calls.append(kwargs)
        return self._update_result

    def insert_memory(self, **kwargs) -> str:
        self.insert_calls.append(kwargs)
        return "new-1"

    def count_unconsolidated_memories(self, bank_id: str) -> int:
        return 0  # keeps _maybe_consolidate a no-op


class _FakeDedupLLM:
    def __init__(self, dedup_result: dict):
        self._dedup_result = dedup_result
        self.dedup_calls = []

    async def deduplicate(self, new_content, similar_memories, mission="") -> dict:
        self.dedup_calls.append(new_content)
        return self._dedup_result

    async def extract_entities(self, content: str, mission: str = "") -> dict:
        return {"entities": ["e1"], "keywords": ["k1"]}


class RetainDedupTest(unittest.IsolatedAsyncioTestCase):
    """The dedup decision branches of _retain_memory (NOOP / UPDATE / fall-through).

    WHY: these branches decide whether user data is silently dropped (NOOP),
    merged into an existing memory (UPDATE), or stored fresh. A regression here
    loses memories without any error, so each outcome is pinned.
    """

    def setUp(self) -> None:
        self.original = (server._db, server._llm, server._embedder)
        server._embedder = _FakeEmbedder()

    def tearDown(self) -> None:
        server._db, server._llm, server._embedder = self.original

    async def test_noop_skips_storage_and_says_so(self) -> None:
        db = _FakeDedupDB()
        server._db = db
        server._llm = _FakeDedupLLM({"action": "noop", "reason": "exact duplicate"})

        result = await server.KIROK_retain(bank_id="b", content="User prefers dark mode.")

        self.assertIn("NOT stored", result)
        self.assertIn("NOOP", result)
        self.assertIn("existing-1", result)
        self.assertEqual(db.insert_calls, [])
        self.assertEqual(db.update_calls, [])

    async def test_update_merges_into_existing_memory(self) -> None:
        db = _FakeDedupDB(update_result=True)
        server._db = db
        server._llm = _FakeDedupLLM({
            "action": "update",
            "reason": "adds detail",
            "target_memory_id": "existing-1",
            "merged_content": "User prefers dark mode, especially at night.",
        })

        result = await server.KIROK_retain(bank_id="b", content="dark mode at night")

        self.assertIn("UPDATE", result)
        self.assertIn("existing-1", result)
        self.assertEqual(len(db.update_calls), 1)
        self.assertEqual(
            db.update_calls[0]["content"],
            "User prefers dark mode, especially at night.",
        )
        self.assertEqual(db.insert_calls, [])

    async def test_failed_update_falls_through_to_add(self) -> None:
        # If the dedup target vanished (update_memory returns False), the
        # content must still be stored as a new memory — never lost.
        db = _FakeDedupDB(update_result=False)
        server._db = db
        server._llm = _FakeDedupLLM({
            "action": "update",
            "reason": "adds detail",
            "target_memory_id": "ghost",
            "merged_content": "merged",
        })

        result = await server.KIROK_retain(bank_id="b", content="dark mode at night")

        self.assertIn("ADD", result)
        self.assertEqual(len(db.insert_calls), 1)


if __name__ == "__main__":
    unittest.main()
