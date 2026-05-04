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


if __name__ == "__main__":
    unittest.main()
