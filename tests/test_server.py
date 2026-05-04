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

    async def evaluate_importance(self, content: str, mission: str = "") -> dict:
        self.importance_calls.append({"content": content, "mission": mission})
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


if __name__ == "__main__":
    unittest.main()
