import unittest
from types import SimpleNamespace

from kirok_mcp.llm import LLMClient


class _FakeModels:
    """Async stand-in for client.aio.models that returns a canned response."""

    def __init__(self, text: str):
        self._text = text

    async def generate_content(self, model: str, contents: str):
        return SimpleNamespace(text=self._text)


class _FakeAio:
    def __init__(self, text: str):
        self.models = _FakeModels(text)


class _FakeClient:
    def __init__(self, text: str):
        self.aio = _FakeAio(text)


def _llm_returning(text: str) -> LLMClient:
    """An LLMClient whose Gemini call is replaced by a canned response."""
    llm = LLMClient(api_key="test-key")
    llm.client = _FakeClient(text)
    return llm


class EvaluateImportanceThresholdTest(unittest.IsolatedAsyncioTestCase):
    """Regression tests for the smart_retain threshold.

    Previously ``should_retain`` was hardcoded to ``score >= 5``, so a caller
    asking for ``threshold=3`` could never retain a score-3 or score-4 item —
    silently dropping low-but-wanted memories (e.g. subtle user preferences).
    The flag must now be computed against the caller's threshold.
    """

    async def test_score_at_or_above_low_threshold_is_retained(self) -> None:
        llm = _llm_returning('{"score": 4, "reason": "a subtle preference"}')

        result = await llm.evaluate_importance("I prefer dark mode", threshold=3)

        self.assertEqual(result["score"], 4)
        self.assertTrue(result["should_retain"])  # 4 >= 3 (was dropped before)

    async def test_score_below_threshold_is_rejected(self) -> None:
        llm = _llm_returning('{"score": 4, "reason": "meh"}')

        result = await llm.evaluate_importance("a note", threshold=5)

        self.assertFalse(result["should_retain"])  # 4 < 5

    async def test_default_threshold_is_five(self) -> None:
        llm = _llm_returning('{"score": 5, "reason": "useful"}')

        result = await llm.evaluate_importance("a note")

        self.assertTrue(result["should_retain"])  # 5 >= default 5

    async def test_failopen_retains_against_caller_threshold(self) -> None:
        # Unparseable response → fail open (retain), staying consistent with the
        # caller's threshold rather than a fixed score of 5.
        llm = _llm_returning("not json at all")

        result = await llm.evaluate_importance("a note", threshold=8)

        self.assertTrue(result["should_retain"])
        self.assertEqual(result["score"], 8)


if __name__ == "__main__":
    unittest.main()
