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
    async def embed(self, text: str) -> list[float]:
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

    def get_observation_embeddings(self, bank_id: str) -> list[dict]:
        return [
            {
                "id": "obs-1",
                "content": "User consistently prefers a dark UI.",
                "embedding": [1.0, 0.0],
                "timestamp": "2026-05-04T00:00:00+00:00",
                "source_memory_ids": ["mem-1"],
            }
        ]


class _FakeRecallEmbedder:
    async def embed(self, text: str) -> list[float]:
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


if __name__ == "__main__":
    unittest.main()
